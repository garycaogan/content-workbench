"""Comments on a draft, like comments in a Feishu doc.

Notes meant for the author (things to confirm, a sentence only he can write) live
here instead of in the article, so nothing private or unfinished can slip into
a publish. The author comments on a selection or on the whole piece, replies to the
AI's notes, then sends everything at once; the AI revises the article and
answers each thread (``revise``)."""

from __future__ import annotations

import difflib
import json
import re

from . import db, images, llm, prompts
from .textutil import normalize_emphasis

PLACEHOLDER = re.compile(r"【([^】\n]{1,300})】")


def threads(draft_id: int) -> list[dict]:
    rows = db.rows("SELECT * FROM comments WHERE draft_id=? ORDER BY id", (draft_id,))
    roots = {r["id"]: {**r, "replies": []} for r in rows if r["parent_id"] is None}
    for r in rows:
        if r["parent_id"] is not None and r["parent_id"] in roots:
            roots[r["parent_id"]]["replies"].append(r)
    return list(roots.values())


def add(draft_id: int, author: str, body: str, *, quote: str | None = None, prefix: str | None = None,
        suffix: str | None = None, parent_id: int | None = None) -> int:
    with db.tx() as conn:
        cur = conn.execute("""INSERT INTO comments(draft_id, parent_id, author, body, quote, prefix, suffix, created_at)
                              VALUES (?,?,?,?,?,?,?,?)""",
                           (draft_id, parent_id, author, body.strip(), (quote or None), prefix, suffix, db.now()))
        if parent_id is not None:  # a reply reopens a resolved thread
            conn.execute("UPDATE comments SET status='open', resolved_at=NULL WHERE id=?", (parent_id,))
        return cur.lastrowid


def pending(draft_id: int) -> list[dict]:
    """Open threads whose last word is the author's: what the next revision should handle."""
    out = []
    for t in threads(draft_id):
        last = (t["replies"] or [t])[-1]
        if t["status"] == "open" and last["author"] == "gary":
            out.append(t)
    return out


def plain(markdown: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
    text = re.sub(r"(?m)^\s{0,3}(#{1,6}\s+|>\s?|[-+*]\s+|\d+[.)]\s+)", "", text)
    text = re.sub(r"\*\*|==|`", "", text)
    return text


def lift_placeholders(draft_id: int) -> int:
    """Move 【…】 notes out of the article into AI comments anchored on the text
    just before them (or on the whole piece when they open a paragraph)."""
    draft = db.row("SELECT body FROM drafts WHERE id=?", (draft_id,))
    body = draft["body"]
    moved = 0
    for m in list(PLACEHOLDER.finditer(body)):
        before = plain(body[:m.start()]).rstrip()
        tail = re.split(r"[。！？\n]", before.rstrip("。！？"))[-1].strip() if before else ""
        quote = tail[-40:] if tail else None
        add(draft_id, "ai", m.group(1).strip(), quote=quote, prefix=None, suffix=None)
        moved += 1
    if moved:
        cleaned = PLACEHOLDER.sub("", body)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        with db.tx() as conn:
            conn.execute("UPDATE drafts SET body=?, updated_at=? WHERE id=?", (cleaned, db.now(), draft_id))
    return moved


def strip_lifted(draft_id: int, body: str) -> str:
    """A page opened before the notes moved to comments may save them back;
    drop any 【…】 whose text already lives in an AI comment."""
    if "【" not in body:
        return body
    lifted = {r["body"] for r in db.rows("SELECT body FROM comments WHERE draft_id=? AND author='ai'", (draft_id,))}
    cleaned = PLACEHOLDER.sub(lambda m: "" if any(b.startswith(m.group(1).strip()) for b in lifted) else m.group(0), body)
    return re.sub(r"\n{3,}", "\n\n", cleaned) if cleaned != body else body


# ------------------------------------------------------------------ AI revision

class RevisionRejected(RuntimeError):
    pass


def _thread_text(t: dict, name: str = "作者") -> str:
    where = f"批注的原文：「{t['quote']}」" if t.get("quote") else "针对全文"
    lines = [f"[批注 {t['id']}] {where}"]
    for m in [t] + t["replies"]:
        lines.append(f"  {name if m['author'] == 'gary' else 'AI'}：{m['body']}")
    return "\n".join(lines)


def revise(settings, draft_id: int) -> dict:
    draft = db.row("SELECT * FROM drafts WHERE id=?", (draft_id,))
    todo = pending(draft_id)
    if not todo:
        raise RevisionRejected("没有要处理的批注")
    others = [t for t in threads(draft_id) if t["status"] == "open" and t not in todo]
    name = prompts.persona(settings)["name"]
    prompt = prompts.build_revise_comments(settings, draft, "\n\n".join(_thread_text(t, name) for t in todo),
                                           "\n\n".join(_thread_text(t, name) for t in others))
    tools = llm.SCYS_TOOLS if prompts.case_search_on(settings) else None
    reply = llm.run(prompt, timeout=1500, tools=tools, max_turns=12 if tools else 2)
    result = llm.extract_json(reply)
    new_body = str(result.get("body") or "").strip()
    if not new_body:
        raise RevisionRejected("AI 没有返回改好的正文")
    lost = [i for i in images.referenced(draft["body"]) if i not in images.referenced(new_body)]
    asked_to_drop = any("删" in t["body"] or any("删" in r["body"] for r in t["replies"]) for t in todo)
    if lost and not asked_to_drop:
        raise RevisionRejected("AI 改稿时弄丢了正文里的图片，这一轮没有采用，原稿没动，可以再发一次")
    new_body = normalize_emphasis(PLACEHOLDER.sub("", new_body)) + "\n"
    from .learning import record_round
    record_round(draft_id, draft["body"], "before-ai")  # what the author did by hand before handing over
    with db.tx() as conn:
        before = conn.execute("INSERT INTO draft_versions(draft_id, title, body, created_at, origin) VALUES (?,?,?,?, 'before-ai')",
                              (draft_id, draft["title"], draft["body"], db.now())).lastrowid
        title = str(result.get("title") or draft["title"]).strip() or draft["title"]
        after = conn.execute("INSERT INTO draft_versions(draft_id, title, body, created_at, origin) VALUES (?,?,?,?, 'ai-revise')",
                             (draft_id, title, new_body, db.now())).lastrowid
    ids = {t["id"] for t in todo}
    answered = 0
    for r in result.get("replies") or []:
        try:
            tid = int(r.get("thread_id"))
        except (TypeError, ValueError):
            continue
        if tid not in ids or not str(r.get("reply", "")).strip():
            continue
        add(draft_id, "ai", str(r["reply"]), parent_id=tid)
        answered += 1
        if r.get("resolved"):
            with db.tx() as conn:
                conn.execute("UPDATE comments SET status='resolved', resolved_at=? WHERE id=?", (db.now(), tid))
    for c in result.get("new_comments") or []:
        text = str(c.get("text", "")).strip()
        if text:
            add(draft_id, "ai", text, quote=str(c.get("quote") or "").strip()[:80] or None)
    info = {"before": before, "after": after, "summary": str(result.get("summary", ""))[:300], "at": db.now(),
            "threads": len(todo), "answered": answered}
    with db.tx() as conn:
        conn.execute("UPDATE drafts SET title=?, body=?, status='draft', error=NULL, last_revision=?, updated_at=? WHERE id=?",
                     (title, new_body, json.dumps(info, ensure_ascii=False), db.now(), draft_id))
    return info


# ------------------------------------------------------------------ diff

def _paragraphs(markdown: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", markdown) if p.strip()]


def diff(old: str, new: str) -> list[dict]:
    """Paragraph-level diff; changed paragraphs carry a character-level breakdown."""
    a, b = _paragraphs(old), _paragraphs(new)
    out: list[dict] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            out += [{"op": "same", "text": t} for t in a[i1:i2]]
            continue
        olds, news = a[i1:i2], b[j1:j2]
        for k in range(max(len(olds), len(news))):
            o = olds[k] if k < len(olds) else None
            n = news[k] if k < len(news) else None
            if o is not None and n is not None:
                parts = []
                for cop, x1, x2, y1, y2 in difflib.SequenceMatcher(a=o, b=n, autojunk=False).get_opcodes():
                    if cop == "equal":
                        parts.append({"op": "same", "text": o[x1:x2]})
                    else:
                        if x2 > x1:
                            parts.append({"op": "del", "text": o[x1:x2]})
                        if y2 > y1:
                            parts.append({"op": "ins", "text": n[y1:y2]})
                out.append({"op": "change", "parts": parts})
            elif o is not None:
                out.append({"op": "del", "text": o})
            else:
                out.append({"op": "ins", "text": n})
    return out
