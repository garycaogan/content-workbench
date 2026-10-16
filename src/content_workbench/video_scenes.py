"""HyperFrames compositions for the short video, written as plain HTML.

Every scene is one sub-composition built here from its data: positions, font
sizes and the moment each element appears are computed in Python, so the page
only runs a paused GSAP timeline (deterministic, seek-safe, no DOM building in
the browser). Cue times come from the aligned voice, which is what makes each
piece land when the narration names it.

Canvas 1080×1920. The stage is y 232–1236 / x 56–1024; captions own y 1262–1452;
the platform's own title and buttons cover everything below that, and a root
overlay draws the author mark in the top band."""

from __future__ import annotations

import html
import json

W, H = 1080, 1920
X0, X1 = 56, 1024
SW = X1 - X0  # stage width 968
TOP, BOTTOM = 232, 1236
CAP_TOP, CAP_H = 1262, 190

BG, INK, PRIMARY, MUTED, FAINT = "#f6f6f3", "#18181b", "#0f766e", "#6f6f78", "#a1a1aa"
TINT, TINT2, BORDER, HL, RED = "rgba(15,118,110,0.08)", "rgba(15,118,110,0.15)", "rgba(15,118,110,0.22)", "#fde68a", "#dc2626"
WEIGHTS = (400, 600, 700, 800)
GSAP = '<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>'

FONT_FACES = "\n".join(
    f'@font-face {{ font-family: "Noto Sans SC"; src: url("assets/fonts/noto-{w}.woff2") format("woff2"); '
    f'font-weight: {w}; font-style: normal; font-display: block; }}' for w in WEIGHTS)


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


def fit(text: str, max_px: int, width: int = SW, min_px: int = 40) -> int:
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


def lines_of(title, max_px: int, min_one_line: int) -> list[str]:
    if isinstance(title, list):
        return [str(t) for t in title if str(t).strip()]
    title = str(title or "").strip()
    if not title:
        return []
    if "\n" in title:
        return [t for t in title.split("\n") if t.strip()]
    if fit(title, max_px) >= min_one_line:
        return [title]
    return split_two(title)


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def accent_html(text: str, accent: str, color: str = PRIMARY) -> str:
    if accent and accent in text:
        a, b = text.split(accent, 1)
        return f'{esc(a)}<span style="color:{color}">{esc(accent)}</span>{esc(b)}'
    return esc(text)


# ------------------------------------------------------------------ composition builder

class Comp:
    def __init__(self, sid: str, duration: float):
        self.sid, self.dur = sid, round(duration, 3)
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
        self.tw(names, {"opacity": 0, "y": dy}, {"opacity": 1, "y": 0, "duration": d, "ease": "power3.out"}, t)

    def pop(self, names, t: float, d: float = 0.45) -> None:
        self.tw(names, {"opacity": 0, "scale": 0.86}, {"opacity": 1, "scale": 1, "duration": d, "ease": "power3.out"}, t)

    def build(self) -> str:
        d = self.dur
        return f"""<template>
  {GSAP}
  <style>
{FONT_FACES}
#root {{ position: absolute; inset: 0; width: {W}px; height: {H}px; overflow: hidden;
  font-family: "Noto Sans SC", sans-serif; letter-spacing: 0; color: {INK}; }}
[id="{self.id('bg')}"] {{ position: absolute; inset: 0; background: {BG}; }}
{chr(10).join(self.css)}
  </style>
  <div id="root" data-composition-id="{self.sid}" data-width="{W}" data-height="{H}" data-duration="{d}">
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
      tl.to({{}}, {{ duration: {d} }}, 0);
      window.__timelines["{self.sid}"] = tl;
    }})();
  </script>
</template>
"""


# ------------------------------------------------------------------ shared pieces

def head(c: Comp, s: dict, y: int, t_title: float, max_px: int = 104) -> int:
    """Eyebrow chip + headline at the top of the stage; returns the bottom y."""
    if s.get("eyebrow"):
        c.rule("eyebrow", f"position:absolute; left:{X0}px; top:{y}px; height:58px; padding:0 26px; border-radius:100px;"
                          f"background:{TINT2}; color:{PRIMARY}; font-size:34px; font-weight:600; line-height:58px; white-space:nowrap;")
        c.add(f'<div id="{c.id("eyebrow")}">{esc(s["eyebrow"])}</div>')
        c.rise("eyebrow", max(0.0, t_title - 0.1), dy=20, d=0.45)
        y += 58 + 26
    lines = lines_of(s.get("title"), max_px, 76)
    if not lines:
        return y
    size = min(fit(line, max_px, min_px=56) for line in lines)
    lh = int(size * 1.18)
    for i, line in enumerate(lines):
        name = f"title{i}"
        c.rule(name, f"position:absolute; left:{X0}px; top:{y + i * lh}px; width:{SW}px; font-size:{size}px; line-height:{lh}px;"
                     f"font-weight:700; white-space:nowrap;")
        c.add(f'<div id="{c.id(name)}">{accent_html(line, s.get("accent", ""))}</div>')
        c.rise(name, t_title + i * 0.14, dy=36)
    return y + len(lines) * lh


def swap_title(c: Comp, s: dict, y_title: int, bottom: int, t: float, old_lines: int) -> None:
    """Replace the headline in place (blur-out up, blur-in from below)."""
    lines = lines_of(s.get("title2"), 104, 76)
    if not lines:
        return
    size = min(fit(line, 104, min_px=56) for line in lines)
    lh = int(size * 1.18)
    for i in range(old_lines):
        c.tw(f"title{i}", {"opacity": 1, "y": 0, "filter": "blur(0px)"},
             {"opacity": 0, "y": -30, "filter": "blur(8px)", "duration": 0.3, "ease": "power2.in"}, t - 0.25)
    top = bottom - len(lines) * lh
    for i, line in enumerate(lines):
        name = f"titleb{i}"
        c.rule(name, f"position:absolute; left:{X0}px; top:{top + i * lh}px; width:{SW}px; font-size:{size}px; line-height:{lh}px;"
                     f"font-weight:700; white-space:nowrap;")
        c.add(f'<div id="{c.id(name)}">{accent_html(line, s.get("accent2") or s.get("accent", ""))}</div>')
        c.tw(name, {"opacity": 0, "y": 34, "filter": "blur(8px)"},
             {"opacity": 1, "y": 0, "filter": "blur(0px)", "duration": 0.45, "ease": "power3.out"}, t + 0.05 + i * 0.1)


def pill(c: Comp, name: str, text: str, x: int, y: int, size: int = 40, solid: bool = True, anchor: str = "left") -> None:
    pos = f"left:{x}px;" if anchor == "left" else (f"right:{W - x}px;" if anchor == "right" else
                                                    f"left:{x}px; transform-origin:center; translate:-50% 0;")
    style = (f"background:{PRIMARY}; color:#fff;" if solid else f"background:#fff; color:{PRIMARY}; border:2px solid {BORDER};")
    c.rule(name, f"position:absolute; {pos} top:{y}px; height:{int(size * 1.8)}px; padding:0 {int(size * 0.7)}px; border-radius:100px;"
                 f"{style} font-size:{size}px; font-weight:700; line-height:{int(size * 1.8) - (4 if not solid else 0)}px; white-space:nowrap;")
    c.add(f'<div id="{c.id(name)}">{esc(text)}</div>')


def highlight_word(c: Comp, name: str, t: float) -> None:
    c.tw(name, {"scaleX": 0}, {"scaleX": 1, "duration": 0.5, "ease": "power2.out"}, t)


# ------------------------------------------------------------------ scene types

def hook(c: Comp, s: dict, cue) -> None:
    """Big words that carry the promise; optional lead line, chips and a closing box."""
    y = TOP + 20
    if s.get("lead"):
        size = fit(s["lead"], 76, min_px=52)
        c.rule("lead", f"position:absolute; left:{X0}px; top:{y}px; width:{SW}px; font-size:{size}px; font-weight:700; white-space:nowrap;")
        c.add(f'<div id="{c.id("lead")}">{accent_html(s["lead"], s.get("lead_accent", ""))}</div>')
        c.rise("lead", cue(s.get("lead_cue"), 0.0), dy=30)
        y += int(size * 1.3)
    if s.get("sub"):
        c.rule("sub", f"position:absolute; left:{X0}px; top:{y}px; width:{SW}px; font-size:44px; color:{MUTED}; white-space:nowrap;")
        c.add(f'<div id="{c.id("sub")}">{esc(s["sub"])}</div>')
        c.rise("sub", cue(s.get("sub_cue"), 0.4), dy=20, d=0.45)
    words = lines_of(s.get("words"), 280, 140) or ["…"]
    size = min(fit(w, 280, min_px=110) for w in words)
    lh = int(size * 1.08)
    block_h = len(words) * lh
    cy = 720 if (s.get("lead") or s.get("sub")) else 680
    top = cy - block_h // 2
    c.rule("disc", f"position:absolute; left:{540 - 330}px; top:{cy - 330}px; width:660px; height:660px; border-radius:50%; background:{TINT};")
    c.add(f'<div id="{c.id("disc")}"></div>')
    c.rule("words", f"position:absolute; left:{X0}px; top:{top}px; width:{SW}px; text-align:center; transform-origin:50% 50%;")
    inner = "".join(f'<div id="{c.id(f"w{i}")}" style="font-size:{size}px; line-height:{lh}px; font-weight:800; white-space:nowrap;">'
                    f'{accent_html(w, s.get("accent", ""))}</div>' for i, w in enumerate(words))
    c.add(f'<div id="{c.id("words")}">{inner}</div>')
    t_words = cue(s.get("words_cue"), 0.0 if not (s.get("lead") or s.get("sub")) else 1.2)
    c.tw("disc", {"opacity": 0, "scale": 0.7}, {"opacity": 1, "scale": 1, "duration": 0.9, "ease": "power3.out"}, max(0, t_words - 0.2))
    for i in range(len(words)):
        c.tw(f"w{i}", {"opacity": 0, "scale": 1.25, "filter": "blur(10px)"},
             {"opacity": 1, "scale": 1, "filter": "blur(0px)", "duration": 0.5, "ease": "power3.out"}, t_words + i * 0.32)
    chips = [x for x in (s.get("chips") or []) if str(x.get("text", "")).strip()][:4]
    if not chips:
        return
    t_first = min(cue(ch.get("cue"), t_words + 1.0 + 0.6 * i) for i, ch in enumerate(chips))
    t_first = max(t_first, t_words + 0.6)
    # the big words move up and shrink to make room for the chip grid
    shrink = 0.5
    new_cy = TOP + 60 + int(block_h * shrink / 2)
    above = [n for n in ("lead", "sub") if s.get(n)]
    if above:
        c.to(above, {"opacity": 0, "y": -30, "duration": 0.35, "ease": "power2.in"}, t_first - 0.45)
    c.to("words", {"y": new_cy - cy, "scale": shrink, "duration": 0.6, "ease": "power3.inOut"}, t_first - 0.35)
    c.to("disc", {"y": new_cy - cy + 120, "scale": 0.85, "opacity": 0.6, "duration": 0.6, "ease": "power3.inOut"}, t_first - 0.35)
    grid_top = new_cy + int(block_h * shrink / 2) + 70
    cw, ch_h, gap = 452, 128, 24
    left = X0 + (SW - (2 * cw + 40)) // 2
    for i, chip in enumerate(chips):
        col, row = i % 2, i // 2
        x = left + col * (cw + 40) if len(chips) > 1 else 540 - cw // 2
        y = grid_top + row * (ch_h + gap)
        name = f"chip{i}"
        sub = f'<div style="font-size:32px; color:{MUTED}; font-weight:400; margin-top:4px;">{esc(chip.get("sub"))}</div>' if chip.get("sub") else ""
        c.rule(name, f"position:absolute; left:{x}px; top:{y}px; width:{cw}px; height:{ch_h}px; border-radius:22px; background:#fff;"
                     f"border:1.5px solid {BORDER}; display:flex; flex-direction:column; justify-content:center; padding:0 34px;"
                     f"font-size:{fit(str(chip['text']), 48, cw - 70, 34)}px; font-weight:700; white-space:nowrap;")
        c.add(f'<div id="{c.id(name)}">{esc(chip["text"])}{sub}</div>')
        c.rise(name, max(t_first, cue(chip.get("cue"), t_first + 0.55 * i)), dy=30, d=0.45)
    if s.get("box"):
        rows = (len(chips) + 1) // 2
        bh = rows * (ch_h + gap) - gap + 80
        by = grid_top - 40
        c.rule("box", f"position:absolute; left:{X0 - 12}px; top:{by}px; width:{SW + 24}px; height:{bh}px; border-radius:30px;"
                      f"border:3px solid {PRIMARY}; background:rgba(15,118,110,0.05);")
        c.add(f'<div id="{c.id("box")}"></div>')
        pill(c, "boxlabel", s["box"], 540, by - 34, size=40, solid=True, anchor="center")
        t_box = cue(s.get("box_cue"), max(t_first + 1.2, c.dur - 1.6))
        c.tw("box", {"opacity": 0, "scale": 1.06}, {"opacity": 1, "scale": 1, "duration": 0.5, "ease": "power3.out"}, t_box)
        c.pop("boxlabel", t_box + 0.15)


def points(c: Comp, s: dict, cue) -> None:
    """Headline plus 1–4 cards that arrive one by one as the voice names them."""
    bottom = head(c, s, TOP + 8, cue(s.get("title_cue"), 0.0))
    items = [x for x in (s.get("items") or []) if str(x.get("text", "")).strip()][:4]
    n = len(items)
    if not n:
        return
    gap = 26
    footer_size = fit(s["footer"], 84, min_px=52) if s.get("footer") else 0
    footer_h = int(footer_size * 1.3) + 30 if footer_size else 0
    room = BOTTOM - bottom - 40 - footer_h
    card_h = min(156 if any(i.get("sub") for i in items) else 124, (room - (n - 1) * gap) // n)
    show_sub = card_h >= 140
    total = n * card_h + (n - 1) * gap
    y = bottom + 40 + max(0, (room - total) // 2)
    for i, item in enumerate(items):
        name = f"item{i}"
        text = str(item["text"])
        sub = f'<div style="font-size:36px; color:{MUTED}; font-weight:400; margin-top:6px; white-space:nowrap;">{esc(item.get("sub"))}</div>' if item.get("sub") and show_sub else ""
        c.rule(name, f"position:absolute; left:{X0}px; top:{y}px; width:{SW}px; height:{card_h}px; border-radius:24px; background:#fff;"
                     f"border:1.5px solid {BORDER}; display:flex; align-items:center; gap:30px; padding:0 40px;")
        badge = (f'<div style="flex:0 0 auto; width:66px; height:66px; border-radius:50%; background:{TINT2}; color:{PRIMARY};'
                 f'font-size:30px; font-weight:800; line-height:66px; text-align:center;">{i + 1:02d}</div>')
        c.add(f'<div id="{c.id(name)}">{badge}<div><div style="font-size:{fit(text, 52, SW - 200, 36)}px; font-weight:700; white-space:nowrap;">'
              f'{accent_html(text, item.get("accent", ""))}</div>{sub}</div></div>')
        default = 0.8 + (c.dur - 2.2) * i / max(1, n)
        c.tw(name, {"opacity": 0, "x": 70}, {"opacity": 1, "x": 0, "duration": 0.55, "ease": "power3.out"}, cue(item.get("cue"), default))
        y += card_h + gap
    if s.get("footer"):
        size = footer_size
        c.rule("footer", f"position:absolute; left:{X0}px; top:{min(y + 4, BOTTOM - int(size * 1.3))}px; width:{SW}px; text-align:center;"
                         f"font-size:{size}px; font-weight:800; color:{PRIMARY}; white-space:nowrap;")
        c.add(f'<div id="{c.id("footer")}">{esc(s["footer"])}</div>')
        c.pop("footer", cue(s.get("footer_cue"), c.dur - 1.6))


def _window_geometry(iw: int, ih: int, top: int, bottom: int, max_w: int = SW) -> tuple[int, int, int, int, float]:
    scale = min(max_w / iw, (bottom - top) / ih)
    ww, wh = int(iw * scale), int(ih * scale)
    return 540 - ww // 2, top + max(0, ((bottom - top) - wh) // 4), ww, wh, scale


def shot(c: Comp, s: dict, cue) -> None:
    """A real screenshot in a window card; the camera pushes into one region on its cue."""
    t_title = cue(s.get("title_cue"), 0.0)
    y_title = TOP + 8
    bottom = head(c, s, y_title, t_title)
    img = s.get("image") or {}
    iw, ih = int(img.get("w") or 0), int(img.get("h") or 0)
    if not (iw and ih and img.get("src")):
        return
    label_room = 110 if s.get("label") else 20
    wx, wy, ww, wh, s0 = _window_geometry(iw, ih, bottom + 44, BOTTOM - label_room)
    c.rule("win", f"position:absolute; left:{wx}px; top:{wy}px; width:{ww}px; height:{wh}px; border-radius:26px; overflow:hidden;"
                  f"background:#fff; border:1.5px solid {BORDER};")
    c.rule("img", f"position:absolute; left:0; top:0; width:{iw}px; height:{ih}px; transform-origin:0 0; max-width:none;")
    c.add(f'<div id="{c.id("win")}"><img id="{c.id("img")}" src="{esc(img["src"])}" alt="" /></div>')
    c.set("img", {"scale": s0, "x": 0, "y": 0}, 0)
    c.tw("win", {"opacity": 0, "y": 80}, {"opacity": 1, "y": 0, "duration": 0.65, "ease": "power3.out"}, max(0.0, t_title + 0.15))
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
            # ring around the focused region, in window coordinates after the push
            rx, ry = fx0 * iw * s1 + tx, fy0 * ih * s1 + ty
            rw2, rh2 = rw * s1, rh * s1
            pad = 10
            rx, ry = max(6, rx - pad), max(6, ry - pad)
            rw2, rh2 = min(ww - rx - 6, rw2 + 2 * pad), min(wh - ry - 6, rh2 + 2 * pad)
            if s.get("ring", True) and rw2 > 40 and rh2 > 30:
                c.rule("ring", f"position:absolute; left:{wx + rx:.0f}px; top:{wy + ry:.0f}px; width:{rw2:.0f}px; height:{rh2:.0f}px;"
                               f"border:5px solid {PRIMARY}; border-radius:16px;")
                c.add(f'<div id="{c.id("ring")}"></div>')
                c.tw("ring", {"opacity": 0, "scale": 1.06}, {"opacity": 1, "scale": 1, "duration": 0.4, "ease": "power3.out"}, t_focus + 0.85)
    for i, b in enumerate([b for b in (s.get("badges") or []) if str(b.get("text", "")).strip()][:2]):
        name = f"badge{i}"
        pill(c, name, b["text"], wx + ww - 10 - i * 0, wy - 30 + i * 96, size=40, anchor="right")
        c.pop(name, cue(b.get("cue"), 1.0 + i * 0.8))
    if s.get("label"):
        pill(c, "label", s["label"], 540, wy + wh + 28, size=40, solid=True, anchor="center")
        c.pop("label", cue(s.get("label_cue"), c.dur - 1.8))
    if s.get("title2"):
        lines_before = len(lines_of(s.get("title"), 104, 76))
        swap_title(c, s, y_title, bottom, cue(s.get("title2_cue"), c.dur * 0.5), lines_before)


def _odometer(c: Comp, name: str, value: str, x_center: int, y: int, size: int, t: float, spin: float = 1.1) -> None:
    """Rolling digit columns (pure transforms, so any seek shows the right digits)."""
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
    c.rule(name, f"position:absolute; left:{left}px; top:{y}px; width:{width}px; height:{size}px; font-size:{size}px; font-weight:800;"
                 f"color:{PRIMARY}; font-variant-numeric:tabular-nums;")
    c.add(f'<div id="{c.id(name)}">{"".join(parts)}</div>')
    c.tw(name, {"opacity": 0, "scale": 0.9}, {"opacity": 1, "scale": 1, "duration": 0.4, "ease": "power3.out"}, t)
    for j, (cid, dy) in enumerate(ids):
        c.tw(cid, {"y": 0}, {"y": dy, "duration": spin + 0.12 * (len(ids) - j), "ease": "power3.out"}, t)


def stat(c: Comp, s: dict, cue) -> None:
    """One number counts up on its cue; optionally it then turns into a second number."""
    bottom = head(c, s, TOP + 8, cue(s.get("title_cue"), 0.0))
    value = str(s.get("value") or "").strip()
    if not value:
        return
    size = min(260, int(SW * 0.9 / max(1, len(value) * 0.6)))
    y = max(bottom + 90, 640 - size // 2)
    t_value = cue(s.get("value_cue"), 0.9)
    _odometer(c, "num", value, 540, y, size, t_value)
    unit_y = y + size + 30
    if s.get("unit"):
        c.rule("unit", f"position:absolute; left:{X0}px; top:{unit_y}px; width:{SW}px; text-align:center; font-size:52px; font-weight:700;")
        c.add(f'<div id="{c.id("unit")}">{esc(s["unit"])}</div>')
        c.rise("unit", t_value + 0.3, dy=20, d=0.45)
    then = str(s.get("then") or "").strip()
    if then:
        t_then = cue(s.get("then_cue"), t_value + 2.2)
        c.to(["num"] + (["unit"] if s.get("unit") else []), {"opacity": 0, "y": -40, "filter": "blur(8px)", "duration": 0.35, "ease": "power2.in"}, t_then - 0.3)
        _odometer(c, "num2", then, 540, y, size, t_then, spin=0.9)
        if s.get("then_unit"):
            c.rule("unit2", f"position:absolute; left:{X0}px; top:{unit_y}px; width:{SW}px; text-align:center; font-size:52px; font-weight:700; color:{PRIMARY};")
            c.add(f'<div id="{c.id("unit2")}">{esc(s["then_unit"])}</div>')
            c.rise("unit2", t_then + 0.3, dy=20, d=0.45)
    if s.get("sub"):
        c.rule("sub", f"position:absolute; left:{X0}px; top:{unit_y + 100}px; width:{SW}px; text-align:center; font-size:42px; color:{MUTED};")
        c.add(f'<div id="{c.id("sub")}">{esc(s["sub"])}</div>')
        c.rise("sub", cue(s.get("sub_cue"), c.dur - 2.0), dy=20, d=0.45)


def change(c: Comp, s: dict, cue) -> None:
    """Before → after rows: the old line is struck through and the new one types over it."""
    bottom = head(c, s, TOP + 8, cue(s.get("title_cue"), 0.0))
    rows = [r for r in (s.get("rows") or []) if str(r.get("after", "")).strip()][:3]
    footer_size = fit(s["footer"], 96, min_px=56) if s.get("footer") else 0
    room = BOTTOM - bottom - 50 - (int(footer_size * 1.3) + 40 if footer_size else 0)
    gap = 26
    row_h = max(150, min(176, (room - (len(rows) - 1) * gap) // max(1, len(rows))))
    y = bottom + 50
    for i, r in enumerate(rows):
        name = f"row{i}"
        before, after = str(r.get("before", "")), str(r["after"])
        bsize = fit(before, 42, SW - 80, 28)
        asize = fit(after, 46, SW - 80, 30)
        c.rule(name, f"position:absolute; left:{X0}px; top:{y}px; width:{SW}px; height:{row_h}px; border-radius:24px; background:#fff;"
                     f"border:1.5px solid {BORDER}; padding:22px 36px;")
        tag = (f'<div style="display:inline-block; height:46px; padding:0 18px; border-radius:100px; background:{TINT2}; color:{PRIMARY};'
               f'font-size:28px; font-weight:700; line-height:46px;">{esc(r.get("label") or "改")}</div>') if r.get("label") is not None else ""
        c.add(f'<div id="{c.id(name)}">{tag}<div style="position:relative; height:70px; margin-top:12px;">'
              f'<div id="{c.id(name + "-b")}" style="position:absolute; left:0; top:8px; font-size:{bsize}px; color:{MUTED}; white-space:nowrap;">{esc(before)}'
              f'<div id="{c.id(name + "-x")}" style="position:absolute; left:-4px; right:-4px; top:52%; height:4px; background:{RED}; transform-origin:0 50%;"></div></div>'
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
        c.rule("footer", f"position:absolute; left:{X0}px; top:{min(y + 30, BOTTOM - int(size * 1.3))}px; width:{SW}px; text-align:center;"
                         f"font-size:{size}px; font-weight:800; white-space:nowrap;")
        c.add(f'<div id="{c.id("footer")}">{accent_html(s["footer"], s.get("footer_accent", ""))}</div>')
        c.pop("footer", cue(s.get("footer_cue"), c.dur - 2.0))


def quote(c: Comp, s: dict, cue) -> None:
    """The calm beat: one sentence, big, with a highlighter sweep on the key word."""
    if s.get("lead"):
        c.rule("lead", f"position:absolute; left:{X0}px; top:{TOP + 120}px; width:{SW}px; text-align:center; font-size:44px; color:{MUTED};"
                       f"line-height:1.5;")
        c.add(f'<div id="{c.id("lead")}">{esc(s["lead"])}</div>')
        c.rise("lead", cue(s.get("lead_cue"), 0.0), dy=20, d=0.5)
    lines = [str(x) for x in (s.get("lines") or []) if str(x).strip()][:4] or ["…"]
    size = min(fit(line, 116, min_px=64) for line in lines)
    lh = int(size * 1.22)
    total = len(lines) * lh
    top = 730 - total // 2
    for r, (sz, op) in enumerate(((460, 0.16), (700, 0.12), (940, 0.08))):
        name = f"ring{r}"
        c.rule(name, f"position:absolute; left:{540 - sz // 2}px; top:{730 - sz // 2}px; width:{sz}px; height:{sz}px; border-radius:50%;"
                     f"border:2px solid rgba(15,118,110,{op}); {'background:' + TINT + ';' if r == 0 else ''}")
        c.add(f'<div id="{c.id(name)}"></div>')
    first = cue(s.get("lines_cue", [None])[0] if isinstance(s.get("lines_cue"), list) else None, 0.0 if not s.get("lead") else c.dur * 0.35)
    if s.get("lead"):
        c.to("lead", {"opacity": 0, "y": -40, "duration": 0.45, "ease": "power2.in"}, first - 0.3)
    c.tw([f"ring{r}" for r in range(3)], {"opacity": 0, "scale": 0.92}, {"opacity": 1, "scale": 1, "duration": 1.2, "ease": "power2.out", "stagger": 0.1}, first)
    accent = s.get("accent", "")
    cues = s.get("lines_cue") if isinstance(s.get("lines_cue"), list) else []
    hl_line = -1
    for i, line in enumerate(lines):
        name = f"line{i}"
        body = esc(line)
        if accent and accent in line:
            a, b = line.split(accent, 1)
            body = (f'{esc(a)}<span style="position:relative; display:inline-block;">'
                    f'<span id="{c.id("hl")}" style="position:absolute; left:-10px; right:-10px; top:52%; bottom:6%; background:{HL};'
                    f'border-radius:10px; transform-origin:0 50%; z-index:0;"></span><span style="position:relative; z-index:1; color:{PRIMARY};">'
                    f'{esc(accent)}</span></span>{esc(b)}')
            accent = ""  # only once
            hl_line = i
        c.rule(name, f"position:absolute; left:{X0}px; top:{top + i * lh}px; width:{SW}px; text-align:center; font-size:{size}px;"
                     f"line-height:{lh}px; font-weight:800; white-space:nowrap;")
        c.add(f'<div id="{c.id(name)}">{body}</div>')
        t = cue(cues[i] if i < len(cues) else None, first + i * 1.4)
        c.tw(name, {"opacity": 0, "y": 60}, {"opacity": 1, "y": 0, "duration": 0.8, "ease": "power3.out"}, t)
        if hl_line == i:
            highlight_word(c, "hl", cue(s.get("accent_cue"), t + 0.5))


def cta(c: Comp, s: dict, cue, final: bool) -> None:
    """End card: optional repo screenshot, a typed command or link, then the signature."""
    y = TOP + 20
    img = s.get("image") or {}
    if img.get("src") and img.get("w") and img.get("h"):
        iw, ih = int(img["w"]), int(img["h"])
        scale = SW / iw
        wh = min(int(ih * scale), 420)
        c.rule("win", f"position:absolute; left:{X0}px; top:{y}px; width:{SW}px; height:{wh}px; border-radius:26px; overflow:hidden;"
                      f"background:#fff; border:1.5px solid {BORDER};")
        c.rule("img", f"position:absolute; left:0; top:0; width:{iw}px; height:{ih}px; transform-origin:0 0; max-width:none;")
        c.add(f'<div id="{c.id("win")}"><img id="{c.id("img")}" src="{esc(img["src"])}" alt="" /></div>')
        c.set("img", {"scale": round(scale, 4)}, 0)
        c.tw("win", {"opacity": 0, "y": 70}, {"opacity": 1, "y": 0, "duration": 0.6, "ease": "power3.out"}, 0.05)
        if s.get("badge"):
            pill(c, "badge", s["badge"], X1 - 10, y - 30, size=40, anchor="right")
            c.pop("badge", cue(s.get("badge_cue"), 0.9))
        y += wh + 40
    elif s.get("badge"):
        size = fit(s["badge"], 120, min_px=64)
        c.rule("badge", f"position:absolute; left:{X0}px; top:{y + 60}px; width:{SW}px; text-align:center; font-size:{size}px; font-weight:800; color:{PRIMARY};")
        c.add(f'<div id="{c.id("badge")}">{esc(s["badge"])}</div>')
        c.pop("badge", cue(s.get("badge_cue"), 0.2))
        y += 60 + size + 60
    chips = [x for x in (s.get("chips") or []) if str(x.get("text", "")).strip()][:3]
    if chips:
        x = X0
        for i, chip in enumerate(chips):
            name = f"chip{i}"
            pill(c, name, chip["text"], x, y, size=36, solid=False)
            c.pop(name, cue(chip.get("cue"), 1.6 + i * 0.6))
            x += int(cells(str(chip["text"])) * 36 + 36 * 1.4) + 20
        y += 90
    if s.get("command"):
        cmd = str(s["command"])
        size = fit(cmd, 40, SW - 140, 26)
        c.rule("term", f"position:absolute; left:{X0}px; top:{y}px; width:{SW}px; min-height:110px; border-radius:30px; background:{INK};"
                       f"color:#fff; padding:28px 40px; font-size:{size}px; line-height:1.5; white-space:nowrap;")
        c.add(f'<div id="{c.id("term")}"><span style="color:#7dd3c7;">$ </span><span id="{c.id("cmd")}" style="display:inline-block;">{esc(cmd)}</span></div>')
        t_cmd = cue(s.get("command_cue"), 1.6)
        c.tw("term", {"opacity": 0, "scale": 0.94}, {"opacity": 1, "scale": 1, "duration": 0.4, "ease": "power3.out"}, t_cmd)
        c.tw("cmd", {"clipPath": "inset(0% 100% 0% 0%)"}, {"clipPath": "inset(0% 0% 0% 0%)", "duration": min(2.2, 0.04 * len(cmd) + 0.4), "ease": "none"}, t_cmd + 0.3)
        y += 150
    if s.get("sign"):
        t_sign = cue(s.get("sign_cue"), max(c.dur * 0.6, c.dur - 3.2))
        dim = [n for n in ("win", "badge", "term") if f'id="{c.id(n)}"' in "".join(c.html)] + [f"chip{i}" for i in range(len(chips))]
        if dim:
            c.to(dim, {"opacity": 0.28, "duration": 0.5, "ease": "power2.out"}, t_sign - 0.2)
        sy = max(y + 40, 880)
        size = fit(s["sign"], 112, min_px=64)
        c.rule("sign", f"position:absolute; left:{X0}px; top:{sy}px; width:{SW}px; text-align:center; font-size:{size}px; font-weight:800; white-space:nowrap;")
        c.add(f'<div id="{c.id("sign")}">{accent_html(s["sign"], s.get("sign_accent", ""))}</div>')
        c.rise("sign", t_sign, dy=40)
        if s.get("sign_sub"):
            c.rule("signsub", f"position:absolute; left:{X0}px; top:{sy + int(size * 1.35)}px; width:{SW}px; text-align:center; font-size:44px; color:{MUTED};")
            c.add(f'<div id="{c.id("signsub")}">{esc(s["sign_sub"])}</div>')
            c.rise("signsub", cue(s.get("sign_sub_cue"), t_sign + 0.6), dy=20, d=0.45)
    if final:
        c.to("all", {"opacity": 0, "duration": 0.55, "ease": "power2.inOut"}, max(0.0, c.dur - 0.6))


BUILDERS = {"hook": hook, "points": points, "shot": shot, "stat": stat, "change": change, "quote": quote}


def scene_html(sid: str, scene: dict, duration: float, cue, final: bool = False) -> str:
    c = Comp(sid, duration)
    kind = scene.get("type")
    if kind == "cta":
        cta(c, scene, cue, final)
    else:
        BUILDERS.get(kind, points)(c, scene, cue)
    return c.build()


# ------------------------------------------------------------------ captions and the author mark

def captions_html(groups: list[dict], total: float) -> str:
    """One caption line at a time; the word being spoken sits on a teal chip."""
    css = f"""{FONT_FACES}
#root {{ position: absolute; inset: 0; pointer-events: none; font-family: "Noto Sans SC", sans-serif; }}
.cap-g {{ position: absolute; left: 0; right: 0; top: {CAP_TOP}px; height: {CAP_H}px; display: flex; align-items: center;
  justify-content: center; opacity: 0; }}
.cap-p {{ max-width: 86%; padding: 16px 34px 20px; background: #fff; border: 2px solid {BORDER}; border-radius: 22px;
  display: flex; flex-wrap: wrap; justify-content: center; font-size: 60px; font-weight: 700; line-height: 1.25; letter-spacing: 0; }}
.cap-w {{ padding: 0 0.04em; border-radius: 0.22em; color: #8b8b8b; white-space: pre; }}"""
    body, js = [], []
    for gi, g in enumerate(groups):
        words = "".join(f'<span id="cap-{gi}-{wi}" class="cap-w">{esc(w["text"])}</span>' for wi, w in enumerate(g["words"]))
        body.append(f'<div id="cap-{gi}" class="cap-g"><div class="cap-p">{words}</div></div>')
        nxt = groups[gi + 1]["start"] if gi + 1 < len(groups) else total
        end = min(nxt, g["end"] + 0.3)
        js.append(f'tl.set("#cap-{gi}", {{ opacity: 1 }}, {g["start"]:.3f}); tl.set("#cap-{gi}", {{ opacity: 0 }}, {max(g["start"] + 0.05, end):.3f});')
        for wi, w in enumerate(g["words"]):
            js.append(f'tl.set("#cap-{gi}-{wi}", {{ color: "#ffffff", backgroundColor: "{PRIMARY}" }}, {w["start"]:.3f});'
                      f'tl.set("#cap-{gi}-{wi}", {{ color: "{INK}", backgroundColor: "rgba(0,0,0,0)" }}, {max(w["start"] + 0.05, w["end"]):.3f});')
    return f"""<template>
  {GSAP}
  <style>
{css}
  </style>
  <div id="root" data-composition-id="captions" data-width="{W}" data-height="{H}" data-duration="{total:.3f}">
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


def chrome_html(author: str, tagline: str, total: float) -> str:
    sub = f'<div id="chrome-sub">{esc(tagline)}</div>' if tagline else ""
    return f"""<template>
  {GSAP}
  <style>
{FONT_FACES}
#root {{ position: absolute; inset: 0; pointer-events: none; font-family: "Noto Sans SC", sans-serif; }}
#chrome-mark {{ position: absolute; left: 56px; top: 116px; display: flex; align-items: center; gap: 18px; }}
#chrome-icon {{ width: 68px; height: 68px; border-radius: 18px; background: {PRIMARY}; display: flex; align-items: center; justify-content: center; }}
#chrome-name {{ font-size: 36px; font-weight: 700; color: {INK}; line-height: 1.15; }}
#chrome-sub {{ font-size: 28px; font-weight: 400; color: {MUTED}; line-height: 1.15; }}
  </style>
  <div id="root" data-composition-id="chrome" data-width="{W}" data-height="{H}" data-duration="{total:.3f}">
    <div id="chrome-mark">
      <div id="chrome-icon"><svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg></div>
      <div><div id="chrome-name">{esc(author)}</div>{sub}</div>
    </div>
  </div>
  <script>
    (function () {{
      const tl = gsap.timeline({{ paused: true }});
      tl.fromTo("#chrome-mark", {{ opacity: 0, y: -14 }}, {{ opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }}, 0.15);
      tl.to("#chrome-mark", {{ opacity: 0, duration: 0.5, ease: "power2.inOut" }}, {max(0.0, total - 0.6):.3f});
      tl.to({{}}, {{ duration: {total:.3f} }}, 0);
      window.__timelines["chrome"] = tl;
    }})();
  </script>
</template>
"""


TRANSITION = {
    "fade": ('{ opacity: 0, duration: 0.45, ease: "power2.inOut" }', '{ opacity: 0 }', '{ opacity: 1, duration: 0.45, ease: "power2.inOut" }'),
    "push": ('{ x: -1080, duration: 0.45, ease: "power3.inOut" }', '{ x: 1080, opacity: 1 }', '{ x: 0, opacity: 1, duration: 0.45, ease: "power3.inOut" }'),
    "zoom": ('{ scale: 2.2, opacity: 0, filter: "blur(8px)", duration: 0.4, ease: "power3.in" }',
             '{ scale: 0.6, opacity: 0, filter: "blur(8px)" }', '{ scale: 1, opacity: 1, filter: "blur(0px)", duration: 0.4, ease: "power3.out" }'),
}
OVERLAP = 0.45


def transition_kind(prev: dict, cur: dict) -> str:
    if cur.get("type") in ("quote", "cta") or prev.get("type") == "hook":
        return "zoom"
    if prev.get("type") == "shot" and cur.get("type") == "shot":
        return "push"
    return "fade"


def index_html(scenes: list[dict], starts: list[float], durations: list[float], voices: list[str], bgm: str | None,
               bgm_volume: float, total: float) -> str:
    els, trans = [], []
    for i, (scene, start, dur) in enumerate(zip(scenes, starts, durations)):
        sid = f"s{i + 1:02d}"
        host = dur + (OVERLAP if i + 1 < len(scenes) else 0)
        els.append(f'<div id="el-{sid}" class="scene" data-composition-id="{sid}" data-composition-src="compositions/{sid}.html" '
                   f'data-start="{start:.3f}" data-duration="{host:.3f}" data-track-index="{i % 2}"></div>')
        els.append(f'<audio id="el-{sid}-voice" src="{voices[i]}" data-start="{start:.3f}" data-duration="{dur:.3f}" '
                   f'data-track-index="10" data-volume="1"></audio>')
        if i:
            kind = transition_kind(scenes[i - 1], scene)
            out_, in_from, in_to = TRANSITION[kind]
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
    <meta name="viewport" content="width={W}, height={H}" />
    {GSAP}
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ width: {W}px; height: {H}px; overflow: hidden; background: #000; }}
      #root {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; background: {BG}; }}
      .scene {{ position: absolute; inset: 0; width: 100%; height: 100%; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{total:.3f}" data-width="{W}" data-height="{H}">
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


def preview_html(parts: list[str], duration: float, offsets: dict[str, float]) -> str:
    """The editor's instant preview of one scene: the same compositions stacked on
    one 1080×1920 stage, scaled to the frame. The page only seeks; the editor
    calls window.cwSeek(t) from the voice it plays, so picture and sound stay in
    step. offsets shift a layer's clock (the author mark is already in place
    after the first scene)."""
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
      html, body {{ width: 100%; height: 100%; overflow: hidden; background: {BG}; }}
      #stage {{ position: absolute; left: 0; top: 0; width: {W}px; height: {H}px; overflow: hidden; background: {BG};
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
        const fit = () => {{ stage.style.transform = "scale(" + Math.min(innerWidth / {W}, innerHeight / {H}) + ")"; }};
        fit();
        addEventListener("resize", fit);
        const offsets = {json.dumps(offsets)};
        let last = {poster};
        window.cwDuration = {duration:.3f};
        window.cwPoster = {poster};
        window.cwSeek = (t) => {{
          last = t;
          for (const [id, tl] of Object.entries(window.__timelines)) tl.seek(Math.max(0, t + (offsets[id] || 0)));
        }};
        window.cwSeek(last);
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

