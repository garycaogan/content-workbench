"""Platform text helpers: X weighted length, thread splitting, 公众号 HTML."""

from __future__ import annotations

import html
import re

from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore
from markdown_it.token import Token

X_LIMIT = 280
URL_RE = re.compile(r"https?://\S+")
# twitter-text v3: these ranges weigh 1, everything else (CJK, emoji…) weighs 2.
_LIGHT = ((0x0000, 0x10FF), (0x2000, 0x200D), (0x2010, 0x201F), (0x2032, 0x2037))


def x_weighted_length(text: str) -> int:
    total = 0
    pos = 0
    for m in URL_RE.finditer(text):
        total += _weigh(text[pos:m.start()]) + 23
        pos = m.end()
    return total + _weigh(text[pos:])


def _weigh(chunk: str) -> int:
    n = 0
    for ch in chunk:
        cp = ord(ch)
        n += 1 if any(lo <= cp <= hi for lo, hi in _LIGHT) else 2
    return n


def split_thread(body: str) -> list[str]:
    """X drafts separate posts with a line containing only ``---``.

    A leading ``1/`` style counter is kept as the author wrote it."""
    parts = re.split(r"(?m)^\s*---\s*$", body.replace("\r\n", "\n"))
    return [p.strip() for p in parts if p.strip()]


def thread_report(body: str) -> list[dict]:
    out = []
    for i, text in enumerate(split_thread(body), 1):
        n = x_weighted_length(text)
        out.append({"index": i, "text": text, "weighted": n, "over": n > X_LIMIT})
    return out


def _text(content: str) -> Token:
    tok = Token("text", "", 0)
    tok.content = content
    return tok


def _mark_rule(state: StateCore) -> None:
    """``==高亮==`` → mark_open/mark_close. Runs before text_join, so an escaped
    ``\\=`` (a text_special token) never opens a highlight; an unmatched ``==``
    stays literal."""
    for block in state.tokens:
        kids = block.children
        if block.type != "inline" or not kids or not any(t.type == "text" and "==" in t.content for t in kids):
            continue
        out: list[Token] = []
        opens: list[int] = []
        for tok in kids:
            if tok.type != "text" or "==" not in tok.content:
                out.append(tok)
                continue
            for i, part in enumerate(tok.content.split("==")):
                if i:
                    if opens:
                        opens.pop()
                        out.append(Token("mark_close", "mark", -1))
                    else:
                        opens.append(len(out))
                        out.append(Token("mark_open", "mark", 1))
                if part:
                    out.append(_text(part))
        for idx in opens:
            out[idx] = _text("==")
        block.children = out


PUNCT = "。！？，；：、“”‘’「」『』（）《》〈〉【】…—～·.,!?;:'\"()[]<>"


def _fix_pairs(line: str, delim: str) -> str:
    """CommonMark won't close ``**…。**放`` or open ``放**「…`` (punctuation next to
    a delimiter that touches a letter), which is common in Chinese. Move that
    punctuation just outside the pair; it reads the same and parses everywhere."""
    pos, i = [], 0
    while (j := line.find(delim, i)) >= 0:
        pos.append(j)
        i = j + len(delim)
    if len(pos) < 2:
        return line
    n = len(delim)
    out, last = [], 0
    for o, c in zip(pos[0::2], pos[1::2]):
        inner = line[o + n:c]
        before = line[o - 1] if o > 0 else " "
        after = line[c + n] if c + n < len(line) else " "
        lead = trail = ""
        if not before.isspace() and before not in PUNCT:
            k = 0
            while k < len(inner) and inner[k] in PUNCT:
                k += 1
            lead, inner = inner[:k], inner[k:]
        if not after.isspace() and after not in PUNCT:
            k = len(inner)
            while k > 0 and inner[k - 1] in PUNCT:
                k -= 1
            inner, trail = inner[:k], inner[k:]
        if not inner:  # nothing left to mark: keep the original text
            continue
        out.append(line[last:o] + lead + delim + inner + delim + trail)
        last = c + n
    out.append(line[last:])
    return "".join(out)


def normalize_emphasis(markdown: str) -> str:
    lines = markdown.split("\n")
    fence = False
    for k, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fence = not fence
        if fence or "`" in line:
            continue
        for delim in ("**", "=="):
            line = _fix_pairs(line, delim)
        lines[k] = line
    return "\n".join(lines)


def markdown_parser() -> MarkdownIt:
    """CommonMark + tables + ``==高亮==``; shared by the 公众号, X and Feishu renderers."""
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable("table")
    md.core.ruler.before("text_join", "mark", _mark_rule)
    return md


_md = markdown_parser()

_STYLES = {
    "p": "margin:0 0 1.1em;font-size:15px;line-height:1.85;color:#333;letter-spacing:0.5px;",
    "h1": "margin:1.8em 0 0.9em;font-size:19px;font-weight:bold;color:#1a1a1a;line-height:1.5;",
    "h2": "margin:1.8em 0 0.9em;font-size:18px;font-weight:bold;color:#1a1a1a;line-height:1.5;",
    "h3": "margin:1.5em 0 0.7em;font-size:16px;font-weight:bold;color:#1a1a1a;line-height:1.5;",
    "h4": "margin:1.3em 0 0.6em;font-size:15px;font-weight:bold;color:#333;line-height:1.5;",
    "mark": "background:#fff0a6;color:#1a1a1a;padding:0 2px;border-radius:2px;",
    "em": "font-style:italic;",
    "blockquote": "margin:1em 0;padding:0.6em 1em;border-left:3px solid #ccc;color:#666;background:#f7f7f7;",
    "ul": "margin:0 0 1.1em;padding-left:1.4em;font-size:15px;line-height:1.85;color:#333;",
    "ol": "margin:0 0 1.1em;padding-left:1.4em;font-size:15px;line-height:1.85;color:#333;",
    "li": "margin:0.2em 0;",
    "strong": "font-weight:bold;color:#222;",
    "code": "font-family:Menlo,monospace;font-size:13px;background:#f3f3f3;padding:0 3px;",
    "table": "border-collapse:collapse;width:100%;font-size:14px;margin:0 0 1.1em;",
    "th": "border:1px solid #ddd;padding:6px;background:#f5f5f5;",
    "td": "border:1px solid #ddd;padding:6px;",
    "hr": "border:none;border-top:1px solid #e5e5e5;margin:1.6em 0;",
    "img": "display:block;max-width:100%;height:auto;margin:0.4em auto;border-radius:4px;",
}


def wechat_html(markdown: str) -> str:
    """Render Markdown to HTML with inline styles (公众号 drops <style> blocks)."""
    rendered = _md.render(markdown)

    def add_style(match: re.Match) -> str:
        tag, rest = match.group(1), match.group(2) or ""
        style = _STYLES.get(tag)
        if not style or "style=" in rest:
            return match.group(0)
        return f'<{tag}{rest} style="{style}">'

    out = re.sub(r"<(p|h1|h2|h3|h4|blockquote|ul|ol|li|strong|em|code|table|th|td)(\s[^>]*)?>", add_style, rendered)
    # 公众号 keeps span styles more reliably than <mark>
    out = out.replace("<mark>", f'<span style="{_STYLES["mark"]}">').replace("</mark>", "</span>")
    return out.replace("<hr />", f'<hr style="{_STYLES["hr"]}" />').replace("<img ", f'<img style="{_STYLES["img"]}" ')


def strip_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"==(.+?)==", r"\1", text)
    return html.unescape(text)


def digest(markdown: str, limit: int = 110) -> str:
    plain = " ".join(strip_markdown(markdown).split())
    return plain[:limit]
