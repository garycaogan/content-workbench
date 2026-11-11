"""Looks for the short video. A style is everything about the picture that is
not layout: colours, fonts, cards, how screenshots are framed and their key
region marked, captions, the progress bar, a standing headline, motion and
the cut between scenes.

The four styles follow what performs in Chinese AI-creator videos (a study of
25 high-engagement Douyin, Bilibili and YouTube videos, 2026-10): dark or
gradient grounds, yellow/gold/orange accents, captions shown a phrase at a
time with one or two key words coloured, screenshots always framed and marked,
something new on screen every few seconds, and a way to tell where you are
(a progress bar or a standing headline).

The scene builders (video_scenes.py) ask the style for CSS fragments, so a new
look is a new Style here, not new scenes. Each style says what content it
suits (hint); the script writer picks one per video and the author can switch."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Face:
    family: str
    stem: str                 # file name stem under assets/fonts/
    weights: tuple[int, ...]
    source: str               # key in video.FONT_SOURCES


NOTO = Face("Noto Sans SC", "noto", (400, 700, 900), "noto-sans-sc")
SMILEY = Face("Smiley Sans", "smiley", (400,), "smiley-sans")        # 得意黑: slanted condensed heavy, the Douyin headline look
SERIF = Face("Noto Serif SC", "serif", (900,), "noto-serif-sc")


@dataclass(frozen=True)
class Style:
    key: str
    name: str
    hint: str                                  # what content it suits, for the planner and the editor
    bg: str                                    # scene background (CSS, may be a gradient)
    base: str                                  # the nearest solid colour of bg
    ink: str
    primary: str                               # UI accent: pills, numbers, focus boxes
    accent: str                                # key words in headlines and captions
    muted: str
    faint: str
    tint: str                                  # soft fill (eyebrows, boxes)
    tint2: str
    border: str
    hl: str = "#ffe14d"                        # highlighter
    red: str = "#ef4444"
    on_primary: str = "#111111"
    card: str = "#ffffff"
    card_line: str = ""
    card_shadow: str = ""
    card_blur: bool = False                    # frosted glass
    title_color: str = ""                      # "" = ink
    title_glow: str = ""
    big_glow: str = ""
    faces: tuple[Face, ...] = (NOTO,)
    display: str = ""                          # family for headlines and big words; "" = body font
    caption_font: str = ""                     # "" = body font
    title_weight: int = 900
    big_weight: int = 900
    accent_kind: str = "color"                 # color | marker
    chip_radius: str = "100px"
    backdrop: str = ""                         # "" | grid | keynote | vignette | warm
    frame_kind: str = "browser"                # browser | card: how a screenshot is framed (tall ones get a phone)
    frame_bar: str = "#2a2f45"
    frame_line: str = "1px solid rgba(255,255,255,0.14)"
    frame_shadow: str = "0 30px 80px rgba(0,0,0,0.55)"
    focus: str = "#a78bfa"                     # box around the region the camera pushes into
    focus_dashed: bool = False
    cap_color: str = "#ffffff"
    cap_key: str = "#ffd84a"
    cap_stroke: str = "rgba(10,10,20,0.92)"    # outline of caption words; "" = shadow only
    cap_plate: str = ""                        # a plate behind the caption; "" = none
    progress: str = "#ffffff"                  # colour (or gradient) of the progress bar
    progress_track: str = "rgba(255,255,255,0.14)"
    headline: bool = False                     # a standing two-line headline at the top (vertical)
    head_colors: tuple[str, str] = ("#ffffff", "#ff4d4f")
    term: str = "#05060a"
    term_prompt: str = "#ffd84a"
    mark_bg: str = ""
    mark_ink: str = ""
    mark_icon: str = "#111111"
    ease: str = "power3.out"
    pop_ease: str = "back.out(1.6)"
    big_from: dict = field(default_factory=lambda: {"opacity": 0, "scale": 1.18, "filter": "blur(12px)"})
    drift: float = 0.035                       # slow push-in over every scene, so a long one still moves
    cuts: dict = field(default_factory=dict)   # overrides of the cut between scene types

    @property
    def dark(self) -> bool:
        """A dark ground (light ink)."""
        c = self.ink.lstrip("#")
        return len(c) == 6 and sum(int(c[i:i + 2], 16) for i in (0, 2, 4)) > 384

    def __post_init__(self):
        if not self.title_color:
            object.__setattr__(self, "title_color", self.ink)
        if not self.mark_bg:
            object.__setattr__(self, "mark_bg", self.primary)
        if not self.mark_ink:
            object.__setattr__(self, "mark_ink", self.ink)

    # ---------------------------------------------------------------- fonts
    def font_faces(self) -> str:
        return "\n".join(
            f'@font-face {{ font-family: "{fc.family}"; src: url("assets/fonts/{fc.stem}-{w}.woff2") format("woff2"); '
            f'font-weight: {w}; font-style: normal; font-display: block; }}' for fc in self.faces for w in fc.weights)

    def font_stack(self) -> str:
        return f'"{self.faces[0].family}", sans-serif'

    def _family(self, name: str) -> str:
        # single quotes: this also lands inside style="…" attributes
        return f"font-family: '{name}', '{self.faces[0].family}', sans-serif;" if name else ""

    # ---------------------------------------------------------------- pieces
    def title_css(self) -> str:
        glow = f" text-shadow:{self.title_glow};" if self.title_glow else ""
        return f"{self._family(self.display)} color:{self.title_color};{glow}"

    def overlay_css(self) -> str:
        """Words laid over footage: white with a dark outline, whatever the style's ink."""
        return (f"{self._family(self.display)} color:#ffffff; -webkit-text-stroke:8px rgba(0,0,0,0.82); paint-order:stroke fill;"
                f" text-shadow:0 6px 24px rgba(0,0,0,0.5);")

    def big_css(self) -> str:
        glow = f" text-shadow:{self.big_glow};" if self.big_glow else ""
        return f"{self._family(self.display)}{glow}"

    def number_big_css(self) -> str:
        return self.big_css()

    def big_in(self) -> dict:
        return dict(self.big_from)

    def big_to(self) -> dict:
        return {k: (1 if k in ("opacity", "scale") else "blur(0px)" if k == "filter" else 0) for k in self.big_from}

    def eyebrow_css(self) -> str:
        return f"background:{self.tint2}; color:{self.primary};"

    def number_css(self) -> str:
        return f"background:{self.primary}; color:{self.on_primary};"

    def card_css(self, radius: int) -> str:
        line = self.card_line or f"1.5px solid {self.border}"
        shadow = f" box-shadow:{self.card_shadow};" if self.card_shadow else ""
        blur = " backdrop-filter:blur(20px);" if self.card_blur else ""
        return f"border-radius:{radius}px; background:{self.card}; border:{line};{shadow}{blur} color:{self.ink};"

    def frame_css(self, kind: str) -> str:
        """The box a screenshot sits in: a browser window, a phone, or a plain card."""
        if kind == "phone":
            return f"border-radius:56px; background:#0b0b0d; border:2px solid rgba(255,255,255,0.18); box-shadow:{self.frame_shadow};"
        radius = 18 if kind == "browser" else 14
        return f"border-radius:{radius}px; background:#ffffff; border:{self.frame_line}; box-shadow:{self.frame_shadow}; overflow:hidden;"

    def frame_bar_html(self, width: int, bar: int) -> str:
        dots = "".join(f'<span style="display:inline-block; width:{bar // 3}px; height:{bar // 3}px; border-radius:50%; background:{c}; margin-right:{bar // 5}px;"></span>'
                       for c in ("#ff5f57", "#febc2e", "#28c840"))
        addr = (f'<span style="position:absolute; left:{int(width * 0.26)}px; right:{int(width * 0.26)}px; top:{bar // 4}px; height:{bar // 2}px;'
                f' border-radius:{bar // 4}px; background:rgba(255,255,255,0.14);"></span>')
        return (f'<div style="position:absolute; left:0; top:0; width:{width}px; height:{bar}px; background:{self.frame_bar};'
                f' display:flex; align-items:center; padding-left:{bar // 2}px;">{dots}{addr}</div>')

    def focus_css(self) -> str:
        line = "dashed" if self.focus_dashed else "solid"
        return f"border:5px {line} {self.focus}; border-radius:14px; box-shadow:0 0 0 4px {_alpha(self.focus, 0.18)}, 0 0 30px {_alpha(self.focus, 0.35)};"

    def box_css(self) -> str:
        return f"border:3px solid {self.primary}; background:{_alpha(self.primary, 0.06)};"

    def pill_solid_css(self) -> str:
        return f"background:{self.primary}; color:{self.on_primary};"

    def pill_ghost_css(self) -> str:
        return f"background:{self.tint}; color:{self.ink}; border:2px solid {self.border};"

    def mark_word(self, text: str, color: str | None = None) -> str:
        """A key word inside a headline or big words."""
        if self.accent_kind == "marker":
            return (f'<span style="color:{color or self.ink}; background:linear-gradient(transparent 58%, {self.hl} 58%, {self.hl} 92%, transparent 92%);'
                    f' padding:0 0.06em;">{text}</span>')
        return f'<span style="color:{color or self.accent}">{text}</span>'

    def marker_html(self, hid: str, text: str) -> str:
        """The key word of a quote, with a highlighter that sweeps in (id hid)."""
        color = "#111111"  # dark on the light highlighter, whatever the ground
        band = "top:52%; bottom:6%" if self.dark is False else "top:4%; bottom:0"  # a full block on a dark ground
        return (f'<span style="position:relative; display:inline-block;">'
                f'<span id="{hid}" style="position:absolute; left:-10px; right:-10px; {band}; background:{_alpha(self.hl, 0.92)};'
                f'border-radius:10px; transform-origin:0 50%; z-index:0;"></span><span style="position:relative; z-index:1; color:{color};">'
                f'{text}</span></span>')

    def backdrop_css(self, frame, bg_id: str) -> str:
        """Depth on the scene background: a grid, a keynote glow, a vignette, a warm glow."""
        sel = f'[id="{bg_id}"]'
        w, h = frame.W, frame.H
        if self.backdrop == "grid":
            return (f"{sel} {{ background-image: linear-gradient(rgba(255,255,255,0.05) 1px, transparent 1px),"
                    f" linear-gradient(90deg, rgba(255,255,255,0.05) 1px, transparent 1px),"
                    f" radial-gradient(ellipse at 50% 40%, rgba(255,255,255,0.06), transparent 70%);"
                    f" background-size: 48px 48px, 48px 48px, 100% 100%; }}")
        if self.backdrop == "keynote":
            r = int(min(w, h) * 0.95)
            return (f"{sel}::after {{ content: ''; position: absolute; left: {int(w * 0.62 - r / 2)}px; top: {int(h * 0.30 - r / 2)}px; width: {r}px; height: {r}px;"
                    f" border-radius: 50%; border: 2px solid rgba(255,255,255,0.10); box-shadow: 0 0 120px rgba(201,166,224,0.18) inset; }}\n"
                    f"{sel}::before {{ content: ''; position: absolute; inset: 0; background: radial-gradient(circle at {int(w * 0.85)}px {int(h * 0.12)}px,"
                    f" rgba(242,184,198,0.30), transparent {int(min(w, h) * 0.55)}px); }}")
        if self.backdrop == "vignette":
            return (f"{sel}::after {{ content: ''; position: absolute; inset: 0; background: radial-gradient(ellipse at 50% 42%, transparent 45%, rgba(0,0,0,0.55) 100%); }}\n"
                    f"{sel}::before {{ content: ''; position: absolute; inset: 0; background: radial-gradient(circle at {int(w * 0.5)}px {int(h * 0.3)}px,"
                    f" rgba(255,215,106,0.10), transparent {int(min(w, h) * 0.6)}px); }}")
        if self.backdrop == "warm":
            return (f"{sel}::after {{ content: ''; position: absolute; inset: 0; background: radial-gradient(circle at {int(w * 0.5)}px {int(h * 0.38)}px,"
                    f" rgba(245,179,138,0.30), transparent {int(min(w, h) * 0.62)}px); }}")
        return ""

    # ---------------------------------------------------------------- captions: a phrase at a time, key words coloured
    def caption_css(self, frame) -> str:
        size = frame.CAP_SIZE
        stroke = (f" -webkit-text-stroke: {max(5, size // 10)}px {self.cap_stroke}; paint-order: stroke fill;" if self.cap_stroke
                  else " text-shadow: 0 2px 12px rgba(0,0,0,0.45);")
        plate = (f" padding: 10px 26px 14px; background: {self.cap_plate}; border-radius: 14px;" if self.cap_plate else "")
        return (f".cap-p {{ max-width: {frame.W - 2 * frame.CAP_SIDE}px; display: flex; flex-wrap: wrap; justify-content: center; align-items: baseline;"
                f" font-size: {size}px; font-weight: 900; line-height: 1.22; letter-spacing: 0.01em; color: {self.cap_color};"
                f" {self._family(self.caption_font)}{plate} }}\n"
                f".cap-w {{ white-space: pre;{stroke} }}\n"
                f".cap-k {{ color: {self.cap_key}; font-size: 1.08em; }}")

    # ---------------------------------------------------------------- the standing headline
    def headline_css(self) -> str:
        a, b = self.head_colors
        return (f".hd {{ position: absolute; left: 0; right: 0; text-align: center; {self._family(self.display)} font-weight: {self.title_weight};"
                f" white-space: nowrap; -webkit-text-stroke: 6px rgba(0,0,0,0.85); paint-order: stroke fill; text-shadow: 0 6px 18px rgba(0,0,0,0.45); }}\n"
                f".hd1 {{ color: {a}; }} .hd2 {{ color: {b}; }} .hd .k {{ color: {self.cap_key}; }}")

    # ---------------------------------------------------------------- cuts
    def transition(self, prev: dict, cur: dict) -> str:
        a, b = prev.get("type"), cur.get("type")
        for key in (f"{a}>{b}", f"*>{b}", f"{a}>*", "*"):
            if key in self.cuts:
                return self.cuts[key]
        if b in ("quote", "cta") or a == "hook":
            return "zoom"
        if a == "shot" and b == "shot":
            return "push"
        return "fade"


def _alpha(color: str, a: float) -> str:
    """rgba() of a #rrggbb colour."""
    c = color.lstrip("#")
    if len(c) != 6:
        return color
    return f"rgba({int(c[0:2], 16)},{int(c[2:4], 16)},{int(c[4:6], 16)},{a})"


# ------------------------------------------------------------------ the styles

KEYNOTE = Style(
    key="keynote", name="发布会",
    hint="暮色渐变底、玻璃卡片、超大白字，截图装进浏览器窗口、紫框圈重点，像产品发布会。适合工具教程、产品演示、开源项目介绍。",
    bg="linear-gradient(180deg, #1b1f45 0%, #322c5c 55%, #4b424b 100%)", base="#262449",
    ink="#ffffff", primary="#c9a6e0", accent="#ffd84a", muted="#c3bedb", faint="#8c87a8",
    tint="rgba(255,255,255,0.08)", tint2="rgba(201,166,224,0.22)", border="rgba(255,255,255,0.22)",
    card="rgba(255,255,255,0.10)", card_line="1px solid rgba(255,255,255,0.25)", card_shadow="0 20px 50px rgba(10,8,40,0.35)", card_blur=True,
    big_glow="0 0 60px rgba(201,166,224,0.35)", backdrop="keynote",
    frame_kind="browser", frame_bar="#23264a", focus="#a78bfa",
    progress="linear-gradient(90deg, #a78bfa, #f2b8c6)", mark_icon="#1b1f45",
    big_from={"opacity": 0.35, "scale": 1.12, "filter": "blur(14px)"},
)

NEWS = Style(
    key="news", name="头条快剪",
    hint="近黑底加细网格，顶部常驻两行粗斜体大标题，截图做成白卡、红框圈重点，节奏快。适合 AI 新闻、新模型发布、观点和热点评论、带数字的案例、单个开源项目。",
    bg="#0d0d10", base="#0d0d10",
    ink="#f5f5f5", primary="#ff4d4f", accent="#ffd84a", muted="#a1a1aa", faint="#62626b",
    tint="rgba(255,255,255,0.06)", tint2="rgba(255,77,79,0.18)", border="rgba(255,255,255,0.12)", on_primary="#ffffff",
    card="#18181c", card_line="1px solid rgba(255,255,255,0.10)", card_shadow="0 18px 40px rgba(0,0,0,0.5)",
    faces=(NOTO, SMILEY), display="Smiley Sans", caption_font="Smiley Sans", title_weight=400, big_weight=400,
    backdrop="grid", frame_kind="card", frame_line="0", frame_shadow="0 20px 50px rgba(0,0,0,0.6)", focus="#ff3b30",
    headline=True, head_colors=("#ffffff", "#ff4d4f"),
    progress="#ff4d4f", mark_icon="#ffffff",
    ease="expo.out", pop_ease="back.out(2)", big_from={"opacity": 1, "scale": 1.25},
    cuts={"*>hook": "cut", "*>quote": "zoom", "*": "push"},
)

LIST = Style(
    key="list", name="暗色清单",
    hint="深紫灰底加暗角、金色发光标题、半透明玻璃条目逐条亮起、底部渐变进度条。适合盘点清单（几个工具、几个技巧、几个开源项目）、步骤多的教程。",
    bg="linear-gradient(180deg, #19171f 0%, #262431 100%)", base="#1f1d27",
    ink="#ffffff", primary="#ffd76a", accent="#ffd76a", muted="#5ad1e6", faint="#7b7790",
    tint="rgba(255,255,255,0.06)", tint2="rgba(224,112,80,0.22)", border="rgba(255,255,255,0.10)",
    card="rgba(255,255,255,0.06)", card_line="1px solid rgba(255,255,255,0.10)", card_blur=True,
    title_glow="0 0 24px rgba(255,200,80,0.22)", big_glow="0 0 30px rgba(255,200,80,0.40)",
    backdrop="vignette", frame_kind="browser", frame_bar="#2a2733", focus="#fb923c", focus_dashed=True,
    cap_color="#ffd84a", cap_key="#ff9a3c", cap_stroke="",
    progress="linear-gradient(90deg, #60a5fa, #a78bfa, #f472b6)", mark_icon="#19171f",
)

PAPER = Style(
    key="paper", name="暖白杂志",
    hint="暖白底加橙色柔光、粗衬线大字、白卡大阴影、橙色序号，干净有质感。适合观点、方法论、复盘、经验总结、读书笔记。",
    bg="#f9f6f3", base="#f9f6f3",
    ink="#111111", primary="#f97316", accent="#f97316", muted="#77716b", faint="#b8b2ab",
    tint="rgba(249,115,22,0.07)", tint2="rgba(249,115,22,0.14)", border="rgba(17,17,17,0.10)", on_primary="#ffffff",
    card="#ffffff", card_line="1px solid rgba(17,17,17,0.04)", card_shadow="0 20px 60px rgba(0,0,0,0.08)",
    faces=(NOTO, SERIF), display="Noto Serif SC", accent_kind="marker", hl="#fdba74",
    backdrop="warm", frame_kind="card", frame_line="1px solid rgba(17,17,17,0.06)", frame_shadow="0 24px 70px rgba(60,40,20,0.16)",
    focus="#f97316", cap_color="#111111", cap_key="#ea580c", cap_stroke="rgba(255,255,255,0.95)",
    progress="#f97316", progress_track="rgba(17,17,17,0.08)", mark_icon="#ffffff", mark_bg="#111111",
    big_from={"opacity": 0, "y": 40}, drift=0.025,
    cuts={"*": "fade"},
)

STYLES: dict[str, Style] = {s.key: s for s in (KEYNOTE, NEWS, LIST, PAPER)}
DEFAULT = KEYNOTE


def style_for(key: str | None) -> Style:
    return STYLES.get(key or "", DEFAULT)
