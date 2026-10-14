"""Archive every draft in one Feishu document, updated in place.

Each draft owns exactly one docx. A sync rewrites that document (Feishu keeps
the revision history, so every earlier version stays visible there); it never
creates a second copy. Pictures go in with ``docs +media-insert``, which only
appends at the end, so the document is rebuilt in order: overwrite with the
first stretch of text, then picture, text, picture… If the document's revision
moved since our last sync, someone edited it in Feishu and the sync stops
instead of overwriting, until the author chooses to overwrite."""

from __future__ import annotations

import html
import json
import tempfile
import threading
from pathlib import Path

from . import db, images
from .config import _write_private, data_dir
from .sources import SourceError, _lark
from .textutil import markdown_parser

FOLDER_NAME = "内容工作台 · 文章"
_md = markdown_parser()
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


class ArchiveConflict(RuntimeError):
    pass


def _lock(draft_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(draft_id, threading.Lock())


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _attr(text: str) -> str:
    return html.escape(text, quote=True)


# ------------------------------------------------------------------ Markdown → Feishu DocxXML

def _inline(children) -> str:
    """Inline tokens → XML. Each text run is wrapped in its own tags in Feishu's
    required order (a → b → em → code → span), so overlapping marks stay valid."""
    out = []
    active = {"strong": 0, "em": 0, "mark": 0}
    link: str | None = None

    def run(text: str, code: bool = False) -> str:
        inner = _esc(text)
        if active["mark"]:
            inner = f'<span background-color="light-yellow">{inner}</span>'
        if code:
            inner = f"<code>{inner}</code>"
        if active["em"]:
            inner = f"<em>{inner}</em>"
        if active["strong"]:
            inner = f"<b>{inner}</b>"
        if link:
            inner = f'<a href="{_attr(link)}">{inner}</a>'
        return inner

    for tok in children or []:
        t = tok.type
        if t == "text":
            out.append(run(tok.content))
        elif t == "code_inline":
            out.append(run(tok.content, code=True))
        elif t in ("softbreak", "hardbreak"):
            out.append("<br/>")
        elif t.endswith("_open") and t[:-5] in active:
            active[t[:-5]] += 1
        elif t.endswith("_close") and t[:-6] in active:
            active[t[:-6]] = max(0, active[t[:-6]] - 1)
        elif t == "link_open":
            link = str(tok.attrGet("href") or "")
        elif t == "link_close":
            link = None
        elif t == "image":
            out.append(_esc(f"[图片：{tok.content}]" if tok.content else "[图片]"))
    return "".join(out)


def segments(markdown: str) -> list[tuple[str, object]]:
    """[("xml", str) | ("image", image_id)] in document order. Pictures that sit
    in their own paragraph (how the editor inserts them) become real images;
    anything else stays text."""
    tokens = _md.parse(markdown.replace("\r\n", "\n"))
    lines = markdown.replace("\r\n", "\n").split("\n")
    segs: list[tuple[str, object]] = []
    buf: list[str] = []
    stack: list[str] = []          # open container tags
    i = 0

    def flush():
        if buf:
            segs.append(("xml", "".join(buf)))
            buf.clear()

    while i < len(tokens):
        tok = tokens[i]
        t = tok.type
        if t == "paragraph_open":
            inline = tokens[i + 1]
            kids = inline.children or []
            pics = [k for k in kids if k.type == "image"]
            hidden = tok.hidden  # tight list item: no <p>
            if pics and not stack:
                part: list = []
                for k in kids:
                    m = images.REF_RE.fullmatch(str(k.attrGet("src") or "")) if k.type == "image" else None
                    if m:
                        text = _inline(part).strip()
                        if text and text != "<br/>":
                            buf.append(f"<p>{text}</p>")
                        flush()
                        segs.append(("image", int(m.group(1))))
                        part = []
                    else:
                        part.append(k)
                text = _inline(part)
                if text.replace("<br/>", "").strip():
                    buf.append(f"<p>{text}</p>")
            else:
                body = _inline(kids)
                buf.append(body if hidden else f"<p>{body}</p>")
            i += 3
            continue
        if t == "heading_open":
            level = min(max(int(tok.tag[1]), 2), 4) if tok.tag != "h1" else 2
            buf.append(f"<h{level}>{_inline(tokens[i + 1].children)}</h{level}>")
            i += 3
            continue
        if t in ("bullet_list_open", "ordered_list_open", "blockquote_open"):
            tag = {"bullet_list_open": "ul", "ordered_list_open": "ol", "blockquote_open": "blockquote"}[t]
            stack.append(tag)
            buf.append(f"<{tag}>")
        elif t in ("bullet_list_close", "ordered_list_close", "blockquote_close"):
            buf.append(f"</{stack.pop()}>")
        elif t == "list_item_open":
            buf.append("<li>")
        elif t == "list_item_close":
            buf.append("</li>")
        elif t == "hr":
            buf.append("<hr/>")
        elif t in ("fence", "code_block"):
            buf.append(f"<pre><code>{_esc(tok.content.rstrip())}</code></pre>")
        elif t == "table_open":
            start, end = tok.map or (0, 0)
            rows = [r.strip().strip("|").split("|") for r in lines[start:end] if r.strip()]
            rows = [r for k, r in enumerate(rows) if k != 1]  # drop the --- separator
            if rows:
                head = "".join(f"<th>{_esc(c.strip())}</th>" for c in rows[0])
                body = "".join("<tr>" + "".join(f"<td>{_esc(c.strip())}</td>" for c in r) + "</tr>" for r in rows[1:])
                buf.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
            while i < len(tokens) and tokens[i].type != "table_close":
                i += 1
        i += 1
    flush()
    return segs


def document_segments(draft: dict) -> list[tuple[str, object]]:
    """The archived document: the article itself, then what went out with it."""
    segs = segments(draft.get("body") or "")
    extra = ["<hr/>", "<h2>发布信息</h2>"]
    if draft.get("summary"):
        extra.append(f"<p><b>摘要：</b>{_esc(draft['summary'])}</p>")
    if draft.get("source_url"):
        extra.append(f'<p><b>阅读原文：</b><a href="{_attr(draft["source_url"])}">{_esc(draft["source_url"])}</a></p>')
    if draft.get("notes"):
        extra.append("<p><b>改稿备注：</b></p>" + "".join(f"<p>{_esc(line)}</p>" for line in draft["notes"].splitlines() if line.strip()))
    note = db.loads(draft.get("xhs"), {}) or {}
    if note.get("title") or note.get("body"):
        extra.append("<h3>小红书版</h3>" + f"<p><b>{_esc(note.get('title', ''))}</b></p>"
                     + "".join(f"<p>{_esc(line)}</p>" for line in (note.get("body") or "").splitlines() if line.strip())
                     + f"<p>{_esc(' '.join('#' + t for t in note.get('tags') or []))}</p>")
    covers = [(label, draft.get(key)) for label, key in (("公众号封面", "cover_wechat"), ("X 长文封面", "cover_x")) if draft.get(key)]
    tail: list[tuple[str, object]] = [("xml", "".join(extra))]
    for label, cid in covers:
        tail += [("xml", f"<p><b>{label}：</b></p>"), ("image", cid)]
    return segs + tail


# ------------------------------------------------------------------ lark-cli

def _data(out: dict) -> dict:
    """sources._lark already unwraps ``data``; accept either shape."""
    return out["data"] if isinstance(out.get("data"), dict) else out


def _state_path() -> Path:
    return data_dir() / "feishu_archive.json"


def _folder() -> str | None:
    path = _state_path()
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if state.get("folder_token"):
        return state["folder_token"]
    try:
        out = _lark(["drive", "+create-folder", "--as", "user", "--name", FOLDER_NAME])
    except SourceError:
        return None  # fall back to the root of 我的空间
    data = _data(out)
    token = data.get("token") or data.get("folder_token") or (data.get("folder") or {}).get("token")
    if token:
        state["folder_token"] = token
        _write_private(path, json.dumps(state, ensure_ascii=False))
    return token


def _revision(doc: str) -> int | None:
    out = _lark(["docs", "+fetch", "--as", "user", "--doc", doc, "--scope", "outline", "--max-depth", "1"])
    return (_data(out).get("document") or {}).get("revision_id")


def _write(args: list[str], content: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "content.xml").write_text(content, encoding="utf-8")
        return _lark(args + ["--content", "@content.xml"], cwd=Path(tmp), timeout=180)


def _insert_image(doc: str, image_id: int) -> None:
    image = images.get(image_id)
    if not image or not images.path_of(image).is_file():
        _write(["docs", "+update", "--as", "user", "--doc", doc, "--command", "append"], "<p>[图片已删除]</p>")
        return
    data, _mime, name = images.encoded(image, max_bytes=19 * 1024 * 1024)
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, name).write_bytes(data)
        _lark(["docs", "+media-insert", "--as", "user", "--doc", doc, "--file", name, "--align", "center"],
              cwd=Path(tmp), timeout=180)


def _set(draft_id: int, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    with db.tx() as conn:
        conn.execute(f"UPDATE drafts SET {cols} WHERE id=?", (*fields.values(), draft_id))


def sync(draft_id: int, *, force: bool = False) -> dict:
    with _lock(draft_id):
        draft = db.row("SELECT * FROM drafts WHERE id=?", (draft_id,))
        if not draft:
            return {}
        version = draft["updated_at"]
        title = (draft["title"] or "").strip() or f"未命名稿件 {draft_id}"
        segs = document_segments(draft)
        doc = draft.get("feishu_doc")
        # a failed or conflicting attempt is not retried until the draft changes again
        _set(draft_id, feishu_state="syncing", feishu_error=None, feishu_tried_version=version)
        try:
            first = segs[0][1] if segs and segs[0][0] == "xml" else "<p></p>"
            rest = segs[1:] if segs and segs[0][0] == "xml" else segs
            if doc:
                current = _revision(doc)
                if not force and draft.get("feishu_rev") and current != draft["feishu_rev"]:
                    raise ArchiveConflict("飞书文档在工作台之外被改过，这次没有覆盖")
                _write(["docs", "+update", "--as", "user", "--doc", doc, "--command", "overwrite"],
                       f"<title>{_esc(title)}</title>{first}")
            else:
                args = ["docs", "+create", "--as", "user", "--title", title]
                folder = _folder()
                if folder:
                    args += ["--parent-token", folder]
                out = _write(args, first)
                created = _data(out).get("document") or _data(out)
                doc = created.get("document_id") or created.get("doc_id") or created.get("token")
                if not doc:
                    raise SourceError(f"飞书没返回文档编号：{json.dumps(out, ensure_ascii=False)[:200]}")
                url = created.get("url") or created.get("doc_url") or f"https://feishu.cn/docx/{doc}"
                _set(draft_id, feishu_doc=doc, feishu_url=url)
            for kind, value in rest:
                if kind == "image":
                    _insert_image(doc, int(value))
                else:
                    _write(["docs", "+update", "--as", "user", "--doc", doc, "--command", "append"], str(value))
            rev = _revision(doc)
        except ArchiveConflict as exc:
            _set(draft_id, feishu_state="conflict", feishu_error=str(exc))
            return {"state": "conflict"}
        except Exception as exc:
            _set(draft_id, feishu_state="failed", feishu_error=str(exc)[:300])
            raise
        _set(draft_id, feishu_state="ok", feishu_error=None, feishu_rev=rev, feishu_synced_version=version,
             feishu_synced_at=db.now())
        return {"state": "ok", "doc": doc, "revision": rev}


def pending(min_idle: float = 90) -> list[int]:
    """Drafts edited since their last archive and left alone for a moment."""
    found = db.rows("""SELECT id FROM drafts
                       WHERE status != 'generating' AND (title != '' OR body != '')
                       AND COALESCE(feishu_state, '') NOT IN ('conflict', 'syncing')
                       AND updated_at > COALESCE(feishu_tried_version, 0) AND updated_at < ?""",
                    (db.now() - min_idle,))
    return [r["id"] for r in found]
