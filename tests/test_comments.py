"""Comments like a Feishu doc, AI revision from comments, and learning from Gary's edits."""

import json

from content_workbench import comments, db, learning, llm
from content_workbench.config import Settings

from test_app import client, login
from test_longform import H, new_longform, png, upload


def ai_version(did, body):
    with db.tx() as conn:
        conn.execute("UPDATE drafts SET body=? WHERE id=?", (body, did))
        conn.execute("INSERT INTO draft_versions(draft_id, title, body, created_at, origin) VALUES (?, '长文', ?, ?, 'ai-draft')",
                     (did, body, db.now()))


def test_threads_replies_and_pending():
    c = client()
    login(c)
    did = new_longform(c, "第一段话。\n\n第二段话。")
    root = c.post(f"/api/drafts/{did}/comments", json={"body": "这句太硬", "quote": "第一段话", "suffix": "。"}, headers=H).json()["id"]
    ai = comments.add(did, "ai", "这里要你补一句", quote="第二段话")
    assert [t["id"] for t in comments.pending(did)] == [root]
    c.post(f"/api/comments/{ai}/replies", json={"body": "补：我也是这么想的"}, headers=H)
    assert {t["id"] for t in comments.pending(did)} == {root, ai}
    c.patch(f"/api/comments/{root}", json={"status": "resolved"}, headers=H)
    assert [t["id"] for t in comments.pending(did)] == [ai]
    threads = c.get(f"/api/drafts/{did}/comments").json()
    assert threads[1]["replies"][0]["body"] == "补：我也是这么想的" and threads[0]["status"] == "resolved"
    assert c.post(f"/api/drafts/{did}/comments", json={"body": " "}, headers=H).status_code == 400


def test_placeholders_move_out_of_the_article():
    c = client()
    login(c)
    did = new_longform(c, "剩下的，**交给 AI** 就好。\n\n【这里需要你自己的一句话】\n\n## 七、开源了\n\n正文【确认能不能公开】结束")
    assert comments.lift_placeholders(did) == 2
    body = db.row("SELECT body FROM drafts WHERE id=?", (did,))["body"]
    assert "【" not in body and "\n\n\n" not in body
    notes = comments.threads(did)
    assert [n["body"] for n in notes] == ["这里需要你自己的一句话", "确认能不能公开"]
    assert notes[0]["quote"] == "剩下的，交给 AI 就好" and notes[0]["author"] == "ai"


def test_revise_applies_answers_and_records_the_round(monkeypatch):
    c = client()
    login(c)
    did = new_longform(c)
    pic, = upload(c, did, png())
    ai_version(did, f"开头那段。\n\n![](/api/images/{pic})\n\n结尾那段。")
    c.put(f"/api/drafts/{did}", json={"body": f"开头那段，我改过。\n\n![](/api/images/{pic})\n\n结尾那段。"}, headers=H)
    tid = c.post(f"/api/drafts/{did}/comments", json={"body": "结尾加一句号召", "quote": "结尾那段"}, headers=H).json()["id"]

    def fake(prompt, timeout=600, tools=None, max_turns=2):
        assert "结尾加一句号召" in prompt and "开头那段，我改过" in prompt
        return json.dumps({"body": f"开头那段，我改过。\n\n![](/api/images/{pic})\n\n结尾那段。欢迎评论区聊聊。【补数据】",
                           "replies": [{"thread_id": tid, "reply": "结尾加了一句", "resolved": True}],
                           "new_comments": [{"quote": "开头那段", "text": "这里要不要放数字？"}], "summary": "加了结尾号召"})
    monkeypatch.setattr(llm, "run", fake)
    assert c.post(f"/api/drafts/{did}/revise", headers=H).json() == {"threads": 1}
    assert db.row("SELECT status FROM drafts WHERE id=?", (did,))["status"] == "revising"
    assert c.post(f"/api/drafts/{did}/revise", headers=H).status_code == 409
    info = comments.revise(Settings(), did)
    d = db.row("SELECT * FROM drafts WHERE id=?", (did,))
    assert d["status"] == "draft" and "欢迎评论区聊聊" in d["body"] and "【" not in d["body"]
    assert json.loads(d["last_revision"])["summary"] == "加了结尾号召" and info["answered"] == 1
    t = {x["id"]: x for x in comments.threads(did)}
    assert t[tid]["status"] == "resolved" and t[tid]["replies"][0]["author"] == "ai"
    assert any(x["body"] == "这里要不要放数字？" for x in t.values())
    rounds = db.rows("SELECT * FROM edit_rounds WHERE draft_id=?", (did,))
    assert len(rounds) == 1 and rounds[0]["trigger"] == "before-ai"
    assert json.loads(rounds[0]["changes"])[0]["after"] == "开头那段，我改过。"
    diff = c.get(f"/api/drafts/{did}/diff", params={"a": info["before"]}).json()
    assert any(b["op"] == "change" for b in diff)
    c.post(f"/api/drafts/{did}/undo-revision", headers=H)
    assert "欢迎评论区" not in db.row("SELECT body FROM drafts WHERE id=?", (did,))["body"]


def test_revise_rejected_when_pictures_disappear(monkeypatch):
    c = client()
    login(c)
    did = new_longform(c)
    pic, = upload(c, did, png())
    c.put(f"/api/drafts/{did}", json={"body": f"文字\n\n![](/api/images/{pic})"}, headers=H)
    c.post(f"/api/drafts/{did}/comments", json={"body": "改短一点"}, headers=H)
    monkeypatch.setattr(llm, "run", lambda *a, **k: json.dumps({"body": "文字", "replies": []}))
    try:
        comments.revise(Settings(), did)
    except comments.RevisionRejected as exc:
        assert "图片" in str(exc)
    else:
        raise AssertionError("expected the revision to be refused")
    assert f"/api/images/{pic}" in db.row("SELECT body FROM drafts WHERE id=?", (did,))["body"]


def test_rounds_one_per_ai_version_and_learning(monkeypatch):
    c = client()
    login(c)
    did = new_longform(c)
    ai_version(did, "首先，我们来看一下这个问题。\n\n第二段。")
    assert learning.record_round(did, "首先，我们来看一下这个问题。\n\n第二段。", "idle") is None
    r1 = learning.record_round(did, "这个问题挺有意思。\n\n第二段。", "idle")
    r2 = learning.record_round(did, "这个问题挺有意思的。\n\n第二段。", "publish")
    assert r1 == r2 and len(learning.pending_rounds()) == 1
    learning.save({"lessons": [{"id": "g1", "rule": "署名用 Gary曹淦", "source": "gary", "enabled": True}]})

    def fake(prompt, timeout=600, tools=None, max_turns=2):
        assert "首先，我们来看一下" in prompt and "g1" in prompt
        return json.dumps({"lessons": [{"id": "", "rule": "开头不用“首先，我们来看一下”，直接说事", "evidence": 1,
                                        "examples": [{"before": "首先，我们来看一下这个问题", "after": "这个问题挺有意思的"}]}],
                           "summary": "学到一条"})
    monkeypatch.setattr(llm, "run", fake)
    out = learning.learn()
    assert out["lessons"] == 2 and learning.pending_rounds() == []
    text = learning.lessons_prompt()
    assert "开头不用" in text and "署名用 Gary曹淦" in text and "他改过" in text
    state = c.get("/api/learning").json()
    assert len(state["lessons"]) == 2 and state["rounds"][0]["draft_id"] == did
    lessons = [dict(l, enabled=(l["id"] != "g1")) for l in state["lessons"]]
    c.put("/api/learning/lessons", json={"lessons": lessons + [{"rule": "少用分号"}]}, headers=H)
    text = learning.lessons_prompt()
    assert "少用分号" in text and "署名用 Gary曹淦" not in text


def test_title_change_is_part_of_the_round_and_stale_saves_cannot_restore_notes():
    c = client()
    login(c)
    did = new_longform(c, "正文一句。\n\n【这里你补一句】")
    comments.lift_placeholders(did)
    ai_version(did, db.row("SELECT body FROM drafts WHERE id=?", (did,))["body"])
    c.put(f"/api/drafts/{did}", json={"title": "改过的标题", "body": "正文一句。\n\n【这里你补一句】"}, headers=H)
    assert "【" not in db.row("SELECT body FROM drafts WHERE id=?", (did,))["body"]
    rid = learning.record_round(did, db.row("SELECT body FROM drafts WHERE id=?", (did,))["body"], "manual")
    found = json.loads(db.row("SELECT changes FROM edit_rounds WHERE id=?", (rid,))["changes"])
    assert found[0] == {"kind": "title", "before": "长文", "after": "改过的标题"}
