"""Formatting (==高亮== and heading levels), the Feishu archive and the 生财 case search."""

import json

from content_workbench import db, feishu, jobs, llm
from content_workbench.textutil import wechat_html
from content_workbench.xarticle import content_state

from test_app import client, login
from test_longform import H, new_longform, png, upload


def test_highlight_and_headings_on_each_platform():
    md = "## 一、开头\n\n### 小节\n\n#### 再小\n\n一句 ==最重要的判断== 和 **加粗==又高亮==**，还有 x == y"
    html = wechat_html(md)
    assert html.count("background:#fff0a6") == 2 and "<mark>" not in html and "x == y" in html
    assert "<h4" in html and "<h3" in html
    state = content_state(md, lambda s: "m")
    assert [b["type"] for b in state["blocks"]] == ["header-one", "header-two", "header-three", "unstyled"]
    styles = {s["style"] for s in state["blocks"][3]["inline_style_ranges"]}
    assert styles == {"bold"}  # X has no highlight


def test_feishu_segments_keep_order_and_valid_nesting():
    md = ("## 一、标题\n\n开头**加粗==高亮==**。\n\n![](/api/images/3)\n\n中间 [链接](https://a.com)\n\n"
          "- 第一\n- 第二\n\n> 引用\n\n![](/api/images/5)\n\n结尾")
    segs = feishu.segments(md)
    assert [k for k, _ in segs] == ["xml", "image", "xml", "image", "xml"]
    assert segs[1][1] == 3 and segs[3][1] == 5
    first = segs[0][1]
    assert "<h2>一、标题</h2>" in first
    assert '<b><span background-color="light-yellow">高亮</span></b>' in first  # span innermost
    assert '<a href="https://a.com">链接</a>' in segs[2][1] and "<ul><li>第一</li>" in segs[2][1]
    assert "<blockquote><p>引用</p></blockquote>" in segs[2][1]


class FakeLark:
    def __init__(self):
        self.calls = []
        self.rev = 10

    def __call__(self, args, cwd=None, timeout=120):
        self.calls.append(args)
        if args[:2] == ["drive", "+create-folder"]:
            return {"token": "fold1"}  # sources._lark returns the unwrapped data
        if args[:2] == ["docs", "+fetch"]:
            return {"document": {"revision_id": self.rev}}
        if args[:2] == ["docs", "+create"]:
            self.rev += 1
            return {"document": {"document_id": "doxNEW", "url": "https://x.feishu.cn/docx/doxNEW"}}
        self.rev += 1
        return {"ok": True, "data": {}}


def test_one_doc_per_draft_updated_in_place_and_conflicts_stop(monkeypatch):
    c = client()
    login(c)
    did = new_longform(c)
    pic, = upload(c, did, png())
    c.put(f"/api/drafts/{did}", json={"body": f"第一段\n\n![](/api/images/{pic})\n\n第二段"}, headers=H)
    lark = FakeLark()
    monkeypatch.setattr(feishu, "_lark", lark)
    assert feishu.sync(did)["state"] == "ok"
    d = db.row("SELECT * FROM drafts WHERE id=?", (did,))
    assert d["feishu_doc"] == "doxNEW" and d["feishu_state"] == "ok" and d["feishu_rev"] == lark.rev
    kinds = [a[1] for a in lark.calls]
    assert kinds.count("+create") == 1 and "+media-insert" in kinds
    assert ["--parent-token", "fold1"] == lark.calls[1][lark.calls[1].index("--parent-token"):][:2]

    lark.calls.clear()
    c.put(f"/api/drafts/{did}", json={"body": "改过了"}, headers=H)
    assert feishu.sync(did)["state"] == "ok"
    kinds = [a[1] for a in lark.calls]
    assert "+create" not in kinds and ["docs", "+update"] == lark.calls[1][:2] and "overwrite" in lark.calls[1]

    lark.rev += 5  # someone edited the doc in Feishu
    c.put(f"/api/drafts/{did}", json={"body": "又改了"}, headers=H)
    lark.calls.clear()
    assert feishu.sync(did)["state"] == "conflict"
    assert not any("overwrite" in a for a in lark.calls)
    assert db.row("SELECT feishu_state FROM drafts WHERE id=?", (did,))["feishu_state"] == "conflict"
    assert did not in feishu.pending(min_idle=-10)
    assert feishu.sync(did, force=True)["state"] == "ok"


def test_pending_waits_for_edits_and_skips_failed_until_changed(monkeypatch):
    c = client()
    login(c)
    did = new_longform(c, "正文")
    assert did in feishu.pending(min_idle=-10) and did not in feishu.pending(min_idle=3600)

    def boom(args, cwd=None, timeout=120):
        raise feishu.SourceError("飞书挂了")
    monkeypatch.setattr(feishu, "_lark", boom)
    try:
        feishu.sync(did)
    except feishu.SourceError:
        pass
    assert db.row("SELECT feishu_state FROM drafts WHERE id=?", (did,))["feishu_state"] == "failed"
    assert did not in feishu.pending(min_idle=-10)
    c.put(f"/api/drafts/{did}", json={"body": "正文改了"}, headers=H)
    assert did in feishu.pending(min_idle=-10)


def test_case_search_job(monkeypatch):
    from content_workbench.config import Settings
    Settings(case_search="scys").save()  # the job reads settings from the data dir
    c = client(case_search="scys")
    login(c)
    did = new_longform(c, "AI 视频起号")
    seen = {}

    def fake_run(prompt, timeout=600, tools=None, max_turns=2):
        seen.update(tools=tools, prompt=prompt)
        return json.dumps({"cases": [{"title": "三个月开通10个YPP", "url": "https://scys.com/x", "summary": "s", "fit": "f"}]})

    monkeypatch.setattr(llm, "run", fake_run)
    r = c.post(f"/api/drafts/{did}/cases", json={"query": "油管长视频起号"}, headers=H)
    sid = r.json()["id"]
    job = db.row("SELECT * FROM jobs WHERE kind='cases'")
    jobs.run_job(None, "cases", json.loads(job["payload"]))
    found = c.get(f"/api/drafts/{did}/cases").json()[0]
    assert found["id"] == sid and found["state"] == "done" and found["results"][0]["url"] == "https://scys.com/x"
    assert "mcp__scys-mcp__searchDeepseaPublicContent" in seen["tools"] and "油管长视频起号" in seen["prompt"]
    assert c.post(f"/api/drafts/{did}/cases", json={"query": " "}, headers=H).status_code == 400


def test_persona_defaults_are_generic_and_configurable():
    from content_workbench import prompts
    from content_workbench.config import Settings
    plain_one = prompts.build_extract(Settings(), {"source": "note", "title": "t", "body": "b"})
    assert "Gary" not in plain_one and "深海圈" not in plain_one and "你在帮 作者从他的工作记录里" in plain_one
    mine = Settings(author="小王", persona={"identity": "独立开发者", "x_style": "我在 X 上爱用短句。"})
    assert "你在帮 小王（独立开发者）从" in prompts.build_extract(mine, {"source": "note", "title": "t", "body": "b"})
    x = prompts.build_draft(mine, "x", {"title": "t", "angle": ""}, None, [])
    assert "我在 X 上爱用短句。" in x and "生财 MCP" not in x
    assert "生财 MCP" in prompts.build_draft(Settings(case_search="scys"), "wechat", {"title": "t", "angle": ""}, None, [])
