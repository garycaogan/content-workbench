"""HyperFrames compositions for the short video, written as plain HTML.

Every scene is one sub-composition built here from its data: positions, font
sizes and the moment each element appears are computed in Python, so the page
only runs a paused GSAP timeline (deterministic, seek-safe, no DOM building in
the browser). Cue times come from the aligned voice, which is what makes each
piece land when the narration names it.

Two things vary independently:
  Frame  the canvas: vertical 1080×1920 (抖音 / 视频号 / 小红书) or landscape
         1920×1080 (B 站 / YouTube): where the stage, the caption band and the
         author mark sit. Some scenes lay out differently on a wide canvas
         (a screenshot sits beside its headline instead of under it).
  Style  the look (video_styles.py): colours, fonts, cards, how a key word is
         marked, the caption treatment, backdrop and transitions.
The scene builders are written once against both.

Vertical: the stage is y 232–1236 / x 56–1024; captions own y 1262–1452; the
platform's own title and buttons cover everything below that."""

from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass, replace

from .video_styles import DEFAULT, STYLES, Style, style_for  # noqa: F401  (re-exported for video.py)

GSAP = '<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>'


@dataclass(frozen=True)
class Frame:
    key: str
    W: int
    H: int
    X0: int
    X1: int
    TOP: int
    BOTTOM: int
    CAP_TOP: int
    CAP_H: int
    MARK: tuple[int, int]   # author mark, top-left corner
    CAP_CELLS: float        # how much text one caption phrase holds
    CAP_SIZE: int
    CAP_SIDE: int           # caption margin left and right

    @property
    def SW(self) -> int:
        return self.X1 - self.X0

    @property
    def CX(self) -> int:
        return self.W // 2

    @property
    def wide(self) -> bool:
        return self.W > self.H


# vertical captions sit in y 1250–1450, clear of Douyin's caption text below and its buttons at the right
VERTICAL = Frame("vertical", 1080, 1920, 56, 1024, 232, 1236, 1250, 200, (56, 116), 13.0, 60, 80)
LANDSCAPE = Frame("landscape", 1920, 1080, 120, 1800, 158, 862, 900, 130, (120, 46), 18.0, 54, 160)
HEADLINE_ROOM = 112  # a standing headline (some styles) pushes the vertical stage down this far


def stage_frame(frame: Frame, style: Style) -> Frame:
    """The frame the scenes are laid out in: lower on a vertical canvas when the style keeps a headline on top."""
    return replace(frame, TOP=frame.TOP + HEADLINE_ROOM) if style.headline and not frame.wide else frame
FRAMES = {f.key: f for f in (VERTICAL, LANDSCAPE)}
FORMAT_NAMES = {"vertical": "竖屏 9:16", "landscape": "横屏 16:9"}


def frame_for(key: str | None) -> Frame:
    return FRAMES.get(key or "", VERTICAL)


# ------------------------------------------------------------------ text metrics

def cells(text: str) -> float:
    """Rough width of a line in em: CJK and full-width marks are 1, Latin about half."""
    w = 0.0
    for c in text:
        if c == " ":
            w += 0.3
        elif ord(c) < 0x2E80:
            w += 0.36 if c in ".,:;!'|()[]-/" else 0.6
        else:
            w += 1.0
    return w


def fit(text: str, max_px: int, width: int = 968, min_px: int = 40) -> int:
    em = max(cells(text), 0.5)
    return int(max(min_px, min(max_px, width * 0.96 / em)))


def split_two(text: str) -> list[str]:
    """Break a long line into two near the middle, preferring a mark or a space."""
    mid = len(text) // 2
    best, score = mid, 99
    for i in range(1, len(text)):
        if text[i - 1] in "，、：；,:; " or text[i] == " ":
            d = abs(i - mid)
            if d < score:
                best, score = i, d
    return [text[:best].strip(" ，、"), text[best:].strip()]


def lines_of(title, max_px: int, min_one_line: int, width: int = 968) -> list[str]:
    if isinstance(title, list):
        return [str(t) for t in title if str(t).strip()]
    title = str(title or "").strip()
    if not title:
        return []
    if "\n" in title:
        return [t for t in title.split("\n") if t.strip()]
    if fit(title, max_px, width) >= min_one_line:
        return [title]
    two = split_two(title)
    if width >= 900 or all(fit(t, max_px, width) >= min_one_line * 0.8 for t in two):
        return two
    third = max(1, len(title) // 3)  # a narrow column (landscape headline beside a screenshot)
    return [title[:third], title[third:2 * third], title[2 * third:]]


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def accent_html(text: str, accent: str, t: Style, color: str | None = None) -> str:
    if accent and accent in text:
        a, b = text.split(accent, 1)
        return f'{esc(a)}{t.mark_word(esc(accent), color)}{esc(b)}'
    return esc(text)


# ------------------------------------------------------------------ composition builder

class Comp:
    def __init__(self, sid: str, duration: float, frame: Frame = VERTICAL, style: Style = DEFAULT):
        self.sid, self.dur = sid, round(duration, 3)
        self.f, self.t = frame, style
        self.css: list[str] = []
        self.html: list[str] = []
        self.js: list[str] = []

    def id(self, name: str) -> str:
        return f"{self.sid}-{name}"

    def rule(self, name: str, decl: str) -> None:
        self.css.append(f'[id="{self.id(name)}"] {{ {decl} }}')

    def add(self, markup: str) -> None:
        self.html.append(markup)

    def ref(self, names) -> str:
        if isinstance(names, str):
            return f'$("{names}")'
        return "[" + ", ".join(f'$("{n}")' for n in names) + "]"

    def tw(self, names, frm: dict, to: dict, t: float) -> None:
        self.js.append(f"tl.fromTo({self.ref(names)}, {json.dumps(frm)}, {json.dumps(to)}, {max(0.0, round(t, 3))});")

    def to(self, names, to: dict, t: float) -> None:
        self.js.append(f"tl.to({self.ref(names)}, {json.dumps(to)}, {max(0.0, round(t, 3))});")

    def set(self, names, props: dict, t: float) -> None:
        self.js.append(f"tl.set({self.ref(names)}, {json.dumps(props)}, {max(0.0, round(t, 3))});")

    def rise(self, names, t: float, dy: int = 40, d: float = 0.6) -> None:
        self.tw(names, {"opacity": 0, "y": dy}, {"opacity": 1, "y": 0, "duration": d, "ease": self.t.ease}, t)

    def pop(self, names, t: float, d: float = 0.45) -> None:
        self.tw(names, {"opacity": 0, "scale": 0.86}, {"opacity": 1, "scale": 1, "duration": d, "ease": self.t.pop_ease}, t)

    def build(self) -> str:
        d, f, t = self.dur, self.f, self.t
        drift = (f'      tl.fromTo($("all"), {{ scale: 1 }}, {{ scale: {1 + t.drift}, duration: {d}, ease: "none" }}, 0);\n'
                 if t.drift and d > 3 else "")
        return f"""<template>
  {GSAP}
  <style>
{t.font_faces()}
#root {{ position: absolute; inset: 0; width: {f.W}px; height: {f.H}px; overflow: hidden;
  font-family: {t.font_stack()}; letter-spacing: 0; color: {t.ink}; }}
[id="{self.id('bg')}"] {{ position: absolute; inset: 0; background: {t.bg}; }}
{t.backdrop_css(f, self.id('bg'))}
[id="{self.id('all')}"] {{ transform-origin: 50% 45%; }}
{chr(10).join(self.css)}
  </style>
  <div id="root" data-composition-id="{self.sid}" data-width="{f.W}" data-height="{f.H}" data-duration="{d}">
    <div id="{self.id('bg')}" class="clip" data-start="0" data-duration="{d}" data-track-index="0"></div>
    <div id="{self.id('all')}" style="position:absolute; inset:0;">
{chr(10).join('      ' + h for h in self.html)}
    </div>
  </div>
  <script>
    (function () {{
      const $ = (id) => document.getElementById("{self.sid}-" + id);
      const tl = gsap.timeline({{ paused: true }});
{chr(10).join('      ' + j for j in self.js)}
{drift}      tl.to({{}}, {{ duration: {d} }}, 0);
      window.__timelines["{self.sid}"] = tl;
    }})();
  </script>
</template>
"""


# ------------------------------------------------------------------ shared pieces

def head(c: Comp, s: dict, y: int, t_title: float, max_px: int = 104, x: int | None = None, width: int | None = None) -> int:
    """Eyebrow chip + headline at the top of the stage (or of a column); returns the bottom y."""
    f, t = c.f, c.t
    x = f.X0 if x is None else x
    width = f.SW if width is None else width
    if s.get("eyebrow"):
        c.rule("eyebrow", f"position:absolute; left:{x}px; top:{y}px; height:58px; padding:0 26px; border-radius:{t.chip_radius};"
                          f"{t.eyebrow_css()} font-size:34px; font-weight:600; line-height:58px; white-space:nowrap;")
        c.add(f'<div id="{c.id("eyebrow")}">{esc(s["eyebrow"])}</div>')
        c.rise("eyebrow", max(0.0, t_title - 0.1), dy=20, d=0.45)
        y += 58 + 26
    lines = lines_of(s.get("title"), max_px, 76, width)
    if not lines:
        return y
    size = min(fit(line, max_px, width, min_px=48 if width < 900 else 56) for line in lines)
    lh = int(size * 1.18)
    for i, line in enumerate(lines):
        name = f"title{i}"
        c.rule(name, f"position:absolute; left:{x}px; top:{y + i * lh}px; width:{width}px; font-size:{size}px; line-height:{lh}px;"
                     f"font-weight:{t.title_weight}; white-space:nowrap; {t.title_css()}")
        c.add(f'<div id="{c.id(name)}">{accent_html(line, s.get("accent", ""), t)}</div>')
        c.rise(name, t_title + i * 0.14, dy=36)
    return y + len(lines) * lh


def swap_title(c: Comp, s: dict, y_title: int, bottom: int, t_at: float, old_lines: int, x: int | None = None, width: int | None = None) -> None:
    """Replace the headline in place (blur-out up, blur-in from below)."""
    f, t = c.f, c.t
    x = f.X0 if x is None else x
    width = f.SW if width is None else width
    lines = lines_of(s.get("title2"), 104, 76, width)
    if not lines:
        return
    size = min(fit(line, 104, width, min_px=48 if width < 900 else 56) for line in lines)
    lh = int(size * 1.18)
    for i in range(old_lines):
        c.tw(f"title{i}", {"opacity": 1, "y": 0, "filter": "blur(0px)"},
             {"opacity": 0, "y": -30, "filter": "blur(8px)", "duration": 0.3, "ease": "power2.in"}, t_at - 0.25)
    top = bottom - len(lines) * lh
    for i, line in enumerate(lines):
        name = f"titleb{i}"
        c.rule(name, f"position:absolute; left:{x}px; top:{top + i * lh}px; width:{width}px; font-size:{size}px; line-height:{lh}px;"
                     f"font-weight:{t.title_weight}; white-space:nowrap; {t.title_css()}")
        c.add(f'<div id="{c.id(name)}">{accent_html(line, s.get("accent2") or s.get("accent", ""), t)}</div>')
        c.tw(name, {"opacity": 0, "y": 34, "filter": "blur(8px)"},
             {"opacity": 1, "y": 0, "filter": "blur(0px)", "duration": 0.45, "ease": t.ease}, t_at + 0.05 + i * 0.1)


def pill(c: Comp, name: str, text: str, x: int, y: int, size: int = 40, solid: bool = True, anchor: str = "left") -> None:
    t = c.t
    pos = f"left:{x}px;" if anchor == "left" else (f"right:{c.f.W - x}px;" if anchor == "right" else
                                                    f"left:{x}px; transform-origin:center; translate:-50% 0;")
    style = t.pill_solid_css() if solid else t.pill_ghost_css()
    c.rule(name, f"position:absolute; {pos} top:{y}px; height:{int(size * 1.8)}px; padding:0 {int(size * 0.7)}px; border-radius:{t.chip_radius};"
                 f"{style} font-size:{size}px; font-weight:700; line-height:{int(size * 1.8) - (4 if not solid else 0)}px; white-space:nowrap;")
    c.add(f'<div id="{c.id(name)}">{esc(text)}</div>')


def highlight_word(c: Comp, name: str, t: float) -> None:
    c.tw(name, {"scaleX": 0}, {"scaleX": 1, "duration": 0.5, "ease": "power2.out"}, t)


def card(c: Comp, name: str, x: int, y: int, w: int, h: int, radius: int, extra: str = "") -> None:
    c.rule(name, f"position:absolute; left:{x}px; top:{y}px; width:{w}px; height:{h}px; {c.t.card_css(radius)} {extra}")


# ------------------------------------------------------------------ scene types

def hook(c: Comp, s: dict, cue) -> None:
    """Big words that carry the promise; optional lead line, chips and a closing box."""
    f, t = c.f, c.t
    y = f.TOP + 20
    if s.get("lead"):
        size = fit(s["lead"], 76, f.SW, min_px=52)
        c.rule("lead", f"position:absolute; left:{f.X0}px; top:{y}px; width:{f.SW}px; font-size:{size}px; font-weight:700; white-space:nowrap;"
                       f"{' text-align:center;' if f.wide else ''}")
        c.add(f'<div id="{c.id("lead")}">{accent_html(s["lead"], s.get("lead_accent", ""), t)}</div>')
        t_lead = cue(s.get("lead_cue"), 0.0)
        if t_lead < 0.05:  # the first frame already says something: the lead is there and settles
            c.tw("lead", {"opacity": 1, "y": 30}, {"opacity": 1, "y": 0, "duration": 0.6, "ease": t.ease}, 0.0)
        else:
            c.rise("lead", t_lead, dy=30)
        y += int(size * 1.3)
    if s.get("sub"):
        c.rule("sub", f"position:absolute; left:{f.X0}px; top:{y}px; width:{f.SW}px; font-size:44px; color:{t.muted}; white-space:nowrap;"
                      f"{' text-align:center;' if f.wide else ''}")
        c.add(f'<div id="{c.id("sub")}">{esc(s["sub"])}</div>')
        c.rise("sub", cue(s.get("sub_cue"), 0.4), dy=20, d=0.45)
    words = lines_of(s.get("words"), 280, 140, f.SW) or ["…"]
    size = min(fit(w, 280 if not f.wide else 250, f.SW, min_px=110) for w in words)
    above = bool(s.get("lead") or s.get("sub"))
    if f.wide:  # a short canvas: the big words take what is left under the lead lines
        room_top = (y + 30) if above else f.TOP
        size = min(size, int((f.BOTTOM - 30 - room_top) / (len(words) * 1.08)))
        cy = (room_top + f.BOTTOM - 30) // 2
    else:
        cy = 720 if above else 680
    lh = int(size * 1.08)
    block_h = len(words) * lh
    top = cy - block_h // 2
    c.rule("words", f"position:absolute; left:{f.X0}px; top:{top}px; width:{f.SW}px; text-align:center; transform-origin:50% 50%;")
    inner = "".join(f'<div id="{c.id(f"w{i}")}" style="font-size:{size}px; line-height:{lh}px; font-weight:{t.big_weight}; white-space:nowrap; {t.big_css()}">'
                    f'{accent_html(w, s.get("accent", ""), t)}</div>' for i, w in enumerate(words))
    c.add(f'<div id="{c.id("words")}">{inner}</div>')
    t_words = cue(s.get("words_cue"), 0.0 if not above else 1.2)
    for i in range(len(words)):
        frm = t.big_in()
        # the opening claim is already there on the first frame and settles; words that wait for their cue stay hidden till then
        frm["opacity"] = 1 if t_words + i * 0.32 < 0.05 else 0
        c.tw(f"w{i}", frm, {**t.big_to(), "duration": 0.5, "ease": t.ease}, t_words + i * 0.32)
    chips = [x for x in (s.get("chips") or []) if str(x.get("text", "")).strip()][:4]
    if not chips:
        return
    t_first = min(cue(ch.get("cue"), t_words + 1.0 + 0.6 * i) for i, ch in enumerate(chips))
    t_first = max(t_first, t_words + 0.6)
    # the big words move up and shrink to make room for the chips
    shrink = 0.5 if not f.wide else 0.62
    new_cy = f.TOP + 60 + int(block_h * shrink / 2)
    above_names = [n for n in ("lead", "sub") if s.get(n)]
    if above_names:
        c.to(above_names, {"opacity": 0, "y": -30, "duration": 0.35, "ease": "power2.in"}, t_first - 0.45)
    c.to("words", {"y": new_cy - cy, "scale": shrink, "duration": 0.6, "ease": "power3.inOut"}, t_first - 0.35)
    grid_top = new_cy + int(block_h * shrink / 2) + 70
    ch_h, gap = 128, 24
    if f.wide:  # one row across the canvas
        per_row, gx = len(chips), 32
        cw = min(452, (f.SW - (per_row - 1) * gx) // per_row)
        left = f.CX - (per_row * cw + (per_row - 1) * gx) // 2
    else:
        per_row, gx, cw = 2, 40, 452
        left = f.X0 + (f.SW - (2 * cw + gx)) // 2
    for i, chip in enumerate(chips):
        col, row = i % per_row, i // per_row
        x = left + col * (cw + gx) if len(chips) > 1 else f.CX - cw // 2
        y = grid_top + row * (ch_h + gap)
        name = f"chip{i}"
        sub = f'<div style="font-size:32px; color:{t.muted}; font-weight:400; margin-top:4px;">{esc(chip.get("sub"))}</div>' if chip.get("sub") else ""
        card(c, name, x, y, cw, ch_h, 22, f"display:flex; flex-direction:column; justify-content:center; padding:0 34px;"
             f"font-size:{fit(str(chip['text']), 48, cw - 70, 34)}px; font-weight:700; white-space:nowrap;")
        c.add(f'<div id="{c.id(name)}">{esc(chip["text"])}{sub}</div>')
        c.rise(name, max(t_first, cue(chip.get("cue"), t_first + 0.55 * i)), dy=30, d=0.45)
    if s.get("box"):
        rows = (len(chips) + per_row - 1) // per_row
        bh = rows * (ch_h + gap) - gap + 80
        by = grid_top - 40
        bx, bw = (f.X0 - 12, f.SW + 24) if not f.wide else (left - 40, per_row * cw + (per_row - 1) * gx + 80)
        c.rule("box", f"position:absolute; left:{bx}px; top:{by}px; width:{bw}px; height:{bh}px; border-radius:30px; {t.box_css()}")
        c.add(f'<div id="{c.id("box")}"></div>')
        pill(c, "boxlabel", s["box"], f.CX, by - 34, size=40, solid=True, anchor="center")
        t_box = cue(s.get("box_cue"), max(t_first + 1.2, c.dur - 1.6))
        c.tw("box", {"opacity": 0, "scale": 1.06}, {"opacity": 1, "scale": 1, "duration": 0.5, "ease": t.ease}, t_box)
        c.pop("boxlabel", t_box + 0.15)


def points(c: Comp, s: dict, cue) -> None:
    """Headline plus 1–4 cards that arrive one by one as the voice names them."""
    f, t = c.f, c.t
    bottom = head(c, s, f.TOP + 8, cue(s.get("title_cue"), 0.0))
    items = [x for x in (s.get("items") or []) if str(x.get("text", "")).strip()][:4]
    n = len(items)
    if not n:
        return
    gap = 26
    footer_size = fit(s["footer"], 84, f.SW, min_px=52) if s.get("footer") else 0
    footer_h = int(footer_size * 1.3) + 30 if footer_size else 0
    room = f.BOTTOM - bottom - 40 - footer_h
    cols = 2 if f.wide and n >= 3 else 1
    rows = math.ceil(n / cols)
    width = f.SW if not f.wide else (f.SW - gap) // 2 if cols == 2 else min(f.SW, 1300)
    x0 = f.X0 if not f.wide or cols == 2 else f.CX - width // 2
    tall = (156, 124) if not f.wide else (176, 148)
    card_h = min(tall[0] if any(i.get("sub") for i in items) else tall[1], (room - (rows - 1) * gap) // rows)
    show_sub = card_h >= 140
    text_px = 52 if not f.wide else 58
    total = rows * card_h + (rows - 1) * gap
    y0 = bottom + 40 + max(0, (room - total) // 2)
    for i, item in enumerate(items):
        name = f"item{i}"
        text = str(item["text"])
        x = x0 + (i % cols) * (width + gap)
        y = y0 + (i // cols) * (card_h + gap)
        sub = f'<div style="font-size:36px; color:{t.muted}; font-weight:400; margin-top:6px; white-space:nowrap;">{esc(item.get("sub"))}</div>' if item.get("sub") and show_sub else ""
        card(c, name, x, y, width, card_h, 24, "display:flex; align-items:center; gap:30px; padding:0 40px;")
        mark = esc(item["icon"]) if item.get("icon") else f"{i + 1:02d}"
        badge = (f'<div style="flex:0 0 auto; width:66px; height:66px; border-radius:50%; {t.number_css()}'
                 f'font-size:{36 if item.get("icon") else 30}px; font-weight:900; line-height:66px; text-align:center;">{mark}</div>')
        c.add(f'<div id="{c.id(name)}">{badge}<div><div style="font-size:{fit(text, text_px, width - 200, 36)}px; font-weight:700; white-space:nowrap;">'
              f'{accent_html(text, item.get("accent", ""), t)}</div>{sub}</div></div>')
        default = 0.8 + (c.dur - 2.2) * i / max(1, n)
        c.tw(name, {"opacity": 0, "x": 70}, {"opacity": 1, "x": 0, "duration": 0.55, "ease": t.ease}, cue(item.get("cue"), default))
    y = y0 + total + gap
    if s.get("footer"):
        size = footer_size
        c.rule("footer", f"position:absolute; left:{f.X0}px; top:{min(y + 4, f.BOTTOM - int(size * 1.3))}px; width:{f.SW}px; text-align:center;"
                         f"font-size:{size}px; font-weight:{t.big_weight}; color:{t.primary}; white-space:nowrap; {t.big_css()}")
        c.add(f'<div id="{c.id("footer")}">{esc(s["footer"])}</div>')
        c.pop("footer", cue(s.get("footer_cue"), c.dur - 1.6))


def _window_geometry(iw: int, ih: int, x0: int, max_w: int, top: int, bottom: int, centre: bool = False) -> tuple[int, int, int, int, float]:
    scale = min(max_w / iw, (bottom - top) / ih)
    ww, wh = int(iw * scale), int(ih * scale)
    spare = (bottom - top) - wh
    return x0 + (max_w - ww) // 2, top + max(0, spare // 2 if centre else spare // 4), ww, wh, scale


def screen(c: Comp, img: dict, x0: int, max_w: int, top: int, bottom: int, centre: bool = False) -> tuple[int, int, int, int, float]:
    """A screenshot in its frame: a browser window or a plain card (the style's
    choice), a phone for tall ones. Returns the picture's own box (inside the
    frame) and its scale; the frame element is "win", the picture "img"."""
    t = c.t
    iw, ih = int(img["w"]), int(img["h"])
    kind = "phone" if ih > iw * 1.3 else t.frame_kind
    bar = 44 if kind == "browser" else 0
    pad = 16 if kind == "phone" else 0
    wx, wy, ww, wh, s0 = _window_geometry(iw, ih, x0 + pad, max_w - 2 * pad, top + bar + pad, bottom - pad, centre)
    fx, fy, fw, fh = wx - pad, wy - bar - pad, ww + 2 * pad, wh + bar + 2 * pad
    radius = 40 if kind == "phone" else 0
    c.rule("win", f"position:absolute; left:{fx}px; top:{fy}px; width:{fw}px; height:{fh}px; {t.frame_css(kind)}")
    c.rule("view", f"position:absolute; left:{pad}px; top:{bar + pad}px; width:{ww}px; height:{wh}px; overflow:hidden; border-radius:{radius}px; background:#fff;")
    c.rule("img", "position:absolute; left:0; top:0; width:%dpx; height:%dpx; transform-origin:0 0; max-width:none;" % (iw, ih))
    chrome = t.frame_bar_html(fw, bar) if bar else ""
    c.add(f'<div id="{c.id("win")}">{chrome}<div id="{c.id("view")}"><img id="{c.id("img")}" src="{esc(img["src"])}" alt="" /></div></div>')
    c.set("img", {"scale": s0, "x": 0, "y": 0}, 0)
    return wx, wy, ww, wh, s0


def shot(c: Comp, s: dict, cue) -> None:
    """A real screenshot in a window card; the camera pushes into one region on its cue.
    On a wide canvas the headline sits in a column beside the screenshot."""
    f, t = c.f, c.t
    t_title = cue(s.get("title_cue"), 0.0)
    y_title = f.TOP + 8
    col = 560 if f.wide else f.SW
    if f.wide:
        y_title = f.TOP + 40
    bottom = head(c, s, y_title, t_title, max_px=104 if not f.wide else 84, width=col)
    img = s.get("image") or {}
    iw, ih = int(img.get("w") or 0), int(img.get("h") or 0)
    if not (iw and ih and img.get("src")):
        return
    label_room = 110 if s.get("label") else 20
    if f.wide:
        wx, wy, ww, wh, s0 = screen(c, img, f.X0 + col + 60, f.SW - col - 60, f.TOP + 10, f.BOTTOM - 10, centre=True)
    else:
        wx, wy, ww, wh, s0 = screen(c, img, f.X0, f.SW, bottom + 44, f.BOTTOM - label_room)
    c.tw("win", {"opacity": 0, "y": 80, "rotateX": 8}, {"opacity": 1, "y": 0, "rotateX": 0, "duration": 0.65, "ease": t.ease}, max(0.0, t_title + 0.15))
    focus = s.get("focus")
    if focus and len(focus) == 4:
        fx0, fy0, fx1, fy1 = [min(1.0, max(0.0, float(v))) for v in focus]
        if fx1 - fx0 > 0.02 and fy1 - fy0 > 0.02:
            rw, rh = (fx1 - fx0) * iw, (fy1 - fy0) * ih
            cap = min(s0 * 3.2, 0.9 if iw >= 2000 else 1.25)
            s1 = max(s0, min(ww * 0.88 / rw, wh * 0.84 / rh, cap))
            cx, cy = (fx0 + fx1) / 2 * iw, (fy0 + fy1) / 2 * ih
            tx = min(0.0, max(ww - iw * s1, ww / 2 - cx * s1))
            ty = min(0.0, max(wh - ih * s1, wh / 2 - cy * s1))
            t_focus = cue(s.get("focus_cue"), min(c.dur * 0.4, 2.2))
            c.to("img", {"scale": round(s1, 4), "x": round(tx, 1), "y": round(ty, 1), "duration": 0.95, "ease": "power3.inOut"}, t_focus)
            # a box around the focused region, in picture coordinates after the push
            rx, ry = fx0 * iw * s1 + tx, fy0 * ih * s1 + ty
            rw2, rh2 = rw * s1, rh * s1
            pad = 10
            rx, ry = max(6, rx - pad), max(6, ry - pad)
            rw2, rh2 = min(ww - rx - 6, rw2 + 2 * pad), min(wh - ry - 6, rh2 + 2 * pad)
            if s.get("ring", True) and rw2 > 40 and rh2 > 30:
                c.rule("ring", f"position:absolute; left:{wx + rx:.0f}px; top:{wy + ry:.0f}px; width:{rw2:.0f}px; height:{rh2:.0f}px;"
                               f"{t.focus_css()}")
                c.add(f'<div id="{c.id("ring")}"></div>')
                c.tw("ring", {"opacity": 0, "scale": 1.06}, {"opacity": 1, "scale": 1, "duration": 0.4, "ease": t.ease}, t_focus + 0.85)
    for i, b in enumerate([b for b in (s.get("badges") or []) if str(b.get("text", "")).strip()][:2]):
        name = f"badge{i}"
        if f.wide:  # stacked under the headline
            pill(c, name, b["text"], f.X0, bottom + 40 + i * 96, size=40)
        else:
            pill(c, name, b["text"], wx + ww - 10, wy - 30 + i * 96, size=40, anchor="right")
        c.pop(name, cue(b.get("cue"), 1.0 + i * 0.8))
    if s.get("label"):
        if f.wide:
            pill(c, "label", s["label"], f.X0, f.BOTTOM - 80, size=36, solid=True)
        else:
            pill(c, "label", s["label"], f.CX, wy + wh + 28, size=40, solid=True, anchor="center")
        c.pop("label", cue(s.get("label_cue"), c.dur - 1.8))
    if s.get("title2"):
        lines_before = len(lines_of(s.get("title"), 104 if not f.wide else 84, 76, col))
        swap_title(c, s, y_title, bottom, cue(s.get("title2_cue"), c.dur * 0.5), lines_before, width=col)


def clip_fill(fill: str | None, w: int, h: int, frame: Frame) -> str:
    """Where a clip goes: over the whole canvas when its shape is near the canvas's
    (a vertical video in the vertical cut), otherwise in a frame like a screenshot."""
    if fill in ("full", "window"):
        return fill
    ratio = (w / h) / (frame.W / frame.H) if w and h else 0
    return "full" if 0.8 <= ratio <= 1.25 else "window"


def _video_tag(c: Comp, src: str) -> str:
    """The clip itself. HyperFrames owns its playback (it starts with the scene and
    is seeked with it); the editor's preview drives it from the voice's clock."""
    return (f'<video id="{c.id("video")}" class="clip" src="{esc(src)}" muted playsinline preload="auto"'
            f' data-start="0" data-duration="{c.dur}" data-track-index="1"></video>')


def _source(c: Comp, s: dict, x: int, y: int) -> str:
    """The small source line in a corner of the footage."""
    if not s.get("source"):
        return ""
    c.rule("source", f"position:absolute; left:{x}px; top:{y}px; font-size:28px; font-weight:700; color:rgba(255,255,255,0.88);"
                     f" white-space:nowrap; text-shadow:0 1px 3px rgba(0,0,0,0.9), 0 0 14px rgba(0,0,0,0.6);")
    return f'<div id="{c.id("source")}">{esc(s["source"])}</div>'


def _over_title(c: Comp, s: dict, y: int, t_at: float, max_px: int) -> int:
    """The headline laid over footage; on the first frame already when it is said at once. Returns the bottom y."""
    f, t = c.f, c.t
    lines = lines_of(s.get("title"), max_px, 80, f.SW)
    size = min((fit(line, max_px, f.SW, min_px=56) for line in lines), default=0)
    lh = int(size * 1.16)
    accent = s.get("accent", "")
    for i, line in enumerate(lines):
        name = f"title{i}"
        c.rule(name, f"position:absolute; left:{f.X0}px; top:{y + i * lh}px; width:{f.SW}px; text-align:center; font-size:{size}px;"
                     f" line-height:{lh}px; font-weight:{t.title_weight}; white-space:nowrap; {t.overlay_css()}")
        body = esc(line)
        if accent and accent in line:
            a, b = line.split(accent, 1)
            body = f'{esc(a)}<span style="color:{t.cap_key}">{esc(accent)}</span>{esc(b)}'
        c.add(f'<div id="{c.id(name)}">{body}</div>')
        if t_at < 0.05:
            c.tw(name, {"opacity": 1, "y": 24}, {"opacity": 1, "y": 0, "duration": 0.6, "ease": t.ease}, 0.0)
        else:
            c.rise(name, t_at + i * 0.14, dy=36)
    return y + len(lines) * lh


def clip(c: Comp, s: dict, cue) -> None:
    """A stretch of a video from the article, playing while the line is said (its
    last frame held when the line runs longer). Over the whole canvas, under an
    outlined headline, when it has the canvas's shape; otherwise in a phone or card
    frame under the headline (beside it on a wide canvas), like a screenshot."""
    f, t = c.f, c.t
    v = s.get("clip") or {}
    if not v.get("src"):
        points(c, s, cue)
        return
    t_title = cue(s.get("title_cue"), 0.0)
    badges = [b for b in (s.get("badges") or []) if str(b.get("text", "")).strip()][:2]
    if v.get("fill") == "full":
        c.rule("video", f"position:absolute; left:0; top:0; width:{f.W}px; height:{f.H}px; object-fit:cover; background:#000;")
        # darker at the top (headline, author mark) and at the bottom (captions, the platform's own text)
        c.rule("scrim", "position:absolute; inset:0; background:linear-gradient(180deg, rgba(0,0,0,0.55) 0%, rgba(0,0,0,0) 26%,"
                        " rgba(0,0,0,0) 54%, rgba(0,0,0,0.6) 74%, rgba(0,0,0,0.72) 100%);")
        c.add(_video_tag(c, v["src"]))
        c.add(f'<div id="{c.id("scrim")}"></div>')
        y = f.TOP + 8
        if s.get("eyebrow"):
            pill(c, "eyebrow", s["eyebrow"], f.CX, y, size=36, anchor="center")
            c.pop("eyebrow", max(0.0, t_title - 0.1))
            y += int(36 * 1.8) + 22
        bottom = _over_title(c, s, y, t_title, 128 if not f.wide else 110)
        for i, b in enumerate(badges):
            pill(c, f"badge{i}", b["text"], f.X1, bottom + 30 + i * 96, size=40, anchor="right")
            c.pop(f"badge{i}", cue(b.get("cue"), 1.0 + i * 0.8))
        if s.get("label"):
            pill(c, "label", s["label"], f.CX, f.BOTTOM - (150 if s.get("source") else 96), size=40, solid=True, anchor="center")
            c.pop("label", cue(s.get("label_cue"), 1.2))
        if s.get("source"):
            c.add(_source(c, s, f.X0, f.BOTTOM - 48))
        return
    col = 560 if f.wide else f.SW
    y_title = f.TOP + (40 if f.wide else 8)
    bottom = head(c, s, y_title, t_title, max_px=104 if not f.wide else 84, width=col)
    iw, ih = int(v["w"]), int(v["h"])
    kind = "phone" if ih > iw * 1.3 else "card"
    pad = 16 if kind == "phone" else 0
    if f.wide:
        x0, max_w, top, low, centre = f.X0 + col + 60, f.SW - col - 60, f.TOP + 10, f.BOTTOM - 10, True
    else:
        x0, max_w, top, low, centre = f.X0, f.SW, bottom + 44, f.BOTTOM - (110 if s.get("label") else 20), False
    wx, wy, ww, wh, _ = _window_geometry(iw, ih, x0 + pad, max_w - 2 * pad, top + pad, low - pad, centre)
    c.rule("win", f"position:absolute; left:{wx - pad}px; top:{wy - pad}px; width:{ww + 2 * pad}px; height:{wh + 2 * pad}px; {t.frame_css(kind)}")
    c.rule("view", f"position:absolute; left:{pad}px; top:{pad}px; width:{ww}px; height:{wh}px; overflow:hidden;"
                   f" border-radius:{40 if kind == 'phone' else 0}px; background:#000;")
    c.rule("video", f"position:absolute; left:0; top:0; width:{ww}px; height:{wh}px; object-fit:cover;")
    c.add(f'<div id="{c.id("win")}"><div id="{c.id("view")}">{_video_tag(c, v["src"])}{_source(c, s, 22, wh - 50)}</div></div>')
    # the footage is there from the first frame and settles
    c.tw("win", {"opacity": 1, "y": 40, "scale": 0.97}, {"opacity": 1, "y": 0, "scale": 1, "duration": 0.7, "ease": t.ease}, 0.0)
    for i, b in enumerate(badges):
        if f.wide:
            pill(c, f"badge{i}", b["text"], f.X0, bottom + 40 + i * 96, size=40)
        else:
            pill(c, f"badge{i}", b["text"], wx + ww - 10, wy - 30 + i * 96, size=40, anchor="right")
        c.pop(f"badge{i}", cue(b.get("cue"), 1.0 + i * 0.8))
    if s.get("label"):
        if f.wide:
            pill(c, "label", s["label"], f.X0, f.BOTTOM - 80, size=36, solid=True)
        else:
            pill(c, "label", s["label"], f.CX, wy + wh + 28, size=40, solid=True, anchor="center")
        c.pop("label", cue(s.get("label_cue"), c.dur - 1.8))


def _odometer(c: Comp, name: str, value: str, x_center: int, y: int, size: int, t_at: float, spin: float = 1.1) -> None:
    """Rolling digit columns (pure transforms, so any seek shows the right digits)."""
    t = c.t
    digits = [ch for ch in value]
    col_w = int(size * 0.6)
    width = sum(col_w if ch.isdigit() else int(size * 0.32) for ch in digits)
    left = x_center - width // 2
    parts, ids = [], []
    x = 0
    for i, ch in enumerate(digits):
        if ch.isdigit():
            loops = 1 + (len(digits) - i) // 2
            stack = "".join(f"<div style='height:{size}px; line-height:{size}px;'>{n % 10}</div>" for n in range(loops * 10 + int(ch) + 1))
            cid = f"{name}-d{i}"
            parts.append(f'<div style="position:absolute; left:{x}px; top:0; width:{col_w}px; height:{size}px; overflow:hidden; text-align:center;">'
                         f'<div id="{c.id(cid)}">{stack}</div></div>')
            ids.append((cid, -(loops * 10 + int(ch)) * size))
            x += col_w
        else:
            parts.append(f'<div style="position:absolute; left:{x}px; top:0; width:{int(size * 0.32)}px; text-align:center; line-height:{size}px;">{esc(ch)}</div>')
            x += int(size * 0.32)
    c.rule(name, f"position:absolute; left:{left}px; top:{y}px; width:{width}px; height:{size}px; font-size:{size}px; font-weight:{t.big_weight};"
                 f"color:{t.primary}; font-variant-numeric:tabular-nums; {t.number_big_css()}")
    c.add(f'<div id="{c.id(name)}">{"".join(parts)}</div>')
    c.tw(name, {"opacity": 0, "scale": 0.9}, {"opacity": 1, "scale": 1, "duration": 0.4, "ease": t.ease}, t_at)
    for j, (cid, dy) in enumerate(ids):
        c.tw(cid, {"y": 0}, {"y": dy, "duration": spin + 0.12 * (len(ids) - j), "ease": "power3.out"}, t_at)


def stat(c: Comp, s: dict, cue) -> None:
    """One number counts up on its cue; optionally it then turns into a second number."""
    f, t = c.f, c.t
    bottom = head(c, s, f.TOP + 8, cue(s.get("title_cue"), 0.0))
    value = str(s.get("value") or "").strip()
    if not value:
        return
    size = min(260 if not f.wide else 280, int(f.SW * 0.9 / max(1, len(value) * 0.6)))
    mid = 640 if not f.wide else 520
    y = max(bottom + (90 if not f.wide else 50), mid - size // 2)
    t_value = cue(s.get("value_cue"), 0.9)
    _odometer(c, "num", value, f.CX, y, size, t_value)
    unit_y = y + size + 30
    if s.get("unit"):
        c.rule("unit", f"position:absolute; left:{f.X0}px; top:{unit_y}px; width:{f.SW}px; text-align:center; font-size:52px; font-weight:700;")
        c.add(f'<div id="{c.id("unit")}">{esc(s["unit"])}</div>')
        c.rise("unit", t_value + 0.3, dy=20, d=0.45)
    then = str(s.get("then") or "").strip()
    if then:
        t_then = cue(s.get("then_cue"), t_value + 2.2)
        c.to(["num"] + (["unit"] if s.get("unit") else []), {"opacity": 0, "y": -40, "filter": "blur(8px)", "duration": 0.35, "ease": "power2.in"}, t_then - 0.3)
        _odometer(c, "num2", then, f.CX, y, size, t_then, spin=0.9)
        if s.get("then_unit"):
            c.rule("unit2", f"position:absolute; left:{f.X0}px; top:{unit_y}px; width:{f.SW}px; text-align:center; font-size:52px; font-weight:700; color:{t.primary};")
            c.add(f'<div id="{c.id("unit2")}">{esc(s["then_unit"])}</div>')
            c.rise("unit2", t_then + 0.3, dy=20, d=0.45)
    if s.get("sub"):
        c.rule("sub", f"position:absolute; left:{f.X0}px; top:{unit_y + 100}px; width:{f.SW}px; text-align:center; font-size:42px; color:{t.muted};")
        c.add(f'<div id="{c.id("sub")}">{esc(s["sub"])}</div>')
        c.rise("sub", cue(s.get("sub_cue"), c.dur - 2.0), dy=20, d=0.45)


def change(c: Comp, s: dict, cue) -> None:
    """Before → after rows: the old line is struck through and the new one types over it."""
    f, t = c.f, c.t
    bottom = head(c, s, f.TOP + 8, cue(s.get("title_cue"), 0.0))
    rows = [r for r in (s.get("rows") or []) if str(r.get("after", "")).strip()][:3]
    footer_size = fit(s["footer"], 96, f.SW, min_px=56) if s.get("footer") else 0
    room = f.BOTTOM - bottom - 50 - (int(footer_size * 1.3) + 40 if footer_size else 0)
    gap = 26 if not f.wide else 20
    lo, hi = (150, 176) if not f.wide else (120, 150)
    row_h = max(lo, min(hi, (room - (len(rows) - 1) * gap) // max(1, len(rows))))
    width = f.SW if not f.wide else min(f.SW, 1400)
    x0 = f.X0 if not f.wide else f.CX - width // 2
    y = bottom + (50 if not f.wide else 36)
    for i, r in enumerate(rows):
        name = f"row{i}"
        before, after = str(r.get("before", "")), str(r["after"])
        bsize = fit(before, 42, width - 80, 28)
        asize = fit(after, 46, width - 80, 30)
        card(c, name, x0, y, width, row_h, 24, "padding:22px 36px;" if not f.wide else "padding:16px 36px;")
        tag = (f'<div style="display:inline-block; height:46px; padding:0 18px; border-radius:{t.chip_radius}; {t.eyebrow_css()}'
               f'font-size:28px; font-weight:700; line-height:46px;">{esc(r.get("label") or "改")}</div>') if r.get("label") is not None else ""
        c.add(f'<div id="{c.id(name)}">{tag}<div style="position:relative; height:70px; margin-top:12px;">'
              f'<div id="{c.id(name + "-b")}" style="position:absolute; left:0; top:8px; font-size:{bsize}px; color:{t.muted}; white-space:nowrap;">{esc(before)}'
              f'<div id="{c.id(name + "-x")}" style="position:absolute; left:-4px; right:-4px; top:52%; height:4px; background:{t.red}; transform-origin:0 50%;"></div></div>'
              f'<div id="{c.id(name + "-a")}" style="position:absolute; left:0; top:6px; font-size:{asize}px; font-weight:700; white-space:nowrap;">{esc(after)}</div>'
              f'</div></div>')
        t_row = cue(r.get("cue"), 1.0 + i * 1.6)
        c.rise(name, t_row, dy=30, d=0.45)
        c.set(name + "-a", {"clipPath": "inset(0% 100% 0% 0%)"}, 0)
        t_strike = t_row + 0.55
        c.tw(name + "-x", {"scaleX": 0}, {"scaleX": 1, "duration": 0.3, "ease": "power2.out"}, t_strike)
        c.to(name + "-b", {"opacity": 0, "duration": 0.2}, t_strike + 0.4)
        type_d = min(0.9, 0.05 * len(after) + 0.2)
        c.tw(name + "-a", {"clipPath": "inset(0% 100% 0% 0%)"}, {"clipPath": "inset(0% 0% 0% 0%)", "duration": type_d, "ease": "none"}, t_strike + 0.45)
        y += row_h + gap
    if s.get("footer"):
        size = footer_size
        c.rule("footer", f"position:absolute; left:{f.X0}px; top:{min(y + 30, f.BOTTOM - int(size * 1.3))}px; width:{f.SW}px; text-align:center;"
                         f"font-size:{size}px; font-weight:{t.big_weight}; white-space:nowrap; {t.big_css()}")
        c.add(f'<div id="{c.id("footer")}">{accent_html(s["footer"], s.get("footer_accent", ""), t)}</div>')
        c.pop("footer", cue(s.get("footer_cue"), c.dur - 2.0))


def quote(c: Comp, s: dict, cue) -> None:
    """The calm beat: one sentence, big, with a highlighter sweep on the key word."""
    f, t = c.f, c.t
    mid = 730 if not f.wide else 500
    if s.get("lead"):
        c.rule("lead", f"position:absolute; left:{f.X0}px; top:{f.TOP + (120 if not f.wide else 40)}px; width:{f.SW}px; text-align:center; font-size:44px; color:{t.muted};"
                       f"line-height:1.5;")
        c.add(f'<div id="{c.id("lead")}">{esc(s["lead"])}</div>')
        c.rise("lead", cue(s.get("lead_cue"), 0.0), dy=20, d=0.5)
    lines = [str(x) for x in (s.get("lines") or []) if str(x).strip()][:4] or ["…"]
    size = min(fit(line, 116 if not f.wide else 120, f.SW, min_px=64) for line in lines)
    lh = int(size * 1.22)
    total = len(lines) * lh
    top = mid - total // 2
    first = cue(s.get("lines_cue", [None])[0] if isinstance(s.get("lines_cue"), list) else None, 0.0 if not s.get("lead") else c.dur * 0.35)
    if s.get("lead"):
        c.to("lead", {"opacity": 0, "y": -40, "duration": 0.45, "ease": "power2.in"}, first - 0.3)
    accent = s.get("accent", "")
    cues = s.get("lines_cue") if isinstance(s.get("lines_cue"), list) else []
    hl_line = -1
    for i, line in enumerate(lines):
        name = f"line{i}"
        body = esc(line)
        if accent and accent in line:
            a, b = line.split(accent, 1)
            body = f'{esc(a)}{t.marker_html(c.id("hl"), esc(accent))}{esc(b)}'
            accent = ""  # only once
            hl_line = i
        c.rule(name, f"position:absolute; left:{f.X0}px; top:{top + i * lh}px; width:{f.SW}px; text-align:center; font-size:{size}px;"
                     f"line-height:{lh}px; font-weight:{t.big_weight}; white-space:nowrap; {t.big_css()}")
        c.add(f'<div id="{c.id(name)}">{body}</div>')
        at = cue(cues[i] if i < len(cues) else None, first + i * 1.4)
        c.tw(name, {"opacity": 0, "y": 60}, {"opacity": 1, "y": 0, "duration": 0.8, "ease": t.ease}, at)
        if hl_line == i:
            highlight_word(c, "hl", cue(s.get("accent_cue"), at + 0.5))


def cta(c: Comp, s: dict, cue, final: bool) -> None:
    """End card: optional repo screenshot, a typed command or link, then the signature.
    On a wide canvas the screenshot takes the left half and the rest stacks on the right."""
    f, t = c.f, c.t
    y = f.TOP + 20
    x, width = f.X0, f.SW
    img = s.get("image") or {}
    if img.get("src") and img.get("w") and img.get("h"):
        iw, ih = int(img["w"]), int(img["h"])
        if f.wide:
            ww = 860
            scale = ww / iw
            wh = min(int(ih * scale), f.BOTTOM - f.TOP - 60)
            wx, wy = f.X0, f.TOP + (f.BOTTOM - f.TOP - wh) // 2
            x, width = f.X0 + ww + 70, f.SW - ww - 70
        else:  # the top of the picture, as tall as the rest of the card leaves room for
            bar = 44 if t.frame_kind == "browser" else 0
            sign_px = fit(s["sign"], 112, f.SW, min_px=56) if s.get("sign") else 0
            below = (90 if s.get("chips") else 0) + (150 if s.get("command") else 0) + \
                (40 + int(sign_px * 1.35) + (60 if s.get("sign_sub") else 0) if sign_px else 0)
            scale = f.SW / iw
            wh = max(160, min(int(ih * scale), 420, f.BOTTOM - y - 40 - bar - below))
            ww, wx, wy = f.SW, f.X0, y
        bar = 44 if t.frame_kind == "browser" else 0  # the top of the page, in the style's frame
        c.rule("win", f"position:absolute; left:{wx}px; top:{wy}px; width:{ww}px; height:{wh + bar}px; {t.frame_css(t.frame_kind)}")
        c.rule("view", f"position:absolute; left:0; top:{bar}px; width:{ww}px; height:{wh}px; overflow:hidden; background:#fff;")
        c.rule("img", "position:absolute; left:0; top:0; width:%dpx; height:%dpx; transform-origin:0 0; max-width:none;" % (iw, ih))
        chrome = t.frame_bar_html(ww, bar) if bar else ""
        c.add(f'<div id="{c.id("win")}">{chrome}<div id="{c.id("view")}"><img id="{c.id("img")}" src="{esc(img["src"])}" alt="" /></div></div>')
        c.set("img", {"scale": round(scale, 4)}, 0)
        c.tw("win", {"opacity": 0, "y": 70}, {"opacity": 1, "y": 0, "duration": 0.6, "ease": t.ease}, 0.05)
        wh += bar
        if s.get("badge"):
            pill(c, "badge", s["badge"], wx + ww - 10, wy - 30, size=40, anchor="right")
            c.pop("badge", cue(s.get("badge_cue"), 0.9))
        y = y + wh + 40 if not f.wide else f.TOP + 60
    elif s.get("badge"):
        size = fit(s["badge"], 120, f.SW, min_px=64)
        c.rule("badge", f"position:absolute; left:{f.X0}px; top:{y + 60}px; width:{f.SW}px; text-align:center; font-size:{size}px;"
                        f" font-weight:{t.big_weight}; color:{t.primary}; {t.big_css()}")
        c.add(f'<div id="{c.id("badge")}">{esc(s["badge"])}</div>')
        c.pop("badge", cue(s.get("badge_cue"), 0.2))
        y += 60 + size + 60
    chips = [ch for ch in (s.get("chips") or []) if str(ch.get("text", "")).strip()][:3]
    if chips:
        cx = x
        for i, chip in enumerate(chips):
            name = f"chip{i}"
            pill(c, name, chip["text"], cx, y, size=36, solid=False)
            c.pop(name, cue(chip.get("cue"), 1.6 + i * 0.6))
            cx += int(cells(str(chip["text"])) * 36 + 36 * 1.4) + 20
        y += 90
    if s.get("command"):
        cmd = str(s["command"])
        size = fit(cmd, 40, width - 140, 26)
        c.rule("term", f"position:absolute; left:{x}px; top:{y}px; width:{width}px; min-height:110px; border-radius:30px; background:{t.term};"
                       f"color:#fff; padding:28px 40px; font-size:{size}px; line-height:1.5; white-space:nowrap;")
        c.add(f'<div id="{c.id("term")}"><span style="color:{t.term_prompt};">$ </span><span id="{c.id("cmd")}" style="display:inline-block;">{esc(cmd)}</span></div>')
        t_cmd = cue(s.get("command_cue"), 1.6)
        c.tw("term", {"opacity": 0, "scale": 0.94}, {"opacity": 1, "scale": 1, "duration": 0.4, "ease": t.ease}, t_cmd)
        c.tw("cmd", {"clipPath": "inset(0% 100% 0% 0%)"}, {"clipPath": "inset(0% 0% 0% 0%)", "duration": min(2.2, 0.04 * len(cmd) + 0.4), "ease": "none"}, t_cmd + 0.3)
        y += 150
    if s.get("sign"):
        t_sign = cue(s.get("sign_cue"), max(c.dur * 0.6, c.dur - 3.2))
        dim = [n for n in ("win", "badge", "term") if f'id="{c.id(n)}"' in "".join(c.html)] + [f"chip{i}" for i in range(len(chips))]
        if dim:
            c.to(dim, {"opacity": 0.28, "duration": 0.5, "ease": "power2.out"}, t_sign - 0.2)
        sy = max(y + 40, min(880, f.BOTTOM - int(fit(s["sign"], 112, width, min_px=56) * 1.35) - 70)) if not f.wide else max(y + 30, 520)
        size = fit(s["sign"], 112 if not f.wide else 96, width, min_px=56)
        c.rule("sign", f"position:absolute; left:{x}px; top:{sy}px; width:{width}px; text-align:center; font-size:{size}px;"
                       f" font-weight:{t.big_weight}; white-space:nowrap; {t.big_css()}")
        c.add(f'<div id="{c.id("sign")}">{accent_html(s["sign"], s.get("sign_accent", ""), t)}</div>')
        c.rise("sign", t_sign, dy=40)
        if s.get("sign_sub"):
            c.rule("signsub", f"position:absolute; left:{x}px; top:{sy + int(size * 1.35)}px; width:{width}px; text-align:center; font-size:44px; color:{t.muted};")
            c.add(f'<div id="{c.id("signsub")}">{esc(s["sign_sub"])}</div>')
            c.rise("signsub", cue(s.get("sign_sub_cue"), t_sign + 0.6), dy=20, d=0.45)
    if final:
        c.to("all", {"opacity": 0, "duration": 0.55, "ease": "power2.inOut"}, max(0.0, c.dur - 0.6))


BUILDERS = {"hook": hook, "points": points, "shot": shot, "clip": clip, "stat": stat, "change": change, "quote": quote}


def scene_html(sid: str, scene: dict, duration: float, cue, final: bool = False, frame: Frame = VERTICAL, style: Style = DEFAULT) -> str:
    c = Comp(sid, duration, stage_frame(frame, style), style)
    kind = scene.get("type")
    if kind == "cta":
        cta(c, scene, cue, final)
    else:
        BUILDERS.get(kind, points)(c, scene, cue)
    return c.build()


# ------------------------------------------------------------------ captions and the author mark

def captions_html(groups: list[dict], total: float, frame: Frame = VERTICAL, style: Style = DEFAULT) -> str:
    """A phrase at a time, the way this genre captions: it fades up when it starts,
    its one or two key words in the accent colour, no box and no word-by-word sweep."""
    f, t = frame, style
    css = f"""{t.font_faces()}
#root {{ position: absolute; inset: 0; pointer-events: none; font-family: {t.font_stack()}; }}
.cap-g {{ position: absolute; left: 0; right: 0; top: {f.CAP_TOP}px; height: {f.CAP_H}px; display: flex; align-items: center;
  justify-content: center; opacity: 0; }}
{t.caption_css(f)}"""
    body, js = [], []
    for gi, g in enumerate(groups):
        words = "".join(f'<span class="cap-w{" cap-k" if w.get("key") else ""}">{esc(w["text"])}</span>' for w in g["words"])
        body.append(f'<div id="cap-{gi}" class="cap-g"><div class="cap-p">{words}</div></div>')
        nxt = groups[gi + 1]["start"] if gi + 1 < len(groups) else total
        end = min(nxt, g["end"] + 0.3)
        js.append(f'tl.fromTo("#cap-{gi}", {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.12, ease: "power2.out" }}, {g["start"]:.3f});'
                  f' tl.set("#cap-{gi}", {{ opacity: 0 }}, {max(g["start"] + 0.15, end):.3f});')
    return f"""<template>
  {GSAP}
  <style>
{css}
  </style>
  <div id="root" data-composition-id="captions" data-width="{f.W}" data-height="{f.H}" data-duration="{total:.3f}">
{chr(10).join('    ' + b for b in body)}
  </div>
  <script>
    (function () {{
      const tl = gsap.timeline({{ paused: true }});
{chr(10).join('      ' + j for j in js)}
      tl.to({{}}, {{ duration: {total:.3f} }}, 0);
      window.__timelines["captions"] = tl;
    }})();
  </script>
</template>
"""


def _headline_lines(headline, accent_words) -> list[str]:
    lines = [str(x).strip() for x in (headline or []) if str(x).strip()][:2]
    out = []
    for line in lines:
        body = esc(line)
        for k in accent_words:
            if k and k in line:
                body = body.replace(esc(k), f'<span class="k">{esc(k)}</span>', 1)
                break
        out.append(body)
    return out


def chrome_html(author: str, tagline: str, total: float, frame: Frame = VERTICAL, style: Style = DEFAULT,
                headline: list[str] | None = None, marks: list[float] | None = None, fade: bool = True,
                head_keys: list[str] | None = None, span: tuple[float, float] = (0.0, 1.0)) -> str:
    """What stays on screen the whole video: the author mark (or, in styles that
    keep one, the standing headline) and a progress bar with a notch per scene."""
    f, t = frame, style
    k = 1.0 if not f.wide else 0.86
    mx, my = f.MARK
    lines = _headline_lines(headline, head_keys or []) if t.headline else []
    parts, css, js = [], [], []
    if lines and not f.wide:   # two big lines across the top
        size = min(92, min(int(f.SW * 0.96 / max(1.0, cells(_strip(x)))) for x in lines))
        for i, line in enumerate(lines):
            parts.append(f'<div class="hd hd{i + 1}" style="top:{70 + i * int(size * 1.12)}px; font-size:{size}px;">{line}</div>')
        bar_y, bar_x0, bar_x1 = min(f.TOP + HEADLINE_ROOM - 30, 70 + len(lines) * int(size * 1.12) + 16), f.X0, f.X1
        js.append('tl.fromTo(".hd", { opacity: 0, y: -12 }, { opacity: 1, y: 0, duration: 0.3, stagger: 0.08, ease: "power3.out" }, 0);')
        css.append(t.headline_css())
    elif lines:                # a topic tag where the mark would be
        parts.append(f'<div id="chrome-tag" style="position:absolute; left:{mx}px; top:{my}px; font-size:40px; white-space:nowrap;">'
                     f'<span class="hd1">{lines[0]}</span>{" <span class=hd2>" + lines[1] + "</span>" if len(lines) > 1 else ""}</div>')
        css.append(t.headline_css().replace(".hd {", "#chrome-tag {").replace("position: absolute; left: 0; right: 0; text-align: center; ", ""))
        bar_y, bar_x0, bar_x1 = f.H - 22, f.X0, f.X1
    else:
        sub = f'<div id="chrome-sub">{esc(tagline)}</div>' if tagline else ""
        parts.append(f"""<div id="chrome-mark">
      <div id="chrome-icon"><svg width="{int(38 * k)}" height="{int(38 * k)}" viewBox="0 0 24 24" fill="none" stroke="{t.mark_icon}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg></div>
      <div><div id="chrome-name">{esc(author)}</div>{sub}</div>
    </div>""")
        css.append(f"""#chrome-mark {{ position: absolute; left: {mx}px; top: {my}px; display: flex; align-items: center; gap: {int(18 * k)}px; }}
#chrome-icon {{ width: {int(68 * k)}px; height: {int(68 * k)}px; border-radius: {int(18 * k)}px; background: {t.mark_bg}; display: flex; align-items: center; justify-content: center; }}
#chrome-name {{ font-size: {int(36 * k)}px; font-weight: 700; color: {t.mark_ink}; line-height: 1.15; }}
#chrome-sub {{ font-size: {int(28 * k)}px; font-weight: 400; color: {t.muted}; line-height: 1.15; }}""")
        js.append('tl.fromTo("#chrome-mark", { opacity: 0, y: -14 }, { opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }, 0.15);')
        bar_y = (my + int(68 * k) + 22) if not f.wide else f.H - 22
        bar_x0, bar_x1 = f.X0, f.X1
    # the progress bar: fills over the video, a notch where each scene starts
    width = bar_x1 - bar_x0
    notches = "".join(f'<div style="position:absolute; left:{int(width * m) - 2}px; top:-1px; width:4px; height:8px; background:{t.base};"></div>'
                      for m in (marks or []) if 0.01 < m < 0.99)
    parts.append(f'<div id="chrome-bar" style="position:absolute; left:{bar_x0}px; top:{bar_y}px; width:{width}px; height:6px; border-radius:3px;'
                 f' background:{t.progress_track}; overflow:hidden;"><div id="chrome-fill" style="position:absolute; inset:0; background:{t.progress};'
                 f' transform-origin:0 50%; transform:scaleX(0);"></div>{notches}</div>')
    js.append(f'tl.fromTo("#chrome-fill", {{ scaleX: {span[0]:.4f} }}, {{ scaleX: {span[1]:.4f}, duration: {total:.3f}, ease: "none" }}, 0);')
    if fade:
        js.append(f'tl.to("#root > div", {{ opacity: 0, duration: 0.5, ease: "power2.inOut" }}, {max(0.0, total - 0.6):.3f});')
    return f"""<template>
  {GSAP}
  <style>
{t.font_faces()}
#root {{ position: absolute; inset: 0; pointer-events: none; font-family: {t.font_stack()}; }}
{chr(10).join(css)}
  </style>
  <div id="root" data-composition-id="chrome" data-width="{f.W}" data-height="{f.H}" data-duration="{total:.3f}">
    {(chr(10) + '    ').join(parts)}
  </div>
  <script>
    (function () {{
      const tl = gsap.timeline({{ paused: true }});
      {(chr(10) + '      ').join(js)}
      tl.to({{}}, {{ duration: {total:.3f} }}, 0);
      window.__timelines["chrome"] = tl;
    }})();
  </script>
</template>
"""


def _strip(markup: str) -> str:
    import re
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


TRANSITION = {
    "fade": ('{ opacity: 0, duration: 0.45, ease: "power2.inOut" }', '{ opacity: 0 }', '{ opacity: 1, duration: 0.45, ease: "power2.inOut" }'),
    "push": ('{ x: -%(W)d, duration: 0.45, ease: "power3.inOut" }', '{ x: %(W)d, opacity: 1 }', '{ x: 0, opacity: 1, duration: 0.45, ease: "power3.inOut" }'),
    "zoom": ('{ scale: 2.2, opacity: 0, filter: "blur(8px)", duration: 0.4, ease: "power3.in" }',
             '{ scale: 0.6, opacity: 0, filter: "blur(8px)" }', '{ scale: 1, opacity: 1, filter: "blur(0px)", duration: 0.4, ease: "power3.out" }'),
    "rise": ('{ y: -%(H4)d, opacity: 0, duration: 0.4, ease: "power3.in" }', '{ y: %(H4)d, opacity: 0 }', '{ y: 0, opacity: 1, duration: 0.45, ease: "power3.out" }'),
    "cut": ('{ opacity: 0, duration: 0.01 }', '{ opacity: 0 }', '{ opacity: 1, duration: 0.01 }'),
}
OVERLAP = 0.45


def transition_kind(prev: dict, cur: dict, style: Style = DEFAULT) -> str:
    return style.transition(prev, cur)


def index_html(scenes: list[dict], starts: list[float], durations: list[float], voices: list[str], bgm: str | None,
               bgm_volume: float, total: float, frame: Frame = VERTICAL, style: Style = DEFAULT,
               sounds: list[tuple[str, float, float]] | None = None) -> str:
    """The whole video; sounds are clips' own sound kept under the voice: (file, start, seconds)."""
    f = frame
    els, trans = [], []
    for n, (src, start, seconds) in enumerate(sounds or [], 1):
        els.append(f'<audio id="el-clip-sound-{n}" src="{src}" data-start="{start:.3f}" data-duration="{seconds:.3f}" '
                   f'data-track-index="12" data-volume="1"></audio>')
    for i, (scene, start, dur) in enumerate(zip(scenes, starts, durations)):
        sid = f"s{i + 1:02d}"
        host = dur + (OVERLAP if i + 1 < len(scenes) else 0)
        els.append(f'<div id="el-{sid}" class="scene" data-composition-id="{sid}" data-composition-src="compositions/{sid}.html" '
                   f'data-start="{start:.3f}" data-duration="{host:.3f}" data-track-index="{i % 2}"></div>')
        els.append(f'<audio id="el-{sid}-voice" src="{voices[i]}" data-start="{start:.3f}" data-duration="{dur:.3f}" '
                   f'data-track-index="10" data-volume="1"></audio>')
        if i:
            kind = transition_kind(scenes[i - 1], scene, style)
            out_, in_from, in_to = (p % {"W": f.W, "H4": f.H // 4} for p in TRANSITION[kind])
            trans.append(f'tl.to("#el-s{i:02d}", {out_}, {start:.3f});')
            trans.append(f'tl.fromTo("#el-{sid}", {in_from}, {in_to}, {start:.3f});')
    if bgm:
        els.append(f'<audio id="el-bgm" src="{bgm}" data-start="0" data-duration="{total:.3f}" data-track-index="11" data-volume="{bgm_volume}"></audio>')
    els.append(f'<div id="el-captions" class="scene" data-composition-id="captions" data-composition-src="compositions/captions.html" '
               f'data-start="0" data-duration="{total:.3f}" data-track-index="2"></div>')
    els.append(f'<div id="el-chrome" class="scene" data-composition-id="chrome" data-composition-src="compositions/chrome.html" '
               f'data-start="0" data-duration="{total:.3f}" data-track-index="3"></div>')
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={f.W}, height={f.H}" />
    {GSAP}
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ width: {f.W}px; height: {f.H}px; overflow: hidden; background: #000; }}
      #root {{ position: relative; width: {f.W}px; height: {f.H}px; overflow: hidden; background: {style.bg}; }}
      .scene {{ position: absolute; inset: 0; width: 100%; height: 100%; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{total:.3f}" data-width="{f.W}" data-height="{f.H}">
{chr(10).join('      ' + e for e in els)}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
{chr(10).join('      ' + t for t in trans)}
      tl.to({{}}, {{ duration: {total:.3f} }}, 0);
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


def preview_html(parts: list[str], duration: float, offsets: dict[str, float], frame: Frame = VERTICAL, style: Style = DEFAULT) -> str:
    """The editor's instant preview of one scene: the same compositions stacked on
    one stage, scaled to the frame. The page only seeks; the editor calls
    window.cwSeek(t) from the voice it plays, so picture and sound stay in step.
    offsets shift a layer's clock (the author mark is already in place after the
    first scene).

    A clip's <video> (and its sound) follows the same clock: seeked while the
    editor scrubs or stands still, played while the clock runs and pulled back
    when it drifts, paused when the calls stop."""
    f = frame
    layers = []
    for part in parts:
        inner = part.strip().removeprefix("<template>").removesuffix("</template>").replace(GSAP, "")
        layers.append(f'<div class="layer">{inner}</div>')
    poster = round(max(0.0, duration * 0.85 - 0.3), 3)  # most of the scene is on screen by then
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    {GSAP}
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}  /* as in index_html, or boxes come out wider */
      html, body {{ width: 100%; height: 100%; overflow: hidden; background: {style.bg}; }}
      #stage {{ position: absolute; left: 0; top: 0; width: {f.W}px; height: {f.H}px; overflow: hidden; background: {style.bg};
        transform-origin: 0 0; }}
      .layer {{ position: absolute; inset: 0; }}
    </style>
    <script>window.__timelines = {{}};</script>
  </head>
  <body>
    <div id="stage">
{chr(10).join(layers)}
    </div>
    <script>
      (function () {{
        const stage = document.getElementById("stage");
        const fit = () => {{ stage.style.transform = "scale(" + Math.min(innerWidth / {f.W}, innerHeight / {f.H}) + ")"; }};
        fit();
        addEventListener("resize", fit);
        const offsets = {json.dumps(offsets)};
        const media = [...document.querySelectorAll("#stage video, #stage audio")];
        let last = {poster}, clock = null, idle = 0;
        const follow = (t) => {{
          const now = performance.now();
          const running = clock !== null && t >= clock.t && t - clock.t < 0.3 && now - clock.at < 300;
          clock = {{ t, at: now }};
          for (const m of media) {{
            const local = t - (+m.dataset.start || 0);
            const end = Math.min(+m.dataset.duration || Infinity, m.duration || Infinity) - 0.04;
            if (!running || local < 0 || local >= end) {{
              if (!m.paused) m.pause();
              const at = Math.max(0, Math.min(local, end));
              if (isFinite(at) && Math.abs(m.currentTime - at) > 0.03) m.currentTime = at;
            }} else {{
              if (Math.abs(m.currentTime - local) > 0.25) m.currentTime = local;
              if (m.paused) m.play().catch(() => {{}});
            }}
          }}
          clearTimeout(idle);
          if (running) idle = setTimeout(() => {{ clock = null; follow(last); }}, 250);  // the clock stopped: back to where it stopped
        }};
        window.cwDuration = {duration:.3f};
        window.cwPoster = {poster};
        window.cwSeek = (t) => {{
          last = t;
          for (const [id, tl] of Object.entries(window.__timelines)) tl.seek(Math.max(0, t + (offsets[id] || 0)));
          if (media.length) follow(t);
        }};
        window.cwSeek(last);
        for (const m of media) m.addEventListener("loadedmetadata", () => {{ clock = null; window.cwSeek(last); }});
        if (document.fonts) document.fonts.ready.then(() => window.cwSeek(last));
      }})();
    </script>
  </body>
</html>
"""


def all_text(scenes: list[dict]) -> str:
    """Every visible string, for the font subset."""
    out = []

    def walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k not in ("src",):
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(scenes)
    return "".join(out)


# ------------------------------------------------------------------ the cover

def cover_html(lines: list[str], key: str, kicker: str, author: str, tagline: str,
               frame: Frame = VERTICAL, style: Style = DEFAULT, image: dict | None = None) -> str:
    """A still cover in the video's style: a small label, one or two lines of big
    words with the key word marked, a framed screenshot under them, the author.
    The words stay inside the crops platforms take from it: 3:4 from the middle
    of a vertical cover (视频号, 小红书), 2.35:1 and 1:1 from the middle of a
    landscape one (the 公众号 article cover)."""
    f, t = frame, style
    lines = [x for x in (lines or []) if x.strip()][:2] or ["（封面大字）"]
    wide = f.wide
    text_w = 1000 if wide else 960
    max_px = (150 if image else 176) if wide else (172 if image else 200)
    size = min(fit(x, max_px, text_w, 64) for x in lines)
    lh = int(size * 1.14)
    block = lh * len(lines)
    pill_px = 38 if wide else 46
    pill_h = int(pill_px * 1.9) if kicker else 0
    gap = int(pill_px * 0.9) if kicker else 0
    if image:
        top = 150 if wide else 300  # inside the 2.35:1 band of a landscape cover
    else:
        top = (f.H - (pill_h + gap + block)) // 2 - (0 if wide else 40)
    parts = [f'<div id="bg" style="position:absolute; inset:0; background:{t.bg};"></div>']
    y = top
    if kicker:
        parts.append(f'<div style="position:absolute; left:0; right:0; top:{y}px; text-align:center;">'
                     f'<span style="display:inline-block; {t.pill_solid_css()} border-radius:{t.chip_radius}; font-size:{pill_px}px; font-weight:900;'
                     f' padding:{int(pill_px * 0.32)}px {int(pill_px * 0.8)}px; line-height:1.15;">{esc(kicker)}</span></div>')
        y += pill_h + gap
    for i, line in enumerate(lines):
        parts.append(f'<div style="position:absolute; left:0; right:0; top:{y + i * lh}px; text-align:center; white-space:nowrap; font-size:{size}px;'
                     f' line-height:1.14; font-weight:{t.title_weight}; letter-spacing:0.01em; {t.title_css()}">{accent_html(line, key, t)}</div>')
    y += block
    if image:
        iw, ih = int(image["w"]), int(image["h"])
        kind = "phone" if ih > iw * 1.3 else t.frame_kind
        bar = 44 if kind == "browser" else 0
        pad = 16 if kind == "phone" else 0
        if wide:  # peeks up from the bottom edge
            ww = min(1100, iw)
            wh = int(ih * ww / iw)
            fx, fy = (f.W - ww) // 2 - pad, y + 70
        else:
            room_top, room_bottom = y + 80, f.H - 250
            scale = min(900 / iw, (room_bottom - room_top - bar - 2 * pad) / ih)
            ww, wh = int(iw * scale), int(ih * scale)
            fx, fy = (f.W - ww) // 2 - pad, room_top
        tilt = "" if wide else " transform:rotate(-2deg);"
        radius = 40 if kind == "phone" else 0
        chrome = t.frame_bar_html(ww + 2 * pad, bar) if bar else ""
        parts.append(f'<div style="position:absolute; left:{fx}px; top:{fy}px; width:{ww + 2 * pad}px; height:{wh + bar + 2 * pad}px;'
                     f' {t.frame_css(kind)}{tilt}">{chrome}<div style="position:absolute; left:{pad}px; top:{bar + pad}px; width:{ww}px; height:{wh}px;'
                     f' overflow:hidden; border-radius:{radius}px; background:#fff;"><img src="{esc(image["src"])}" alt=""'
                     f' style="width:{ww}px; height:{wh}px; display:block;" /></div></div>')
    k = 1.0 if not wide else 0.86
    sub = f'<div style="font-size:{int(28 * k)}px; font-weight:400; color:{t.muted}; line-height:1.15;">{esc(tagline)}</div>' if tagline else ""
    mark_at = f"left:{f.MARK[0]}px; top:{f.MARK[1]}px;" if wide else "left:0; right:0; bottom:120px; justify-content:center;"
    parts.append(f"""<div style="position:absolute; {mark_at} display:flex; align-items:center; gap:{int(18 * k)}px;">
      <div style="width:{int(68 * k)}px; height:{int(68 * k)}px; border-radius:{int(18 * k)}px; background:{t.mark_bg}; display:flex; align-items:center; justify-content:center;">
        <svg width="{int(38 * k)}" height="{int(38 * k)}" viewBox="0 0 24 24" fill="none" stroke="{t.mark_icon}" stroke-width="2" stroke-linecap="round"
          stroke-linejoin="round"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg></div>
      <div><div style="font-size:{int(36 * k)}px; font-weight:700; color:{t.mark_ink}; line-height:1.15;">{esc(author)}</div>{sub}</div>
    </div>""")
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <style>
{t.font_faces()}
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ width: {f.W}px; height: {f.H}px; overflow: hidden; background: {t.base}; }}
      #cover {{ position: relative; width: {f.W}px; height: {f.H}px; overflow: hidden; font-family: {t.font_stack()}; color: {t.ink}; }}
      {t.backdrop_css(f, "bg")}
    </style>
  </head>
  <body>
    <div id="cover">
      {(chr(10) + '      ').join(parts)}
    </div>
  </body>
</html>
"""
