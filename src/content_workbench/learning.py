"""Learn from how the author edits AI drafts.

Every stretch of the author's editing on top of an AI-written version becomes one
edit round: what the AI wrote, what he turned it into. Rounds are recorded
before an AI revision, on publish, and after half an hour without edits; one
round per AI version, updated as he keeps editing. Every few rounds the model
turns them (plus the comments he wrote) into concrete writing habits, which go
into every later drafting and revision prompt. The author can read, edit, switch off
or add habits in the workbench."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import secrets

from . import db, llm
from .config import _write_private, data_dir

AI_ORIGINS = ("generated", "ai-revise", "ai-draft")
IDLE_SECONDS = 30 * 60
LEARN_EVERY = 3
MAX_LESSONS = 30


def _hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _paragraphs(markdown: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", markdown) if p.strip()]


def changes(before: str, after: str) -> list[dict]:
    """What the author changed, paragraph by paragraph, with the exact phrase edits
    inside rewritten paragraphs."""
    a, b = _paragraphs(before), _paragraphs(after)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        olds, news = a[i1:i2], b[j1:j2]
        for k in range(max(len(olds), len(news))):
            o = olds[k] if k < len(olds) else ""
            n = news[k] if k < len(news) else ""
            item = {"before": o[:1200], "after": n[:1200]}
            if o and n:
                edits = []
                for cop, x1, x2, y1, y2 in difflib.SequenceMatcher(a=o, b=n, autojunk=False).get_opcodes():
                    if cop != "equal":
                        edits.append({"from": o[x1:x2][:200], "to": n[y1:y2][:200]})
                item["edits"] = edits[:20]
                if sum(len(e["from"]) + len(e["to"]) for e in edits) == 0:
                    continue
            item["kind"] = "rewrite" if o and n else ("delete" if o else "insert")
            out.append(item)
    return out


def last_ai_version(draft_id: int) -> dict | None:
    marks = ",".join("?" * len(AI_ORIGINS))
    return db.row(f"SELECT * FROM draft_versions WHERE draft_id=? AND origin IN ({marks}) ORDER BY id DESC LIMIT 1",
                  (draft_id, *AI_ORIGINS))


def record_round(draft_id: int, current: str, trigger: str, title: str | None = None) -> int | None:
    base = last_ai_version(draft_id)
    if not base:
        return None
    if title is None:
        title = (db.row("SELECT title FROM drafts WHERE id=?", (draft_id,)) or {}).get("title") or ""
    found = changes(base["body"], current)
    if (base["title"] or "").strip() != title.strip() and title.strip():
        found.insert(0, {"kind": "title", "before": base["title"] or "", "after": title})
    if not found:
        return None
    current = title + "\n" + current
    stats = {k: sum(1 for c in found if c["kind"] == k) for k in ("title", "rewrite", "delete", "insert")}
    end = _hash(current)
    existing = db.row("SELECT id, end_hash FROM edit_rounds WHERE draft_id=? AND base_version=?", (draft_id, base["id"]))
    with db.tx() as conn:
        if existing:
            if existing["end_hash"] == end:
                return existing["id"]
            conn.execute("""UPDATE edit_rounds SET trigger=?, changes=?, stats=?, end_hash=?, learned=0, updated_at=? WHERE id=?""",
                         (trigger, db.dumps(found), db.dumps(stats), end, db.now(), existing["id"]))
            return existing["id"]
        cur = conn.execute("""INSERT INTO edit_rounds(draft_id, base_version, trigger, changes, stats, end_hash, created_at, updated_at)
                              VALUES (?,?,?,?,?,?,?,?)""",
                           (draft_id, base["id"], trigger, db.dumps(found), db.dumps(stats), end, db.now(), db.now()))
        return cur.lastrowid


def idle_drafts() -> list[dict]:
    """Drafts the author edited after their last AI version and then left for a while."""
    return db.rows("""SELECT d.id, d.body FROM drafts d
                      WHERE d.status = 'draft' AND d.updated_at < ?
                      AND d.updated_at > COALESCE((SELECT MAX(r.updated_at) FROM edit_rounds r WHERE r.draft_id = d.id), 0)
                      AND EXISTS (SELECT 1 FROM draft_versions v WHERE v.draft_id = d.id AND v.origin IN ('generated','ai-revise','ai-draft'))""",
                   (db.now() - IDLE_SECONDS,))


# ------------------------------------------------------------------ habits

def _path():
    return data_dir() / "style_lessons.json"


def load() -> dict:
    path = _path()
    if not path.exists():
        return {"lessons": [], "learned_at": None}
    return json.loads(path.read_text(encoding="utf-8"))


def save(state: dict) -> None:
    _write_private(_path(), json.dumps(state, ensure_ascii=False, indent=1))


def lessons_prompt() -> str:
    active = [l for l in load().get("lessons", []) if l.get("enabled", True)]
    if not active:
        return ""
    lines = []
    for l in active:
        ex = l.get("examples") or []
        sample = f"（他改过：「{ex[0]['before']}」→「{ex[0]['after']}」）" if ex and ex[0].get("before") else ""
        lines.append(f"- {l['rule']}{sample}")
    from .config import Settings
    from .prompts import persona
    name = persona(Settings.load())["name"]
    return (f"<{name} 改稿时的习惯（从他过去对 AI 底稿的真实改动里总结的，写的时候照着做，别再让他改同样的地方）>\n"
            + "\n".join(lines) + f"\n</{name} 改稿时的习惯>")


LEARN = """下面是 {name} 在 AI 写的底稿上亲手改过的地方，以及他给 AI 写过的批注。请从里面总结他的改稿习惯，用来让以后的底稿少让他改。

<现在已有的习惯>
{current}
</现在已有的习惯>

<新的改动（before 是 AI 写的，after 是他改成的；edits 是段落里具体换掉的字；kind 为 title 的是标题）>
{rounds}
</新的改动>

<他写过的批注>
{comments}
</他写过的批注>

要求：
- 每条习惯要具体到能照着做，比如“段落开头不用‘首先’，他会删掉”“数字后面爱补一句括号里的吐槽”，不要写“语言更口语化”这种空话。
- 只总结在改动里真的出现过的；只出现一次、看不出规律的，先不写成习惯。
- 已有的习惯：新证据支持的，evidence 加上次数；和新证据矛盾的，改写或删掉；source 是 gary 的（他自己加的）原样保留。
- 每条带一两个他真实改过的例子（before/after 用原文里的片段，各 60 字以内）。
- 总数不超过 {max_n} 条，按重要程度排。

只输出一个 JSON 对象：
{{"lessons": [{{"id": "已有习惯的 id，新的留空", "rule": "习惯", "examples": [{{"before": "AI 写的", "after": "他改成的"}}], "evidence": 次数}}], "summary": "这次学到了什么，一句话"}}
"""


def pending_rounds() -> list[dict]:
    return db.rows("SELECT * FROM edit_rounds WHERE learned=0 ORDER BY id")


def learn() -> dict:
    rounds = pending_rounds()[:20]
    state = load()
    if not rounds:
        return {"summary": "没有新的改稿记录", "lessons": len(state.get("lessons", []))}
    since = state.get("learned_at") or 0
    comments = db.rows("SELECT body, quote FROM comments WHERE author='gary' AND created_at > ? ORDER BY id LIMIT 60", (since,))
    rounds_text = json.dumps([{"draft": r["draft_id"], "changes": json.loads(r["changes"])[:15]} for r in rounds],
                             ensure_ascii=False)[:60000]
    comments_text = "\n".join(f"- {('针对「' + c['quote'] + '」：') if c['quote'] else ''}{c['body']}" for c in comments) or "（无）"
    current = json.dumps(state.get("lessons", []), ensure_ascii=False)
    from .config import Settings
    from .prompts import persona
    result = llm.extract_json(llm.run(LEARN.format(current=current, rounds=rounds_text, comments=comments_text,
                                                   max_n=MAX_LESSONS, name=persona(Settings.load())["name"]), timeout=900))
    old = {l["id"]: l for l in state.get("lessons", [])}
    merged = []
    for l in result.get("lessons") or []:
        rule = str(l.get("rule", "")).strip()
        if not rule:
            continue
        prev = old.get(str(l.get("id") or ""))
        merged.append({"id": prev["id"] if prev else secrets.token_hex(4), "rule": rule[:300],
                       "examples": [{"before": str(e.get("before", ""))[:120], "after": str(e.get("after", ""))[:120]}
                                    for e in (l.get("examples") or [])[:2] if isinstance(e, dict)],
                       "evidence": int(l.get("evidence") or 1), "source": prev.get("source", "auto") if prev else "auto",
                       "enabled": prev.get("enabled", True) if prev else True})
    kept = {l["id"] for l in merged}
    # the author's own habits and ones he switched off are his call, never dropped by the model
    merged += [l for l in state.get("lessons", []) if l["id"] not in kept and (l.get("source") == "gary" or not l.get("enabled", True))]
    state = {"lessons": merged[:MAX_LESSONS + 10], "learned_at": db.now(), "summary": str(result.get("summary", ""))[:300]}
    save(state)
    with db.tx() as conn:
        conn.executemany("UPDATE edit_rounds SET learned=1 WHERE id=?", [(r["id"],) for r in rounds])
    return {"summary": state["summary"], "lessons": len(merged)}
