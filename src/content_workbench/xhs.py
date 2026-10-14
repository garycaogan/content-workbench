"""小红书 version of a long-form draft.

There is no publishing API for personal accounts, so the workbench prepares
everything and the author sends it from his phone: an AI rewrite (title ≤20 字,
body ≤1000 字, topic tags), a set of 3:4 cards drawn here (cover + key
points), optionally some of the article's own pictures, then one tap shares
the pictures to the 小红书 app with the text already on the clipboard, where
he saves a draft or publishes."""

from __future__ import annotations

import io
import json
import re
import zipfile

from PIL import Image, ImageDraw, ImageFont

from . import db, images, llm, prompts
from .comments import plain

W, H = 1080, 1440
FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
REGULAR, BOLD = 0, 2
TITLE_MAX, BODY_MAX = 20, 1000


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size, index=BOLD if bold else REGULAR)


NO_LINE_START = set("，。、！？：；）」』》〉】,.!?:;)%")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+%/#-]*|\s+|.", re.S)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    """Wrap by width without splitting Latin words or numbers, and without
    starting a line with closing punctuation."""
    lines, cur = [], ""
    for part in text.split("\n"):
        for tok in TOKEN.findall(part):
            if draw.textlength(cur + tok, font=font) <= width or not cur:
                cur += tok
            elif tok[0] in NO_LINE_START:
                cur += tok  # keep the mark with the previous line
            else:
                lines.append(cur.rstrip())
                cur = tok.lstrip()
        lines.append(cur.rstrip())
        cur = ""
    return [line for line in lines if line] or [""]


def _segments(line: str, hot: str) -> list[tuple[str, bool]]:
    if hot and hot in line:
        a, b = line.split(hot, 1)
        return [(a, False), (hot, True), (b, False)]
    return [(line, False)]


def render_cover(cover: dict, author: str) -> Image.Image:
    img = Image.new("RGB", (W, H), "#0b0f14")
    d = ImageDraw.Draw(img)
    for y in range(H):  # soft green glow towards the bottom right
        t = max(0.0, (y - H * 0.35) / (H * 0.65))
        d.line([(0, y), (W, y)], fill=(11, int(15 + 40 * t * t), int(20 + 30 * t * t)))
    d.text((84, 96), author, font=_font(38), fill="#8b95a1")
    if cover.get("kicker"):
        d.text((84, 170), cover["kicker"], font=_font(52, True), fill="#ffffff", stroke_width=1, stroke_fill="#ffffff")
    y = 330
    big = _font(150, True)
    hot = cover.get("highlight") or ""
    for line in cover.get("lines") or []:
        x = 84
        for text, is_hot in _segments(line, hot):
            if not text:
                continue
            color = "#36f0a8" if is_hot else "#ffffff"
            d.text((x, y), text, font=big, fill=color, stroke_width=3, stroke_fill=color)
            w = d.textlength(text, font=big)
            if is_hot:
                d.rounded_rectangle([x, y + 178, x + w, y + 190], radius=6, fill="#ffd43b")
            x += w
        y += 205
    sub = _font(50)
    for line in _wrap(d, cover.get("subtitle") or "", sub, W - 168)[:3]:
        y += 10
        d.text((84, y + 30), line, font=sub, fill="#c9d1d9")
        y += 70
    x = 84
    for tag in (cover.get("pills") or [])[:3]:
        f = _font(40, True)
        w = d.textlength(tag, font=f) + 56
        d.rounded_rectangle([x, H - 210, x + w, H - 140], radius=35, outline="#36f0a8", width=4)
        d.text((x + 28, H - 196), tag, font=f, fill="#36f0a8")
        x += w + 24
    return img


def render_point(card: dict, index: int, total: int, footer: str) -> Image.Image:
    img = Image.new("RGB", (W, H), "#f6f2ea")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([84, 110, 84 + 150, 110 + 96], radius=22, fill="#e8590c")
    d.text((84 + 75, 110 + 48), f"{index:02d}", font=_font(60, True), fill="#ffffff", anchor="mm")
    d.text((W - 84, 158), f"{index}/{total}", font=_font(36), fill="#9a958c", anchor="rm")
    y = 280
    title = _font(84, True)
    for line in _wrap(d, card.get("title") or "", title, W - 168)[:3]:
        d.text((84, y), line, font=title, fill="#141414", stroke_width=2, stroke_fill="#141414")
        y += 112
    y += 40
    d.line([(84, y), (W - 84, y)], fill="#e2ddd3", width=3)
    room = H - 230 - (y + 64)
    for size in (56, 52, 48, 44, 40):  # shrink until every point fits
        body = _font(size)
        step = int(size * 1.5)
        blocks = [_wrap(d, point, body, W - 168 - 50) for point in card.get("points") or []]
        height = sum(len(b) * step + 48 for b in blocks)
        if height <= room:
            break
    # the points sit in the middle of the space left under the title
    y = max(y + 64, (y + H - 170) // 2 - height // 2)
    for lines in blocks:
        dot = int(step * 0.3)
        d.ellipse([84, y + dot, 84 + 20, y + dot + 20], fill="#e8590c")
        for line in lines:
            if y > H - 230:  # still too long at the smallest size: stop rather than overlap the footer
                break
            d.text((84 + 52, y), line, font=body, fill="#2b2b2b")
            y += step
        y += 48
    d.text((84, H - 120), footer, font=_font(34), fill="#9a958c")
    return img


def _png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def load(draft: dict) -> dict:
    return db.loads(draft.get("xhs"), {}) or {}


def save(draft_id: int, state: dict) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE drafts SET xhs=? WHERE id=?", (json.dumps(state, ensure_ascii=False), draft_id))


def render(draft_id: int, author: str) -> dict:
    """(Re)draw the cards from the stored card text; old card images are replaced."""
    draft = db.row("SELECT * FROM drafts WHERE id=?", (draft_id,))
    state = load(draft)
    for old in state.get("card_images") or []:
        images.delete(old)
    cards = state.get("cards") or []
    footer = f"{author} · {(state.get('title') or draft['title'])[:24]}"
    made = [images.save(draft_id, "小红书-封面.png", _png(render_cover(state.get("cover") or {}, author)))["id"]]
    for n, card in enumerate(cards, 1):
        made.append(images.save(draft_id, f"小红书-{n:02d}.png", _png(render_point(card, n, len(cards), footer)))["id"])
    state["card_images"] = made
    save(draft_id, state)
    return state


XHS = """把下面这篇 {name} 的长文改写成一篇小红书图文笔记，给他自己在手机上发。

<文风指南摘要>
{guide}
</文风指南摘要>

{lessons}

<长文标题>
{title}
</长文标题>

<长文正文>
{body}
</长文正文>

<长文里的图片（编号和它前面那句话）>
{pictures}
</长文里的图片>

要求：
- 标题不超过 20 个字，说人话，点出读者能得到什么或者发生了什么，不要标题党，不编数字。
- 正文不超过 1000 个字：开头一两句交代这是怎么回事，中间几个短段落讲关键内容，结尾落回读者。短段落、多换行，口吻还是 {name} 本人；表情符号最多用 3 个，不用也行。不要加“家人们”“宝子们”这种他不说的话。
- 话题标签 5 到 8 个，不带 #，贴近内容和他的领域（{domain}）。
- 图卡：一张封面，加 3 到 5 张要点卡。封面 lines 是 1 到 3 行大字，每行不超过 7 个字；highlight 是其中最想让人记住的一个词（必须原样出现在某一行里）；kicker 是封面顶上一句不超过 14 个字的引子；subtitle 不超过 30 个字；pills 是 1 到 2 个不超过 5 个字的小标签。要点卡 title 不超过 14 个字，points 2 到 4 条，每条不超过 36 个字。
- 只用长文里有的事实，不编案例、数字和他没说过的观点。
- use_images 从上面的图片里挑 0 到 4 张最能帮读者看懂的，按出现顺序写编号；纯装饰的不要。

只输出一个 JSON 对象：
{{"title": "标题", "body": "正文", "tags": ["标签"], "cover": {{"kicker": "", "lines": ["", ""], "highlight": "", "subtitle": "", "pills": [""]}}, "cards": [{{"title": "", "points": ["", ""]}}], "use_images": [编号]}}
"""


def _pictures(markdown: str) -> str:
    out = []
    for m in re.finditer(r"!\[[^\]]*\]\(/api/images/(\d+)\)", markdown):
        before = plain(markdown[:m.start()]).strip().splitlines()
        out.append(f"- {m.group(1)}：{(before[-1] if before else '')[-60:]}")
    return "\n".join(out) or "（没有图片）"


def generate(settings, draft_id: int) -> dict:
    draft = db.row("SELECT * FROM drafts WHERE id=?", (draft_id,))
    who = prompts.persona(settings)
    prompt = XHS.format(guide=prompts.voice_guide(settings)[:5000], lessons=prompts.style_lessons_text(),
                        title=draft["title"], body=draft["body"][:20000], pictures=_pictures(draft["body"]),
                        name=who["name"], domain=who["domain"])
    result = llm.extract_json(llm.run(prompt, timeout=900))
    allowed = set(images.referenced(draft["body"]))
    state = load(draft)
    state.update({
        "title": str(result.get("title", ""))[:TITLE_MAX],
        "body": str(result.get("body", ""))[:BODY_MAX],
        "tags": [str(t).lstrip("#").strip() for t in (result.get("tags") or []) if str(t).strip()][:10],
        "cover": result.get("cover") or {},
        "cards": [c for c in (result.get("cards") or []) if isinstance(c, dict)][:6],
        "extra_images": [int(i) for i in (result.get("use_images") or []) if str(i).isdigit() and int(i) in allowed][:6],
        "generated_at": db.now(),
    })
    save(draft_id, state)
    return render(draft_id, settings.author)


def caption(state: dict) -> str:
    tags = " ".join(f"#{t}" for t in state.get("tags") or [])
    return f"{state.get('title', '')}\n\n{state.get('body', '')}\n\n{tags}".strip()


def ordered_images(state: dict) -> list[int]:
    return list(state.get("card_images") or []) + list(state.get("extra_images") or [])


def zip_bytes(state: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for n, iid in enumerate(ordered_images(state), 1):
            image = images.get(iid)
            if not image:
                continue
            data, _mime, _name = images.encoded(image, max_bytes=8 * 1024 * 1024, formats=("image/jpeg", "image/png"))
            ext = ".png" if data[:4] == b"\x89PNG" else ".jpg"
            z.writestr(f"{n:02d}{ext}", data)
        z.writestr("文案.txt", caption(state))
    return buf.getvalue()
