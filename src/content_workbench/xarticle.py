"""Markdown → the DraftJS content state that X's Articles API takes.

Mapping: ``##`` → header-one, ``###``/``####`` → header-two (X Articles have two
heading levels; a header-three block makes the draft endpoint answer 503), paragraphs → unstyled,
list items → (un)ordered-list-item, quotes → blockquote, ``**`` / ``==`` /
``*`` → bold / bold / italic ranges (X has no highlight), links and bare URLs → link entities, a picture or a
video → an atomic media block, ``---`` → divider, code and tables → a markdown entity.
Offsets are UTF-16 code units, as in DraftJS.

The Articles API has no video entity: a video goes in the same ``image`` entity
with ``media_category: tweet_video`` (what x.com's own editor stores; a draft
made this way plays in the editor, checked 2026-10-27).

An Article holds at most 25 pictures and videos together: the x.com editor won't
take a 26th, and the API refuses a draft with more with a 400 that only says
"The specified article could not be found" (checked 2026-11-06)."""

from __future__ import annotations

import re
from typing import Callable, Union

from .textutil import markdown_parser

_md = markdown_parser()
URL_RE = re.compile(r"https?://[^\s<>()（），。、【】「」《》“”]+")  # a bare link ends at Chinese punctuation and brackets
MEDIA_RE = re.compile(r"!\[[^\]]*\]\([^)\s]+\)")
MAX_MEDIA = 25
# The body's own headings start at ##; the title is separate.
HEADERS = {"h1": "header-one", "h2": "header-one", "h3": "header-two", "h4": "header-two"}
# resolve(src) → an uploaded X media id (a picture), or (media id, media_category)
Resolver = Callable[[str], Union[str, tuple[str, str]]]


def u16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class _Builder:
    def __init__(self, resolve_media: Resolver):
        self.blocks: list[dict] = []
        self.entities: list[dict] = []
        self.resolve_media = resolve_media

    def entity(self, kind: str, mutability: str, data: dict) -> int:
        self.entities.append({"key": str(len(self.entities)), "value": {"type": kind, "mutability": mutability, "data": data}})
        return len(self.entities) - 1

    def atomic(self, kind: str, mutability: str, data: dict) -> None:
        key = self.entity(kind, mutability, data)
        self.blocks.append({"type": "atomic", "text": " ", "entity_ranges": [{"key": key, "offset": 0, "length": 1}],
                            "inline_style_ranges": []})

    def inline(self, block_type: str, children) -> None:
        text = ""
        styles: list[dict] = []
        ranges: list[dict] = []
        open_styles: dict[str, int] = {}
        link: tuple[int, str] | None = None

        def flush():
            nonlocal text, styles, ranges, open_styles
            body = text.strip("\n")
            if body.strip():
                lead = u16(text) - u16(text.lstrip("\n"))
                self.blocks.append({
                    "type": block_type, "text": body,
                    "inline_style_ranges": [{**s, "offset": s["offset"] - lead} for s in styles if s["length"] > 0],
                    "entity_ranges": [{**r, "offset": r["offset"] - lead} for r in ranges if r["length"] > 0]})
            text, styles, ranges = "", [], []
            open_styles = {k: 0 for k in open_styles}

        for tok in children:
            t = tok.type
            if t == "text" or t == "code_inline":
                start = u16(text)
                text += tok.content
                if link is None:
                    for m in URL_RE.finditer(tok.content):
                        key = self.entity("link", "mutable", {"url": m.group(0)})
                        ranges.append({"key": key, "offset": start + u16(tok.content[:m.start()]), "length": u16(m.group(0))})
            elif t in ("softbreak", "hardbreak"):
                text += "\n"
            elif t in ("strong_open", "em_open", "mark_open"):
                open_styles[t[:-5]] = u16(text)
            elif t in ("strong_close", "em_close", "mark_close"):
                name = t[:-6]
                start = open_styles.pop(name, u16(text))
                # X Articles have no highlight; a highlight goes out as bold
                styles.append({"offset": start, "length": u16(text) - start, "style": "italic" if name == "em" else "bold"})
            elif t == "link_open":
                link = (u16(text), str(tok.attrGet("href") or ""))
            elif t == "link_close" and link:
                start, href = link
                if href:
                    key = self.entity("link", "mutable", {"url": href})
                    ranges.append({"key": key, "offset": start, "length": u16(text) - start})
                link = None
            elif t == "image":
                flush()
                got = self.resolve_media(str(tok.attrGet("src") or ""))
                media_id, category = (got, "tweet_image") if isinstance(got, str) else got
                caption = "".join(c.content for c in (tok.children or []) if c.type == "text").strip()
                data = {"media_items": [{"media_category": category, "media_id": media_id}]}
                if caption:
                    data["caption"] = caption
                self.atomic("image", "immutable", data)
        flush()


def media_count(markdown: str) -> int:
    """Pictures and videos in the body, each time one appears (each is its own media block)."""
    return len(MEDIA_RE.findall(markdown))


def content_state(markdown: str, resolve_media: Resolver) -> dict:
    """``resolve_media(src)`` returns the uploaded X media id for a picture in the
    body, or ``(media id, media_category)`` (a video is ``tweet_video``)."""
    source = markdown.replace("\r\n", "\n")
    lines = source.split("\n")
    tokens = _md.parse(source)
    b = _Builder(resolve_media)
    lists: list[str] = []
    quote = 0
    heading = None
    in_table = False
    for tok in tokens:
        t = tok.type
        if in_table:
            in_table = t != "table_close"
            continue
        if t == "bullet_list_open":
            lists.append("unordered-list-item")
        elif t == "ordered_list_open":
            lists.append("ordered-list-item")
        elif t in ("bullet_list_close", "ordered_list_close"):
            lists.pop()
        elif t == "blockquote_open":
            quote += 1
        elif t == "blockquote_close":
            quote -= 1
        elif t == "heading_open":
            heading = HEADERS.get(tok.tag, "header-two")
        elif t == "heading_close":
            heading = None
        elif t == "inline":
            kind = heading or (lists[-1] if lists else "blockquote" if quote else "unstyled")
            b.inline(kind, tok.children or [])
        elif t == "hr":
            b.atomic("divider", "immutable", {})
        elif t in ("fence", "code_block", "table_open"):
            start, end = tok.map or (0, 0)
            block = "\n".join(lines[start:end]).strip()
            if t == "table_open":  # X has no highlight: bold, as in the rest of the article
                block = re.sub(r"==(?=\S)(.+?)(?<=\S)==", r"**\1**", block)
            b.atomic("markdown", "mutable", {"markdown": block})
            in_table = t == "table_open"  # its cells arrive as inline tokens; skip to table_close
    return {"blocks": b.blocks, "entities": b.entities}
