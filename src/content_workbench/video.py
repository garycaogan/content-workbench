"""Short vertical video (抖音 / 视频号 / 小红书) of a long-form draft.

Two steps, like the 小红书 version: the AI writes a scene-by-scene script (what
is said, what is on screen, which screenshot and which part of it), the author
edits it, then 出片 turns it into an MP4 on this machine:

  voice    Evolink's Chinese TTS, one clip per scene, cached by text
  timing   local Whisper (when installed) finds when each word is said, so
           captions and on-screen pieces land on the spoken word
  picture  each scene becomes a HyperFrames composition (video_scenes.py), the
           HyperFrames CLI renders the whole thing with headless Chrome

Everything is stored on the draft row (drafts.video) like drafts.xhs."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import db, images, llm, prompts, video_scenes as vs
from .comments import plain
from .config import Settings, data_dir

HYPERFRAMES = "hyperframes@0.8.164"
TTS_MODEL = "qwen-audio-3.1-tts-flash"
WHISPER = "mlx-community/whisper-large-v3-turbo"
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36"}

VOICES = [
    {"id": "longanyang_v3.1", "name": "阳光男声", "desc": "年轻、有活力，像科技博主口播"},
    {"id": "longanlang_v3.1", "name": "利落男声", "desc": "清爽，语速快"},
    {"id": "xunanchuan_v3.1", "name": "沉稳男声", "desc": "干净理智，偏知识博主"},
    {"id": "longanzhi_v3.1", "name": "轻熟男声", "desc": "偏低沉，有磁性"},
    {"id": "anmingyuan_v3.1", "name": "清亮男声", "desc": "偏少年感"},
    {"id": "longanhuan_v3.1", "name": "温柔女声", "desc": "自然、亲切"},
]
INSTRUCTION = "像年轻的科技自媒体博主在短视频里口播，语气自然松弛、有点兴奋，语速偏快，不要播音腔。"
LEAD_S, TAIL_S = 0.15, 0.35
TYPES = ("hook", "points", "shot", "stat", "change", "quote", "cta")
TYPE_NAMES = {"hook": "开头大字", "points": "要点卡片", "shot": "截图放大", "stat": "数字", "change": "前后对比",
              "quote": "金句", "cta": "结尾"}
SAY_MAX = 160
PUNCT = "，。？！、：；,.?!:;—…“”\"'（）()《》「」【】 \n"
# a render and an editor preview can both reach for Whisper or the font cache
_WHISPER_LOCK, _FONT_LOCK = threading.Lock(), threading.Lock()


class VideoError(RuntimeError):
    pass


# ------------------------------------------------------------------ state

def load(draft: dict) -> dict:
    return db.loads(draft.get("video"), {}) or {}


def save(draft_id: int, state: dict) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE drafts SET video=? WHERE id=?", (json.dumps(state, ensure_ascii=False), draft_id))


def update(draft_id: int, **fields) -> dict:
    draft = db.row("SELECT video FROM drafts WHERE id=?", (draft_id,))
    state = load(draft or {})
    state.update(fields)
    save(draft_id, state)
    return state


def work_dir(draft_id: int) -> Path:
    return data_dir() / "video-work" / f"draft-{draft_id}"


def video_path(draft_id: int) -> Path:
    return data_dir() / "videos" / f"draft-{draft_id}.mp4"


# ------------------------------------------------------------------ what the voice actually reads

DIGITS = "零一二三四五六七八九"


def num2zh(n: int) -> str:
    if n == 0:
        return "零"
    out, zero = "", False
    for unit, name in ((10000, "万"), (1000, "千"), (100, "百"), (10, "十"), (1, "")):
        q, n = divmod(n, unit)
        if q:
            if zero:
                out += "零"
                zero = False
            out += DIGITS[q] + name
        elif out:
            zero = True
    return out[1:] if out.startswith("一十") else out


SPEAK = re.compile(r"(?i)\bclaude\b|(?<![\w./:-])(\d{4})(?=年)|(?<![\w./:-])(\d{1,3})%"
                   r"|(?<![\w./:-])(\d{1,2}):(\d{2})(?![\w./:])|(?<![\w./:#-])\d{1,5}(?![\w./%:])")


def speakable(text: str) -> tuple[str, list[tuple[int, int, int, int]]]:
    """The text the TTS gets (Claude → 克劳德, 866 → 八百六十六) and the replaced
    spans as (display start, display end, spoken start, spoken end)."""
    out, segs, last = [], [], 0
    pos = 0
    for m in SPEAK.finditer(text):
        word = m.group(0)
        if word.lower() == "claude":
            say = "克劳德"
        elif m.group(1):
            say = "".join(DIGITS[int(d)] for d in m.group(1))
        elif m.group(2):
            say = "百分之" + num2zh(int(m.group(2)))
        elif m.group(3):  # 15:36 → 十五点三十六分
            minute = int(m.group(4))
            say = num2zh(int(m.group(3))) + "点" + ("零" + num2zh(minute) if 0 < minute < 10 else num2zh(minute) if minute else "") + ("分" if minute else "")
        else:
            say = num2zh(int(word))
        out.append(text[last:m.start()])
        pos += m.start() - last
        segs.append((m.start(), m.end(), pos, pos + len(say)))
        out.append(say)
        pos += len(say)
        last = m.end()
    out.append(text[last:])
    return "".join(out), segs


def disp_to_spoken(i: int, segs) -> int:
    shift = 0
    for d0, d1, s0, s1 in segs:
        if i < d0:
            break
        if i < d1:
            return s0
        shift = s1 - d1
    return i + shift


def spoken_to_disp(j: int, segs) -> int:
    shift = 0
    for d0, d1, s0, s1 in segs:
        if j < s0:
            break
        if j < s1:
            return d0
        shift = d1 - s1
    return j + shift


def disp_span(a: int, b: int, segs) -> tuple[int, int]:
    """Display span of the spoken span [a, b); a replaced word is never cut in half."""
    d0 = spoken_to_disp(a, segs)
    last = b - 1
    for sd0, sd1, s0, s1 in segs:
        if s0 <= last < s1:
            return d0, sd1
    return d0, spoken_to_disp(last, segs) + 1


# ------------------------------------------------------------------ voice

def evolink_key(settings: Settings) -> str:
    path = Path(settings.evolink_key_file).expanduser() if settings.evolink_key_file else None
    if not path or not path.is_file():
        raise VideoError("还没配置配音用的 Evolink key（设置里的 evolink_key_file）")
    return path.read_text(encoding="utf-8").strip()


def _ev(key: str, method: str, path: str, body=None):
    req = urllib.request.Request("https://api.evolink.ai/v1" + path,
                                 data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
                                 method=method, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", **UA})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


class VoiceExpired(VideoError):
    pass


def _task(key: str, task: dict, what: str, tries: int = 150) -> dict:
    st = task
    for _ in range(tries):
        time.sleep(2)
        st = _ev(key, "GET", f"/tasks/{task['id']}")
        if st.get("status") in ("completed", "failed", "cancelled"):
            break
    if st.get("status") != "completed":
        err = json.dumps(st.get("error"), ensure_ascii=False)[:200]
        raise (VoiceExpired if "expired" in err else VideoError)(f"{what}失败：{err}")
    return st


def synth(key: str, text: str, voice: str, out: Path, language: str = "") -> None:
    body = {"model": TTS_MODEL, "prompt": text, "voice": voice, "instruction": INSTRUCTION, "response_format": "wav"}
    if language:
        body["language"] = language
    try:
        task = _ev(key, "POST", "/audios/generations", body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise (VoiceExpired if exc.code == 404 or "expired" in detail else VideoError)(f"配音失败：{exc.code} {detail}") from None
    st = _task(key, task, "配音")
    url = st["results"][0]
    url = url if isinstance(url, str) else url["url"]
    out.write_bytes(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read())


# ------------------------------------------------------------------ the author's own voice
#
# Evolink clones a voice from one recording (voice-enrollment). A cloned voice
# lives 6 hours and using it does not renew it, so the settings keep the
# recording, not the voice: the current voice id is kept beside the data with
# its age and cloned again when it is about to expire or the TTS says it has.
# Cloning takes a public link to the recording; an uploaded file lives 72 hours
# and is reused for re-cloning until then, so the recording is uploaded rarely.

CLONE = "clone"
CLONE_TTL = 5.5 * 3600
UPLOAD_TTL = 70 * 3600
_CLONE_LOCK = threading.Lock()
_digests: dict[tuple, str] = {}


def clone_ref(settings: Settings) -> Path | None:
    path = Path(settings.video_clone_ref).expanduser() if settings.video_clone_ref else None
    return path if path and path.is_file() else None


def _digest(path: Path) -> str:
    st = path.stat()
    k = (str(path), st.st_mtime, st.st_size)
    if k not in _digests:
        _digests[k] = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    return _digests[k]


def voice_key(settings: Settings, voice: str) -> str:
    """What the voice cache is keyed on: the preset's id, or for the cloned voice
    the recording itself (the clone's id changes every few hours, the voice doesn't)."""
    if voice != CLONE:
        return voice
    ref = clone_ref(settings)
    return f"clone-{_digest(ref)}" if ref else CLONE


def _upload(key: str, path: Path) -> str:
    import httpx
    with path.open("rb") as fh:
        r = httpx.post("https://files-api.evolink.ai/api/v1/files/upload/stream", headers={"Authorization": "Bearer " + key, **UA},
                       files={"file": (f"ref{path.suffix}", fh, "audio/wav")}, timeout=120)
    r.raise_for_status()
    data = r.json()
    url = (data.get("data") or data).get("file_url")
    if not url:
        raise VideoError("上传参考录音失败：" + json.dumps(data, ensure_ascii=False)[:200])
    return url


def cloned_voice(settings: Settings, key: str, fresh: bool = False) -> str:
    """The provider's id for the author's cloned voice, cloning again when needed."""
    ref = clone_ref(settings)
    if not ref:
        raise VideoError("还没配置复刻音色用的录音（设置里的 video_clone_ref）")
    path = data_dir() / "video" / "clone.json"
    digest = _digest(ref)
    with _CLONE_LOCK:
        st = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if st.get("digest") != digest:
            st = {"digest": digest}
        now = time.time()
        if not fresh and st.get("voice") and now - st.get("voice_at", 0) < CLONE_TTL:
            return st["voice"]
        if not st.get("url") or now - st.get("url_at", 0) > UPLOAD_TTL:
            st.update(url=_upload(key, ref), url_at=now)
        task = _ev(key, "POST", "/audios/generations",
                   {"model": "voice-enrollment", "audio_url": st["url"], "preferred_name": "workbench", "language": "zh"})
        voice = (_task(key, task, "复刻音色", tries=60).get("result_data") or {}).get("voice")
        if not voice:
            raise VideoError("复刻音色失败：没有拿到音色")
        st.update(voice=voice, voice_at=time.time())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(st), encoding="utf-8")
        return voice


def speak(settings: Settings, key: str, text: str, voice: str, out: Path) -> None:
    """One line in the chosen voice: a preset, or the author's cloned voice."""
    if voice != CLONE:
        return synth(key, text, voice, out)
    try:
        synth(key, text, cloned_voice(settings, key), out, language="zh")
    except VoiceExpired:  # expired early: clone once more and retry
        synth(key, text, cloned_voice(settings, key, fresh=True), out, language="zh")


def voices(settings: Settings) -> list[dict]:
    mine = [{"id": CLONE, "name": settings.video_clone_name or "我的声音", "desc": "用你自己的录音复刻"}] if clone_ref(settings) else []
    return mine + VOICES


def default_voice(settings: Settings) -> str:
    ids = {v["id"] for v in voices(settings)}
    return settings.video_voice if settings.video_voice in ids else VOICES[0]["id"]


def tool_env() -> dict:
    env = dict(os.environ)
    extra = [str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    env["PATH"] = ":".join(extra + [env.get("PATH", "/usr/bin:/bin")])
    return env


def ffmpeg(*args: str) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True, env=tool_env())
    if proc.returncode:
        raise VideoError(f"ffmpeg 出错：{proc.stderr[-300:]}")


def duration_of(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, env=tool_env()).stdout.strip()
    return round(float(out or 0), 3)


# ------------------------------------------------------------------ timing

def whisper_available() -> bool:
    try:
        import mlx_whisper  # noqa: F401
        return True
    except Exception:
        return False


def vocab_prompt(vocab: str) -> str:
    """The same words recordings use (settings.transcribe_vocab). On 22 real
    lines the vocabulary moved the match rate by under half a percent either way."""
    vocab = vocab.strip()
    return "以下是普通话口播。" + (f"常见词：{vocab}。" if vocab else "")


def heard_chars(path: Path, vocab: str = "") -> list[tuple[str, float, float]]:
    """Characters Whisper heard, each with a time (a word's span split evenly).
    Only a short vocabulary prompt: given the whole line, Whisper copies it and
    stops giving word times after the first garbled character."""
    import mlx_whisper
    with _WHISPER_LOCK:
        res = mlx_whisper.transcribe(str(path), path_or_hf_repo=WHISPER, language="zh", word_timestamps=True,
                                     initial_prompt=vocab_prompt(vocab))
    chars = []
    for seg in res.get("segments", []):
        for w in seg.get("words", []):
            text = "".join(c for c in w["word"] if c not in PUNCT)
            if not text:
                continue
            step = (w["end"] - w["start"]) / len(text)
            chars.extend((c.lower(), w["start"] + i * step, w["start"] + (i + 1) * step) for i, c in enumerate(text))
    return chars


def align(spoken: str, heard: list, total: float) -> list[tuple[float, float] | None]:
    """A time for every spoken character (None for punctuation). Characters
    Whisper heard differently get times interpolated from their neighbours; with
    no Whisper at all, the line is spread evenly with a short pause at each mark."""
    idx = [i for i, c in enumerate(spoken) if c not in PUNCT]
    times: list = [None] * len(idx)
    if heard:
        sm = difflib.SequenceMatcher(a=[spoken[i].lower() for i in idx], b=[c for c, _, _ in heard], autojunk=False)
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                _, s, e = heard[blk.b + k]
                times[blk.a + k] = (s, e)
    known = [k for k, t in enumerate(times) if t]
    if not known:  # even spread, a beat at each mark
        weights = []
        for k, i in enumerate(idx):
            nxt = spoken[i + 1] if i + 1 < len(spoken) else ""
            weights.append(1.0 + (1.6 if nxt and nxt in "，。？！；：,.?!;:" else 0))
        span = max(0.5, total - LEAD_S - TAIL_S)
        unit = span / max(1.0, sum(weights))
        t = LEAD_S
        for k, w in enumerate(weights):
            times[k] = (t, t + unit)
            t += unit * w
    else:
        for k in range(len(times)):
            if times[k]:
                continue
            prev = max((j for j in known if j < k), default=None)
            nxt = min((j for j in known if j > k), default=None)
            t0 = times[prev][1] if prev is not None else LEAD_S
            t1 = times[nxt][0] if nxt is not None else max(t0 + 0.1, total - TAIL_S)
            lo = prev if prev is not None else -1
            hi = nxt if nxt is not None else len(times)
            step = (t1 - t0) / max(1, hi - lo - 1)
            times[k] = (t0 + (k - lo - 1) * step, t0 + (k - lo) * step)
    out: list = [None] * len(spoken)
    for k, i in enumerate(idx):
        out[i] = times[k]
    return out


def make_cue(display: str, segs, times, offset: float, duration: float):
    """cue("phrase", default) → when the phrase starts being said (scene time)."""
    def cue(phrase, default: float) -> float:
        if phrase:
            at = display.find(str(phrase))
            if at >= 0:
                j = disp_to_spoken(at, segs)
                for k in range(j, min(len(times), j + 8)):
                    if times[k]:
                        return round(max(0.0, min(duration - 0.4, times[k][0] - 0.08)), 3)
        return round(max(0.0, min(duration - 0.4, default)), 3)
    return cue


def caption_chunks(display: str, spoken: str, segs, times) -> list[dict]:
    """Caption words: jieba words merged to about 2–6 characters, never across a
    mark; a chunk ending a phrase is flagged so the caption line breaks there."""
    try:
        import jieba
        jieba.setLogLevel(60)
        for _d0, _d1, s0, s1 in segs:
            jieba.add_word(spoken[s0:s1], freq=10 ** 6)
        toks = list(jieba.cut(spoken))
    except Exception:
        toks = re.findall(r"[A-Za-z0-9]+|\s+|.", spoken)

    def width(s: str) -> float:
        return sum(0.55 if c.isascii() else 1 for c in s if c != " ")

    def is_mark(t: str) -> bool:
        return t.strip() != "" and all(c in PUNCT for c in t)

    out, buf, start = [], "", 0
    pos = 0

    def flush(end_phrase: bool) -> None:
        nonlocal buf
        if buf.strip():
            end = start + len(buf.rstrip())
            ts = [times[i] for i in range(start, end) if i < len(times) and times[i]]
            if ts:
                d0, d1 = disp_span(start, end, segs)
                out.append({"text": display[d0:d1].strip(), "start": round(ts[0][0], 3),
                            "end": round(ts[-1][1], 3), "phrase_end": end_phrase})
        buf = ""

    for k, tok in enumerate(toks):
        if not tok.strip():
            if buf:
                buf += tok
            pos += len(tok)
            continue
        if is_mark(tok):
            flush(True)
            pos += len(tok)
            continue
        rest = [t for t in toks[k + 1:] if t.strip()]
        last_before_mark = not rest or is_mark(rest[0])
        if buf and width(buf) + width(tok) > 6 and not (last_before_mark and width(tok) <= 1):
            flush(False)
        if not buf:
            start = pos
        buf += tok
        pos += len(tok)
    flush(True)
    return out


def caption_groups(chunks: list[dict]) -> list[dict]:
    groups, cur = [], None
    for ch in chunks:
        if cur and (sum(vs.cells(w["text"]) for w in cur["words"]) + vs.cells(ch["text"]) > 13.5
                    or ch["start"] - cur["end"] > 0.3):
            groups.append(cur)
            cur = None
        if not cur:
            cur = {"start": ch["start"], "end": ch["end"], "words": []}
        cur["words"].append({"text": ch["text"], "start": ch["start"], "end": ch["end"]})
        cur["end"] = ch["end"]
        if ch["phrase_end"]:
            groups.append(cur)
            cur = None
    if cur:
        groups.append(cur)
    return groups


# ------------------------------------------------------------------ fonts

def font_source() -> Path:
    path = data_dir() / "video" / "NotoSansSC-VF.ttf"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(urllib.request.urlopen(urllib.request.Request(FONT_URL, headers=UA), timeout=300).read())
        tmp.rename(path)
    return path


def static_font(weight: int) -> Path:
    """One weight cut out of the variable font, kept on disk: cutting takes about
    3 s a weight, subsetting the cut font about 0.2 s, and previews need it fast."""
    path = data_dir() / "video" / f"NotoSansSC-{weight}.ttf"
    with _FONT_LOCK:
        if not path.exists():
            from fontTools.ttLib import TTFont
            from fontTools.varLib import instancer
            font = instancer.instantiateVariableFont(TTFont(font_source()), {"wght": weight})
            tmp = path.with_suffix(".part")
            font.save(tmp)
            tmp.rename(path)
    return path


def subset_fonts(text: str, out_dir: Path) -> None:
    """Four static weights of Noto Sans SC holding just the characters on screen."""
    from fontTools import subset
    from fontTools.ttLib import TTFont
    chars = set(text) | set(chr(c) for c in range(0x20, 0x7F)) | set("，。？！、：；“”‘’（）《》「」—…·→←✓$")
    out_dir.mkdir(parents=True, exist_ok=True)
    for w in vs.WEIGHTS:
        font = TTFont(static_font(w))
        opts = subset.Options()
        opts.layout_features = ["*"]
        opts.notdef_outline = True
        sub = subset.Subsetter(opts)
        sub.populate(text="".join(sorted(chars)))
        sub.subset(font)
        font.flavor = "woff2"
        font.save(out_dir / f"noto-{w}.woff2")


# ------------------------------------------------------------------ planning (the AI's script)

PLAN = """把下面这篇 {name} 的长文改成一条竖屏短视频（抖音 / 视频号 / 小红书），{name} 本人口播，AI 配音读出来。

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

<长文里的截图>
{pictures}
</长文里的截图>

先用 Read 工具把要用的截图都看一遍，再写脚本。

脚本要求：
- 8 到 12 个镜头，口播一共 450 到 620 个字（大约 90 到 120 秒），宁短勿长；起因和痛点合起来不超过 3 个镜头，把篇幅留给具体怎么做和效果。每个镜头的 say 是这一镜头读出来的话，1 到 3 句，口语，像 {name} 在直播里跟熟人聊，不要书面语、不要播音腔、不要“家人们”。
- 第一个镜头直接说这条视频能给人什么（为什么值得看下去），不要先自我介绍；第二、三个镜头讲为什么要做这件事；中间是具体怎么做、效果是什么；倒数第二个可以是一句观点；最后一个是结尾（有开源地址或者链接就放，没有就落回观众）。
- 只用长文里有的事实、数字和观点，不编。数字照常写阿拉伯数字，英文名照常写，配音时会自动处理读法。
- 画面上的字要短：标题不超过 12 个字，卡片每条不超过 10 个字，画面上的字必须是 say 里说到的东西的提炼，不要把 say 整句搬上去。
- 每个“cue”字段写 say 里原样出现的几个字，那个元素会在读到这几个字时出现；写不出来就省略。
- 截图镜头的 focus 是要放大的区域，用 0 到 1 的比例写 [左, 上, 右, 下]（相对整张图），要框住能看清、和这句话直接相关的那块界面；打了码、模糊的地方不要放大；整张图都要看就写 null。

镜头类型和字段（type 只能是这几种）：
- hook：开头大字。words 是 1 到 2 行大字（每行不超过 6 个字），accent 是其中要标亮的词；可选 lead（大字出来前的一句引子，不超过 14 字）、sub（一行小字）；可选 chips（最多 4 个小卡片 {{"text","cue"}}）和 box（把卡片框起来的一句话，比如“全交给 AI”）；words_cue、lead_cue、box_cue。
- points：要点卡片。eyebrow（小标签，如“01 素材”，可选）、title、accent、items（1 到 4 条 {{"text","sub","cue"}}）、可选 footer（卡片下面一句大字）和 footer_cue。
- shot：截图放大。eyebrow、title、accent、image（截图编号）、focus、focus_cue、可选 label（框下面的标注，不超过 12 字）和 label_cue、可选 badges（右上角 1 到 2 个小徽章 {{"text","cue"}}）、可选 title2（读到某处时标题换成这句）和 title2_cue。
- stat：数字。eyebrow、title、accent、value（只写数字）、unit、value_cue、可选 then/then_unit/then_cue（数字再变成另一个，比如 866 篇里挑出 62 篇）、可选 sub。
- change：前后对比。eyebrow、title、accent、rows（1 到 3 条 {{"label","before","after","cue"}}，before 被划掉、after 打出来）、可选 footer、footer_accent、footer_cue。
- quote：金句。可选 lead（前面一句小字）、lines（1 到 3 行，每行不超过 12 字）、accent、lines_cue（每行一个 cue 的数组）。
- cta：结尾。可选 image（仓库或者成品的截图编号）、badge（如“已开源”）、可选 chips（{{"text","cue"}}）、可选 command（一句命令或链接）、sign（署名，比如“我是 {name}”）、sign_sub（一句小字）。

只输出一个 JSON 对象：{{"scenes": [{{"type": "...", "say": "...", ...}}]}}
"""


def _pictures(draft: dict, folder: Path) -> tuple[str, dict[int, dict]]:
    """Downscaled copies the model can open with Read, and the list for the prompt."""
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    lines, known = [], {}
    body = draft["body"]
    for m in re.finditer(r"!\[[^\]]*\]\(/api/images/(\d+)\)", body):
        iid = int(m.group(1))
        image = images.get(iid)
        if not image or iid in known:
            continue
        before = plain(body[:m.start()]).strip().splitlines()
        copy = folder / f"img-{iid}.jpg"
        with Image.open(images.path_of(image)) as im:
            im = im.convert("RGB")
            im.thumbnail((1600, 1600))
            im.save(copy, "JPEG", quality=85)
        known[iid] = image
        lines.append(f"- 编号 {iid}（{image['width']}×{image['height']}，文件 {copy}）：前一句是“{(before[-1] if before else '')[-60:]}”")
    return "\n".join(lines) or "（没有截图，就不用 shot 和带图的 cta）", known


def _clip(v, n: int) -> str:
    return str(v or "").strip()[:n]


def _items(raw, n: int, keys: tuple[str, ...]) -> list[dict]:
    out = []
    for x in (raw or [])[:n]:
        if isinstance(x, str):
            x = {"text": x}
        if isinstance(x, dict):
            out.append({k: _clip(x.get(k), 40) for k in keys if x.get(k)})
    return [x for x in out if any(x.values())]


def normalize(scene: dict, allowed_images: set[int], keep_type: bool = False) -> dict:
    """Clean one scene from the model or the editor. keep_type: the author may pick
    "截图放大" before choosing the picture, so an edit keeps the type as chosen."""
    kind = scene.get("type") if scene.get("type") in TYPES else "points"
    s = {"type": kind, "say": _clip(scene.get("say"), SAY_MAX)}
    for key in ("eyebrow", "title", "accent", "lead", "sub", "box", "footer", "footer_accent", "label", "title2", "accent2",
                "unit", "then_unit", "badge", "command", "sign", "sign_sub"):
        if scene.get(key):
            s[key] = _clip(scene[key], 80 if key == "command" else 40)
    for key in [k for k in scene if k.endswith("_cue")]:
        if isinstance(scene[key], str) and scene[key].strip():
            s[key] = _clip(scene[key], 20)
    if isinstance(scene.get("words"), list):
        s["words"] = [_clip(w, 10) for w in scene["words"] if str(w).strip()][:2]
    elif scene.get("words"):
        s["words"] = [_clip(scene["words"], 10)]
    if isinstance(scene.get("lines"), list):
        s["lines"] = [_clip(w, 16) for w in scene["lines"] if str(w).strip()][:3]
    if isinstance(scene.get("lines_cue"), list):
        s["lines_cue"] = [_clip(w, 20) for w in scene["lines_cue"]][:3]
    for key in ("value", "then"):
        if scene.get(key) is not None:
            digits = re.sub(r"[^\d.,%+]", "", str(scene[key]))[:7]
            if digits:
                s[key] = digits
    s["chips"] = _items(scene.get("chips"), 4, ("text", "sub", "cue")) if scene.get("chips") else []
    s["items"] = _items(scene.get("items"), 4, ("text", "sub", "cue", "accent")) if scene.get("items") else []
    s["badges"] = _items(scene.get("badges"), 2, ("text", "cue")) if scene.get("badges") else []
    s["rows"] = _items(scene.get("rows"), 3, ("label", "before", "after", "cue")) if scene.get("rows") else []
    try:
        image = int(scene.get("image")) if scene.get("image") not in (None, "") else None
    except (TypeError, ValueError):
        image = None
    if image is not None and image in allowed_images:
        s["image"] = image
        focus = scene.get("focus")
        if isinstance(focus, list) and len(focus) == 4:
            try:
                f = [min(1.0, max(0.0, float(v))) for v in focus]
                if f[2] - f[0] > 0.03 and f[3] - f[1] > 0.03:
                    s["focus"] = [round(v, 4) for v in f]
            except (TypeError, ValueError):
                pass
    elif kind == "shot" and not keep_type:
        s["type"] = "points"
    for key in ("chips", "items", "badges", "rows"):
        if not s[key]:
            del s[key]
            continue
        for item in s[key]:  # an item the voice names appears when it is said
            if not item.get("cue") or item["cue"] not in s["say"]:
                item.pop("cue", None)
                text = item.get("text") or item.get("label") or ""
                if text and text in s["say"]:
                    item["cue"] = text[:12]
    return s


def plan(settings: Settings, draft_id: int) -> dict:
    draft = db.row("SELECT * FROM drafts WHERE id=?", (draft_id,))
    if not draft or not draft["body"].strip():
        raise VideoError("长文还是空的")
    who = prompts.persona(settings)
    folder = data_dir() / "llm-cwd" / "video" / f"draft-{draft_id}"
    pictures, known = _pictures(draft, folder)
    prompt = PLAN.format(guide=prompts.voice_guide(settings)[:4000], lessons=prompts.style_lessons_text(), title=draft["title"],
                         body=draft["body"][:20000], pictures=pictures, name=who["name"])
    reply = llm.run(prompt, timeout=1200, tools=["Read"], max_turns=len(known) + 4, allow_read=True)
    result = llm.extract_json(reply)
    raw = result.get("scenes") if isinstance(result, dict) else result
    scenes = [normalize(s, set(known)) for s in (raw or []) if isinstance(s, dict)]
    scenes = [s for s in scenes if s["say"]][:16]
    if not scenes:
        raise VideoError("模型没有给出可用的脚本")
    if scenes[-1]["type"] == "cta" and not scenes[-1].get("sign"):
        scenes[-1]["sign"] = f"我是 {who['name']}"
    shutil.rmtree(folder, ignore_errors=True)
    return update(draft_id, scenes=scenes, planned_at=db.now(), status="", stage="", error=None)


def estimate_seconds(scenes: list[dict]) -> float:
    return round(sum(len(re.sub(r"[\s，。？！、：；,.?!:;]", "", s.get("say", ""))) / 5.1 + 0.5 for s in scenes), 1)


# ------------------------------------------------------------------ render

def _progress(draft_id: int, stage: str, pct: int) -> None:
    update(draft_id, stage=stage, pct=pct)


def _image_for(scene: dict, assets: Path) -> dict | None:
    iid = scene.get("image")
    image = images.get(iid) if iid else None
    if not image:
        return None
    src = images.path_of(image)
    name = f"img-{iid}{src.suffix or '.png'}"
    if not (assets / name).exists():
        shutil.copyfile(src, assets / name)
    return {"src": f"assets/{name}", "w": image["width"], "h": image["height"]}


def draft_voice(settings: Settings, state: dict) -> str:
    picked = state.get("voice")
    return picked if picked in {v["id"] for v in voices(settings)} else default_voice(settings)


def voice_cache(voice: str, spoken: str) -> Path:
    """Where the voice for this exact line and voice is kept (render and preview share it)."""
    cache = data_dir() / "video-cache"
    cache.mkdir(exist_ok=True)
    return cache / (hashlib.sha1(f"{voice}|{INSTRUCTION}|{spoken}".encode()).hexdigest()[:16] + ".wav")


def timed_line(spoken: str, raw: Path, clip: Path, vocab: str, use_whisper: bool) -> dict:
    """The cached voice padded into the scene's clip, and when each character is
    said (cached next to the voice)."""
    ffmpeg("-i", str(raw), "-af", f"adelay={int(LEAD_S * 1000)},apad=pad_dur={TAIL_S}", "-ar", "48000", "-ac", "1", str(clip))
    duration = max(2.0, duration_of(clip))
    saved = raw.with_suffix(f".{LEAD_S}-{TAIL_S}{'-w' if use_whisper else ''}.times.json")
    if saved.exists():
        times = [tuple(t) if t else None for t in json.loads(saved.read_text())]
    else:
        times = align(spoken, heard_chars(clip, vocab) if use_whisper else [], duration)
        saved.write_text(json.dumps(times))
    return {"clip": clip, "raw": raw, "duration": duration, "times": times}


def build_project(settings: Settings, draft_id: int) -> tuple[Path, list[dict], list[float]]:
    """Voice, timing and every composition, ready for the HyperFrames CLI."""
    draft = db.row("SELECT * FROM drafts WHERE id=?", (draft_id,))
    state = load(draft)
    scenes = [s for s in state.get("scenes") or [] if str(s.get("say", "")).strip()]
    if not scenes:
        raise VideoError("脚本是空的")
    key = evolink_key(settings)
    voice = draft_voice(settings, state)
    root = work_dir(draft_id)
    shutil.rmtree(root, ignore_errors=True)
    assets = root / "assets"
    (assets / "audio").mkdir(parents=True)
    (root / "compositions").mkdir()

    # 1. voice, one clip per scene (cached by the exact text and voice; new lines in parallel)
    todo = []
    for scene in scenes:
        spoken, segs = speakable(scene["say"])
        todo.append((spoken, segs, voice_cache(voice_key(settings, voice), spoken)))
    missing = [(spoken, raw) for spoken, _segs, raw in todo if not raw.exists()]
    done = len(todo) - len(missing)
    _progress(draft_id, f"配音 {done}/{len(todo)}", 5)
    with ThreadPoolExecutor(4) as pool:
        for _ in pool.map(lambda job: speak(settings, key, job[0], voice, job[1]), missing):
            done += 1
            _progress(draft_id, f"配音 {done}/{len(todo)}", int(5 + 25 * done / len(todo)))

    # 2. when each character is said (cached next to the voice clip)
    use_whisper = whisper_available()
    lines = []
    for n, (spoken, segs, raw) in enumerate(todo, 1):
        _progress(draft_id, f"对齐字幕 {n}/{len(todo)}", int(30 + 15 * n / len(todo)))
        line = timed_line(spoken, raw, assets / "audio" / f"voice-{n:02d}.wav", settings.transcribe_vocab, use_whisper)
        lines.append({**line, "spoken": spoken, "segs": segs})

    # 3. compositions
    _progress(draft_id, "准备画面", 46)
    starts, t = [], 0.0
    for line in lines:
        starts.append(round(t, 3))
        t += line["duration"]
    total = round(t, 3)
    chunks = []
    for scene, line, start in zip(scenes, lines, starts):
        for ch in caption_chunks(scene["say"], line["spoken"], line["segs"], line["times"]):
            chunks.append({**ch, "start": round(ch["start"] + start, 3), "end": round(ch["end"] + start, 3)})
    groups = caption_groups(chunks)
    for i, (scene, line) in enumerate(zip(scenes, lines)):
        sid = f"s{i + 1:02d}"
        data = dict(scene)
        if scene.get("image"):
            data["image"] = _image_for(scene, assets)
        cue = make_cue(scene["say"], line["segs"], line["times"], 0.0, line["duration"])
        host = line["duration"] + (vs.OVERLAP if i + 1 < len(scenes) else 0)
        (root / "compositions" / f"{sid}.html").write_text(
            vs.scene_html(sid, data, host, cue, final=(i + 1 == len(scenes))), encoding="utf-8")
    (root / "compositions" / "captions.html").write_text(vs.captions_html(groups, total), encoding="utf-8")
    author = settings.author or prompts.persona(settings)["name"]
    (root / "compositions" / "chrome.html").write_text(vs.chrome_html(author, settings.video_tagline, total), encoding="utf-8")
    bgm = None
    bgm_src = Path(settings.video_bgm).expanduser() if settings.video_bgm else data_dir() / "video" / "bgm.wav"
    if state.get("bgm", True) and bgm_src.is_file():
        ffmpeg("-i", str(bgm_src), "-t", f"{total:.3f}", "-af",
               f"afade=t=in:st=0:d=0.6,afade=t=out:st={max(0.0, total - 3.0):.3f}:d=3.0", "-ar", "48000", "-ac", "2",
               str(assets / "audio" / "bgm.wav"))
        bgm = "assets/audio/bgm.wav"
    voices = [f"assets/audio/voice-{n:02d}.wav" for n in range(1, len(lines) + 1)]
    (root / "index.html").write_text(vs.index_html(scenes, starts, [ln["duration"] for ln in lines], voices, bgm, 0.11, total),
                                     encoding="utf-8")
    (root / "hyperframes.json").write_text(json.dumps({"paths": {"assets": "assets"}}), encoding="utf-8")
    shown = vs.all_text(scenes) + "".join(w["text"] for g in groups for w in g["words"]) + author + settings.video_tagline
    subset_fonts(shown, assets / "fonts")
    return root, lines, starts


def render(settings: Settings, draft_id: int) -> dict:
    root, lines, starts = build_project(settings, draft_id)
    # 4. render with HyperFrames (headless Chrome + ffmpeg)
    out = root / "renders" / "video.mp4"
    cmd = ["npx", "--yes", HYPERFRAMES, "render", str(root), "--output", str(out), "--quality", "high"]
    _progress(draft_id, "渲染画面", 50)
    proc = subprocess.Popen(cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=tool_env())
    tail, last = [], 0.0
    for raw_line in proc.stdout:
        tail = (tail + [raw_line])[-40:]
        if raw_line.startswith("@hf-progress"):
            try:
                pct = int(json.loads(raw_line.split(" ", 1)[1]).get("pct", 0))
            except (ValueError, IndexError):
                continue
            if time.time() - last > 1.5:
                _progress(draft_id, "渲染画面", 50 + int(pct * 0.49))
                last = time.time()
    if proc.wait() or not out.exists():
        raise VideoError("渲染失败：" + "".join(tail)[-400:])
    final = video_path(draft_id)
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(out), final)
    info = {"duration": duration_of(final), "size": final.stat().st_size, "rendered_at": db.now(),
            "scenes": [{"start": s, "duration": ln["duration"]} for s, ln in zip(starts, lines)]}
    return update(draft_id, video=info, status="", stage="", pct=100, error=None)


# ------------------------------------------------------------------ instant preview of one scene

PREVIEW_KEEP = 8  # built previews kept per draft
# a preview is kept under a hash of what it shows, and of the code that drew it
_PREVIEW_CODE = hashlib.sha1(Path(vs.__file__).read_bytes() + Path(__file__).read_bytes()).hexdigest()[:8]


def preview_dir(draft_id: int) -> Path:
    return data_dir() / "video-preview" / f"draft-{draft_id}"


def preview(settings: Settings, draft_id: int, index: int, scene: dict, last: bool = False, voice_now: bool = False) -> dict:
    """One scene as a page the browser plays: the composition, captions and author
    mark the render uses, without encoding anything. The page only seeks; the
    editor plays the voice and drives the picture from it.

    The voice is made only when asked (voice_now) and is cached by text, so editing a
    line costs nothing until you listen; until then the timing is estimated and
    the preview is silent. A preview is kept under a hash of what it shows, so
    flipping back to an earlier version is instant."""
    draft = db.row("SELECT * FROM drafts WHERE id=?", (draft_id,))
    if not draft:
        raise VideoError("找不到这篇长文")
    state = load(draft)
    s = normalize(scene, {im["id"] for im in images.for_draft(draft_id)}, keep_type=True)
    if not s["say"].strip():
        raise VideoError("这个镜头还没写口播")
    voice = draft_voice(settings, state)
    spoken, segs = speakable(s["say"])
    raw = voice_cache(voice_key(settings, voice), spoken)
    if voice_now and not raw.exists():
        speak(settings, evolink_key(settings), spoken, voice, raw)
    voiced = raw.exists()
    author = settings.author or prompts.persona(settings)["name"]
    key = hashlib.sha1(json.dumps([s, voice_key(settings, voice), voiced, index, last, author, settings.video_tagline, _PREVIEW_CODE], ensure_ascii=False,
                                  sort_keys=True).encode()).hexdigest()[:12]
    base = preview_dir(draft_id)
    root = base / key
    if not (root / "meta.json").exists():
        tmp = base / f".{key}-{secrets.token_hex(4)}"
        assets = tmp / "assets"
        (assets / "audio").mkdir(parents=True)
        if voiced:
            line = timed_line(spoken, raw, assets / "audio" / "voice.wav", settings.transcribe_vocab, whisper_available())
            duration, times = line["duration"], line["times"]
        else:
            duration = max(2.0, estimate_seconds([s]) + LEAD_S + TAIL_S)
            times = align(spoken, [], duration)
        data = dict(s)
        if s.get("image"):
            data["image"] = _image_for(s, assets)
        sid = f"s{index + 1:02d}"
        cue = make_cue(s["say"], segs, times, 0.0, duration)
        groups = caption_groups(caption_chunks(s["say"], spoken, segs, times))
        parts = [vs.scene_html(sid, data, duration, cue, final=last), vs.captions_html(groups, duration),
                 vs.chrome_html(author, settings.video_tagline, duration + 30)]  # the mark never fades inside a scene
        # the mark has long finished fading in by the second scene
        (tmp / "index.html").write_text(vs.preview_html(parts, duration, {"chrome": 0.0 if index == 0 else 1.0}), encoding="utf-8")
        shown = vs.all_text([s]) + "".join(w["text"] for g in groups for w in g["words"]) + author + settings.video_tagline
        subset_fonts(shown, assets / "fonts")
        (tmp / "meta.json").write_text(json.dumps({"duration": duration, "voiced": voiced}), encoding="utf-8")
        if root.exists():  # a twin request finished first
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            tmp.rename(root)
    os.utime(root)
    built = sorted((p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    stray = [p for p in base.iterdir() if p.name.startswith(".") and time.time() - p.stat().st_mtime > 600]
    for old in built[PREVIEW_KEEP:] + stray:
        shutil.rmtree(old, ignore_errors=True)
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    url = f"/api/drafts/{draft_id}/video/preview/{key}/"
    return {"url": url + "index.html", "audio": url + "assets/audio/voice.wav" if meta["voiced"] else None,
            "duration": meta["duration"], "voiced": meta["voiced"]}


def preview_file(draft_id: int, key: str, path: str) -> Path | None:
    root = (preview_dir(draft_id) / key).resolve()
    target = (root / path).resolve()
    if not re.fullmatch(r"[0-9a-f]{12}", key) or root not in target.parents or not target.is_file():
        return None
    return target


# ------------------------------------------------------------------ the 视频 page

def overview() -> list[dict]:
    """Every long-form draft with where its video stands, most recently touched first."""
    out = []
    for d in db.rows("SELECT id, title, body, status, video, updated_at FROM drafts WHERE platform='wechat' "
                     "ORDER BY updated_at DESC"):
        v = load(d)
        scenes = v.get("scenes") or []
        out.append({"id": d["id"], "title": d["title"], "draft_status": d["status"], "updated_at": d["updated_at"],
                    "has_body": bool(d["body"].strip()), "status": v.get("status") or "", "stage": v.get("stage") or "",
                    "pct": v.get("pct") or 0, "error": v.get("error"), "scenes": len(scenes),
                    "seconds": estimate_seconds(scenes), "planned_at": v.get("planned_at"), "edited_at": v.get("edited_at"),
                    "video": v.get("video")})
    return out


def options(settings: Settings) -> dict:
    key_ok = bool(settings.evolink_key_file and Path(settings.evolink_key_file).expanduser().is_file())
    return {"voices": voices(settings), "default_voice": default_voice(settings), "tts": key_ok,
            "whisper": whisper_available(), "types": TYPE_NAMES,
            "bgm": bool((Path(settings.video_bgm).expanduser() if settings.video_bgm else data_dir() / "video" / "bgm.wav").is_file())}
