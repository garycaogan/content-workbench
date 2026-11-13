"""Short vertical video (抖音 / 视频号 / 小红书) of a long-form draft.

Two steps, like the 小红书 version: the AI writes a scene-by-scene script (what
is said, what is on screen, which screenshot and which part of it), the author
edits it, then 出片 turns it into an MP4 on this machine:

  voice    Evolink's Chinese TTS (or the author's voice cloned on MiniMax),
           one clip per scene, cached by text
  timing   local Whisper (when installed) finds when each word is said, so
           captions and on-screen pieces land on the spoken word
  picture  each scene becomes a HyperFrames composition (video_scenes.py), the
           HyperFrames CLI renders the whole thing with headless Chrome; a
           "原片片段" scene plays a stretch of a video from the article body

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

from . import clips, db, images, llm, prompts, video_scenes as vs, video_styles as vs_styles, whisper
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
TYPES = ("hook", "points", "shot", "clip", "stat", "change", "quote", "cta")
TYPE_NAMES = {"hook": "开头大字", "points": "要点卡片", "shot": "截图放大", "clip": "原片片段", "stat": "数字", "change": "前后对比",
              "quote": "金句", "cta": "结尾"}
FILLS = ("full", "window")
SAY_MAX = 160
PUNCT = "，。？！、：；,.?!:;—…“”\"'（）()《》「」【】 \n"
# a render and an editor preview can both reach for the font cache (whisper.py serializes Whisper)
_FONT_LOCK = threading.RLock()


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


FORMATS = ("vertical", "landscape")


def _suffix(fmt: str) -> str:
    return "" if fmt == "vertical" else f"-{fmt}"


def work_dir(draft_id: int, fmt: str = "vertical") -> Path:
    return data_dir() / "video-work" / f"draft-{draft_id}{_suffix(fmt)}"


def video_path(draft_id: int, fmt: str = "vertical") -> Path:
    return data_dir() / "videos" / f"draft-{draft_id}{_suffix(fmt)}.mp4"


def render_key(fmt: str) -> str:
    """Where a format's last render is kept on the draft's video state."""
    return "video" if fmt == "vertical" else f"video_{fmt}"


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
    the recording itself (the clone's id changes every few hours, the voice doesn't),
    or the MiniMax voice with its model and speed."""
    if voice != CLONE:
        return voice
    if minimax_clone(settings):
        return f"minimax-{settings.minimax_voice_id}-{settings.minimax_model}-{float(settings.minimax_speed or 1.0)}"
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


# ------------------------------------------------------------------ or a voice cloned on MiniMax
#
# When minimax_voice_id is set, the author's own voice is that voice, read by
# MiniMax's T2A (China site). It does not expire. A MiniMax failure is reported
# as it is: the line is never quietly read by the Evolink clone instead.

MINIMAX_RETRY = {1000, 1001, 1002, 1013, 1039}  # unknown, timeout, rate limit, service error, token limit
MINIMAX_WHY = {1004: "key 不对或已失效", 1008: "MiniMax 账户余额不足", 2054: "这个音色 id 在 MiniMax 上不存在",
               2013: "参数不对（音色 id、模型或语速）", 1026: "文字触发了内容审核", 1027: "生成的内容触发了审核"}


def minimax_clone(settings: Settings) -> bool:
    return bool(settings.minimax_voice_id)


def minimax_key(settings: Settings) -> str:
    path = Path(settings.minimax_key_file).expanduser() if settings.minimax_key_file else None
    if not path or not path.is_file():
        raise VideoError("还没配置 MiniMax key（设置里的 minimax_key_file），「我的声音」配不了音")
    return path.read_text(encoding="utf-8").strip()


def minimax_synth(settings: Settings, text: str, out: Path) -> None:
    body = {"model": settings.minimax_model or "speech-2.8-hd", "text": text, "stream": False, "language_boost": "Chinese",
            "voice_setting": {"voice_id": settings.minimax_voice_id, "speed": float(settings.minimax_speed or 1.0), "vol": 1, "pitch": 0},
            "audio_setting": {"sample_rate": 44100, "format": "wav", "channel": 1}}
    key = minimax_key(settings)
    url = (settings.minimax_host or "https://api.minimaxi.com").rstrip("/") + "/v1/t2a_v2"
    why = ""
    for attempt in range(3):
        if attempt:
            time.sleep(3 * attempt)
        req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", **UA})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=120).read())
        except urllib.error.HTTPError as exc:
            why = f"HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:200]}"
            if exc.code < 500 and exc.code != 429:
                break
            continue
        except (urllib.error.URLError, OSError) as exc:
            why = f"连不上 MiniMax：{getattr(exc, 'reason', exc)}"
            continue
        base = r.get("base_resp") or {}
        code = base.get("status_code")
        audio = (r.get("data") or {}).get("audio")
        if code == 0 and audio:
            out.write_bytes(bytes.fromhex(audio))
            return
        why = f"{code} {base.get('status_msg') or '没有返回音频'}" + (f"，{MINIMAX_WHY[code]}" if code in MINIMAX_WHY else "")
        if code not in MINIMAX_RETRY:
            break
    raise VideoError(f"MiniMax 配音失败（{why}），没有换成别的音色")


def tts_key(settings: Settings, voice: str) -> str:
    """The Evolink key, unless the line is read by the MiniMax clone (which brings its own)."""
    if voice == CLONE and minimax_clone(settings):
        minimax_key(settings)  # missing key: say so before anything is built
        return ""
    return evolink_key(settings)


def speak(settings: Settings, key: str, text: str, voice: str, out: Path) -> None:
    """One line in the chosen voice: a preset, or the author's cloned voice."""
    if voice != CLONE:
        return synth(key, text, voice, out)
    if minimax_clone(settings):
        return minimax_synth(settings, text, out)
    try:
        synth(key, text, cloned_voice(settings, key), out, language="zh")
    except VoiceExpired:  # expired early: clone once more and retry
        synth(key, text, cloned_voice(settings, key, fresh=True), out, language="zh")


def voices(settings: Settings) -> list[dict]:
    desc = "用你自己的声音（MiniMax 复刻）" if minimax_clone(settings) else "用你自己的录音复刻"
    mine = [{"id": CLONE, "name": settings.video_clone_name or "我的声音", "desc": desc}] if minimax_clone(settings) or clone_ref(settings) else []
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
    return whisper.available()


def vocab_prompt(vocab: str) -> str:
    """The same words recordings use (settings.transcribe_vocab). On 22 real
    lines the vocabulary moved the match rate by under half a percent either way."""
    vocab = vocab.strip()
    return "以下是普通话口播。" + (f"常见词：{vocab}。" if vocab else "")


def heard_chars(paths: list[Path], vocab: str = "", each=None) -> list[list[tuple[str, float, float]]]:
    """For each file, the characters Whisper heard, each with a time (a word's span
    split evenly). All files go through one Whisper child process (whisper.py).
    Only a short vocabulary prompt: given the whole line, Whisper copies it and
    stops giving word times after the first garbled character."""
    out = []
    for res in whisper.transcribe(paths, WHISPER, vocab_prompt(vocab), words=True, each=each):
        chars = []
        for w in res["words"]:
            text = "".join(c for c in w["word"] if c not in PUNCT)
            if not text:
                continue
            step = (w["end"] - w["start"]) / len(text)
            chars.extend((c.lower(), w["start"] + i * step, w["start"] + (i + 1) * step) for i, c in enumerate(text))
        out.append(chars)
    return out


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


def caption_keys(scene: dict) -> list[str]:
    """What the captions colour in a scene: the script's key words, then its accent words."""
    keys = [k for k in scene.get("keys") or [] if k]
    for name in ("accent", "accent2", "footer_accent"):
        if scene.get(name) and scene[name] not in keys:
            keys.append(scene[name])
    return keys


def mark_keys(chunks: list[dict], keys: list[str]) -> list[dict]:
    """Flag the caption words that carry a key word or a number."""
    for ch in chunks:
        text = ch["text"]
        ch["key"] = any(k in text or (len(text) >= 2 and text in k) for k in keys) or bool(re.search(r"\d", text))
    return chunks


def headline_of(state: dict, title: str) -> tuple[list[str], list[str]]:
    """The standing headline (two short lines) and the word to mark in it."""
    lines = [str(x).strip()[:14] for x in state.get("headline") or [] if str(x).strip()][:2]
    if not lines:
        title = re.sub(r"[（(].*?[)）]", "", title or "").strip()
        lines = [x[:14] for x in (vs.split_two(title) if len(title) > 12 else [title]) if x]
    return lines, [state["headline_key"]] if state.get("headline_key") else []


def caption_groups(chunks: list[dict], max_cells: float = 13.5) -> list[dict]:
    groups, cur = [], None
    for ch in chunks:
        if cur and (sum(vs.cells(w["text"]) for w in cur["words"]) + vs.cells(ch["text"]) > max_cells
                    or ch["start"] - cur["end"] > 0.3):
            groups.append(cur)
            cur = None
        if not cur:
            cur = {"start": ch["start"], "end": ch["end"], "words": []}
        cur["words"].append({"text": ch["text"], "start": ch["start"], "end": ch["end"], "key": bool(ch.get("key"))})
        cur["end"] = ch["end"]
        if ch["phrase_end"]:
            groups.append(cur)
            cur = None
    if cur:
        groups.append(cur)
    return groups


# ------------------------------------------------------------------ fonts
#
# Each style names its faces (video_styles.Face); a face's source is a font file
# downloaded once into <data>/video. Variable fonts are cut into static weights
# once and kept (cutting takes ~3 s a weight, subsetting ~0.2 s), then every
# render or preview subsets them to the characters on screen.

GOOGLE_FONTS = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
FONT_SOURCES = {
    # key: (url, file name, variable[, member of a .zip]) — all SIL Open Font License
    "noto-sans-sc": (FONT_URL, "NotoSansSC-VF.ttf", True),
    "noto-serif-sc": (GOOGLE_FONTS + "notoserifsc/NotoSerifSC%5Bwght%5D.ttf", "NotoSerifSC-VF.ttf", True),
    "smiley-sans": ("https://github.com/atelier-anchor/smiley-sans/releases/download/v2.0.1/smiley-sans-v2.0.1.zip",
                    "SmileySans-Oblique.ttf", False, "SmileySans-Oblique.ttf"),
}


def font_source(key: str = "noto-sans-sc") -> Path:
    """The font file, downloaded once (a .zip source names the member to keep)."""
    url, name, _variable = FONT_SOURCES[key][:3]
    path = data_dir() / "video" / name
    with _FONT_LOCK:
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300).read()
        if url.endswith(".zip"):
            import io
            import zipfile
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                data = zf.read(FONT_SOURCES[key][3])
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.rename(path)
    return path


def static_font(weight: int, key: str = "noto-sans-sc") -> Path:
    """One weight of a face, kept on disk (a static font is its own file)."""
    _url, name, variable = FONT_SOURCES[key][:3]
    if not variable:
        return font_source(key)
    path = data_dir() / "video" / f"{Path(name).stem.removesuffix('-VF')}-{weight}.ttf"
    with _FONT_LOCK:
        if not path.exists():
            from fontTools.ttLib import TTFont
            from fontTools.varLib import instancer
            font = instancer.instantiateVariableFont(TTFont(font_source(key)), {"wght": weight})
            tmp = path.with_suffix(".part")
            font.save(tmp)
            tmp.rename(path)
    return path


def subset_fonts(text: str, out_dir: Path, faces=None) -> None:
    """Every weight a style uses, holding just the characters on screen."""
    from fontTools import subset
    from fontTools.ttLib import TTFont
    chars = set(text) | set(chr(c) for c in range(0x20, 0x7F)) | set("，。？！、：；“”‘’（）《》「」—…·→←✓$")
    out_dir.mkdir(parents=True, exist_ok=True)
    for face in faces or vs.DEFAULT.faces:
        for w in face.weights:
            font = TTFont(static_font(w, face.source))
            opts = subset.Options()
            opts.layout_features = ["*"]
            opts.notdef_outline = True
            sub = subset.Subsetter(opts)
            sub.populate(text="".join(sorted(chars)))
            sub.subset(font)
            font.flavor = "woff2"
            font.save(out_dir / f"{face.stem}-{w}.woff2")


# ------------------------------------------------------------------ planning (the AI's script)

PLAN = """把下面这篇 {name} 的长文改成一条口播短视频，{name} 本人口播，AI 配音读出来。同一份脚本会出竖屏（抖音 / 视频号 / 小红书）和横屏（B 站 / YouTube）两版，画面上的字要两种画幅都放得下。

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

<长文里的视频片段>
{clips}
</长文里的视频片段>

{context}先用 Read 工具把要用的截图和视频片段的画面都看一遍，再写脚本。

脚本要求：
- 10 到 15 个镜头，口播一共 450 到 620 个字（大约 90 到 120 秒），宁短勿长；起因和痛点合起来不超过 3 个镜头，把篇幅留给具体怎么做和效果。每个镜头的 say 是这一镜头读出来的话，1 到 2 句、最多 40 个字（画面隔几秒就要变一次，长段落拆成两个镜头），口语，像 {name} 在直播里跟熟人聊，不要书面语、不要播音腔、不要“家人们”。
- 第一个镜头直接说这条视频能给人什么（为什么值得看下去），第一帧就是那句主张的大字，不要片头、不要先自我介绍；第二、三个镜头讲为什么要做这件事；中间是具体怎么做、效果是什么；倒数第二个可以是一句观点；最后一个是结尾（有开源地址或者链接就放，没有就落回观众）。
- 只用长文里有的事实、数字和观点，不编。数字照常写阿拉伯数字，英文名照常写，配音时会自动处理读法。
- 画面上的字要短：标题不超过 12 个字，卡片每条不超过 10 个字，画面上的字必须是 say 里说到的东西的提炼，不要把 say 整句搬上去。
- 每个“cue”字段写 say 里原样出现的几个字，那个元素会在读到这几个字时出现；写不出来就省略。一个镜头里尽量安排两三个元素随口播陆续出现，不要一出来就全部摆满。
- 每个镜头写 keys：say 里原样出现的 1 到 2 个关键词（每个 2 到 6 字，数字、产品名、结论词），字幕里会把它们标成强调色。
- 截图镜头的 focus 是要放大的区域，用 0 到 1 的比例写 [左, 上, 右, 下]（相对整张图），要框住能看清、和这句话直接相关的那块界面；打了码、模糊的地方不要放大；整张图都要看就写 null。
- 长文里有视频片段时优先用片段，真画面比截图有说服力：第一个镜头用 clip，第一帧就是原片画面加大字；讲到某个作品、某个画面时，也用它的片段，不要用片段截出来的图。一个片段可以在不同镜头里各播一段（clip_start、clip_end 错开）。

镜头类型和字段（type 只能是这几种）：
- hook：开头大字。words 是 1 到 2 行大字（每行不超过 6 个字），accent 是其中要标亮的词；可选 lead（大字出来前的一句引子，不超过 14 字）、sub（一行小字）；可选 chips（最多 4 个小卡片 {{"text","cue"}}）和 box（把卡片框起来的一句话，比如“全交给 AI”）；words_cue、lead_cue、box_cue。
- points：要点卡片。eyebrow（小标签，如“01 素材”，可选）、title、accent、items（1 到 4 条 {{"text","sub","cue","icon"}}，icon 是一个贴切的 emoji，可选）、可选 footer（卡片下面一句大字）和 footer_cue。
- shot：截图放大。eyebrow、title、accent、image（截图编号）、focus、focus_cue、可选 label（框下面的标注，不超过 12 字）和 label_cue、可选 badges（右上角 1 到 2 个小徽章 {{"text","cue"}}）、可选 title2（读到某处时标题换成这句）和 title2_cue。
- clip：原片片段，镜头里直接播长文里的一段视频，播多长跟着口播走，片段比口播短就停在最后一帧。clip（片段编号）、可选 clip_start / clip_end（从片段第几秒播到第几秒，不写就从头播）、可选 eyebrow、title（压在画面上的大字，不超过 12 个字）、accent、可选 label（来源或数据，比如“抖音 · 141万播放”，不超过 14 字）和 label_cue、可选 badges、可选 source（角上的小字来源，比如“@账号名 · TikTok”）。原声默认静音，只有原声本身就是看点（台词、音乐卡点）时才写 "sound": true，会压低混在口播下面。和画面同比例的片段会铺满全屏，其他的放在框里，不用管。
- stat：数字。eyebrow、title、accent、value（只写数字）、unit、value_cue、可选 then/then_unit/then_cue（数字再变成另一个，比如 866 篇里挑出 62 篇）、可选 sub。
- change：前后对比。eyebrow、title、accent、rows（1 到 3 条 {{"label","before","after","cue"}}，before 被划掉、after 打出来）、可选 footer、footer_accent、footer_cue。
- quote：金句。可选 lead（前面一句小字）、lines（1 到 3 行，每行不超过 12 字）、accent、lines_cue（每行一个 cue 的数组）。
- cta：结尾。可选 image（仓库或者成品的截图编号）、badge（如“已开源”）、可选 chips（{{"text","cue"}}）、可选 command（一句命令或链接）、sign（署名，比如“我是 {name}”）、sign_sub（一句小字）。

整条视频的画面风格，按这篇内容从下面选一个最合适的，写在 style 字段（只写英文 id）：
{styles}

再写一个常驻标题 headline：两行，每行不超过 10 个字，第一行说是什么事，第二行是钩子或者结论（像“企业的 AI 助手 / 应该是什么样的？”）；headline_key 是第一行里要标黄的词。有的风格会把它一直挂在画面顶部。

只输出一个 JSON 对象：{{"style": "...", "headline": ["...", "..."], "headline_key": "...", "scenes": [{{"type": "...", "say": "...", "keys": ["..."], ...}}]}}
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


def draft_clips(draft_id: int) -> dict[int, float]:
    """The body videos a scene may play (ready ones), with their length in seconds."""
    return {c["id"]: float(c["duration"] or 0) for c in clips.for_draft(draft_id) if c["state"] == "ready"}


def _clips(draft: dict, folder: Path) -> tuple[str, dict[int, float]]:
    """The body's videos for the prompt, each with a still frame the model can open with Read."""
    lines, known = [], {}
    ready = draft_clips(draft["id"])
    body = draft["body"]
    for m in re.finditer(r"!\[([^\]]*)\]\(/api/clips/(\d+)\)", body):
        cid = int(m.group(2))
        clip = clips.get(cid)
        if cid not in ready or cid in known or not clip:
            continue
        copy = folder / f"clip-{cid}.jpg"
        shutil.copyfile(clips.poster_of(clip), copy)
        known[cid] = ready[cid]
        before = plain(body[:m.start()]).strip().splitlines()
        lines.append(f"- 编号 {cid}（{clip['width']}×{clip['height']}，{ready[cid]:.1f} 秒，说明“{m.group(1)[:40]}”，画面 {copy}）："
                     f"前一句是“{(before[-1] if before else '')[-60:]}”")
    return "\n".join(lines) or "（没有视频片段，就不用 clip）", known


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


def _seconds(v) -> float | None:
    try:
        return round(float(v), 1) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def normalize(scene: dict, allowed_images: set[int], keep_type: bool = False, allowed_clips: dict[int, float] | None = None) -> dict:
    """Clean one scene from the model or the editor. keep_type: the author may pick
    "截图放大" before choosing the picture, so an edit keeps the type as chosen.
    allowed_clips: the draft's ready body videos and their lengths (draft_clips)."""
    kind = scene.get("type") if scene.get("type") in TYPES else "points"
    s = {"type": kind, "say": _clip(scene.get("say"), SAY_MAX)}
    for key in ("eyebrow", "title", "accent", "lead", "sub", "box", "footer", "footer_accent", "label", "title2", "accent2",
                "unit", "then_unit", "badge", "command", "sign", "sign_sub", "source"):
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
    s["items"] = _items(scene.get("items"), 4, ("text", "sub", "cue", "accent", "icon")) if scene.get("items") else []
    for item in s["items"]:
        if item.get("icon"):
            item["icon"] = item["icon"][:4]
    keys = scene.get("keys") if isinstance(scene.get("keys"), list) else []
    keys = [k for k in (_clip(x, 12) for x in keys) if k and k in s["say"]][:3]
    if keys:
        s["keys"] = keys
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
    try:
        cid = int(scene.get("clip")) if scene.get("clip") not in (None, "") else None
    except (TypeError, ValueError):
        cid = None
    if cid is not None and cid in (allowed_clips or {}):
        # a stretch of the video: from clip_start to clip_end (the end of the video when left out)
        length = allowed_clips[cid]
        start = min(max(0.0, _seconds(scene.get("clip_start")) or 0.0), max(0.0, length - 0.5))
        end = _seconds(scene.get("clip_end"))
        s.update(clip=cid, clip_start=start)
        if end is not None and start + 0.3 <= end < length - 0.05:
            s["clip_end"] = end
        if scene.get("fill") in FILLS:
            s["fill"] = scene["fill"]
        if scene.get("sound") is True:
            s["sound"] = True
    elif kind == "clip" and not keep_type:
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
    videos, known_clips = _clips(draft, folder)
    prompt = PLAN.format(guide=prompts.voice_guide(settings), lessons=prompts.style_lessons_text(), title=draft["title"],
                         body=draft["body"][:20000], pictures=pictures, clips=videos, context=prompts.context_block(settings, draft),
                         name=who["name"], styles="\n".join(f"- {s.key}（{s.name}）：{s.hint}" for s in vs.STYLES.values()))
    reply = llm.run(prompt, timeout=1200, tools=["Read"], max_turns=len(known) + len(known_clips) + 4, allow_read=True)
    result = llm.extract_json(reply)
    raw = result.get("scenes") if isinstance(result, dict) else result
    scenes = [normalize(s, set(known), allowed_clips=known_clips) for s in (raw or []) if isinstance(s, dict)]
    style = result.get("style") if isinstance(result, dict) and result.get("style") in vs.STYLES else None
    extra = {}
    if isinstance(result, dict) and isinstance(result.get("headline"), list):
        lines = [_clip(x, 14) for x in result["headline"] if str(x).strip()][:2]
        if lines:
            extra["headline"] = lines
            key = _clip(result.get("headline_key"), 10)
            extra["headline_key"] = key if key and key in lines[0] else ""
    scenes = [s for s in scenes if s["say"]][:16]
    if not scenes:
        raise VideoError("模型没有给出可用的脚本")
    if scenes[-1]["type"] == "cta" and not scenes[-1].get("sign"):
        scenes[-1]["sign"] = f"我是 {who['name']}"
    shutil.rmtree(folder, ignore_errors=True)
    return update(draft_id, scenes=scenes, planned_at=db.now(), status="", stage="", error=None,
                  **({"style": style} if style else {}), **extra)


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


# ------------------------------------------------------------------ 原片片段: a stretch of a body video in a scene
#
# The stretch is cut once into a silent H.264 file at the size it is shown,
# then its last frame is held to CLIP_HOLD seconds. A scene lasts as long as its
# line, so it plays the stretch and freezes on the last frame when the line is
# longer, and the render and the editor's preview share one cut whatever the
# line says. The video's own sound is muted unless the scene asks for it; then
# it is cut to the scene, brought to CLIP_LUFS and laid under the voice.

CLIP_HOLD = 36.0     # longer than any scene (SAY_MAX characters plus the overlap)
CLIP_LUFS = -29.0    # the voice lines measure about -17.6 LUFS
CLIP_CUT = "1"       # bump when the cut changes, so cached cuts are made again
CLIP_KEEP = 40       # cuts kept in the cache


def clip_take(scene: dict, length: float) -> tuple[float, float]:
    """Where the stretch starts in the video, and how long it runs."""
    start = float(scene.get("clip_start") or 0.0)
    end = float(scene.get("clip_end") or 0.0) or length or start + CLIP_HOLD
    return start, round(max(0.1, min(end, length or end, start + CLIP_HOLD) - start), 3)


def _link(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def _cut_clip(clip: dict, start: float, take: float, fill: str, frame: vs.Frame) -> tuple[Path, int, int]:
    """The stretch as shown: cropped to the canvas when it fills it, otherwise no
    bigger than the canvas; its last frame held to CLIP_HOLD. Returns the file and its size."""
    W, H = frame.W, frame.H
    if fill == "full":
        size, vf = (W, H), f"crop='min(iw,ih*{W}/{H})':'min(ih,iw*{H}/{W})',scale={W}:{H}:flags=lanczos"
    else:
        k = min(1.0, W / clip["width"], H / clip["height"])
        size = (int(clip["width"] * k) // 2 * 2, int(clip["height"] * k) // 2 * 2)
        vf = f"scale={size[0]}:{size[1]}:flags=lanczos"
    key = hashlib.sha1(json.dumps([clip["file"], start, take, size, CLIP_HOLD, CLIP_CUT]).encode()).hexdigest()[:16]
    folder = data_dir() / "video-cache" / "clips"
    out = folder / f"{key}.mp4"
    if not out.exists():
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f".{key}-{secrets.token_hex(4)}.mp4"
        ffmpeg("-ss", f"{start:.3f}", "-t", f"{take:.3f}", "-i", str(clips.path_of(clip)), "-map", "0:v:0", "-an",
               "-vf", f"{vf},setsar=1,fps=30,tpad=stop_mode=clone:stop_duration={CLIP_HOLD:.0f}", "-t", f"{CLIP_HOLD:.1f}",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-g", "30",
               "-movflags", "+faststart", str(tmp))
        os.replace(tmp, out)
        cuts = sorted((p for p in folder.glob("*.mp4") if not p.name.startswith(".")), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in cuts[CLIP_KEEP:]:  # a render or preview holds its own link to the cut it uses
            old.unlink(missing_ok=True)
    os.utime(out)
    return out, size[0], size[1]


def _clip_for(scene: dict, assets: Path, frame: vs.Frame) -> dict | None:
    """The cut a clip scene plays, placed in the composition's assets."""
    clip = clips.get(scene["clip"]) if scene.get("clip") else None
    if not clip or clip["state"] != "ready" or not clip["width"]:
        return None
    start, take = clip_take(scene, float(clip["duration"] or 0))
    fill = vs.clip_fill(scene.get("fill"), clip["width"], clip["height"], frame)
    cut, w, h = _cut_clip(clip, start, take, fill, frame)
    name = f"clip-{cut.stem}.mp4"
    if not (assets / name).exists():
        _link(cut, assets / name)
    return {"src": f"assets/{name}", "w": w, "h": h, "fill": fill}


def loudness(path: Path) -> float:
    """Integrated loudness in LUFS (-70 for silence)."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-af", "ebur128=framelog=quiet",
                           "-f", "null", "-"], capture_output=True, text=True, env=tool_env())
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", proc.stderr)
    return float(found[-1]) if found else -70.0


def clip_sound(scene: dict, seconds: float, out: Path) -> float:
    """The stretch's own sound for a scene that keeps it: cut to the scene, brought
    to CLIP_LUFS, faded at both ends. Returns its length, 0 when there is nothing to hear."""
    clip = clips.get(scene["clip"]) if scene.get("sound") and scene.get("clip") else None
    if not clip or clip["state"] != "ready" or not clips.probe(clips.path_of(clip))["acodec"]:
        return 0.0
    start, take = clip_take(scene, float(clip["duration"] or 0))
    span = round(min(take, seconds), 3)
    raw = out.with_suffix(".raw.wav")
    ffmpeg("-ss", f"{start:.3f}", "-t", f"{span:.3f}", "-i", str(clips.path_of(clip)), "-map", "0:a:0", "-ar", "48000", "-ac", "2", str(raw))
    level = loudness(raw)
    if level <= -60:
        raw.unlink(missing_ok=True)
        return 0.0
    gain = max(-30.0, min(24.0, CLIP_LUFS - level))
    # alimiter's own auto level would lift it back up: off
    ffmpeg("-i", str(raw), "-af", f"volume={gain:.1f}dB,alimiter=limit=0.5:level=false,afade=t=in:d=0.08,"
           f"afade=t=out:st={max(0.0, span - 0.35):.3f}:d=0.35", str(out))
    raw.unlink(missing_ok=True)
    return span


def draft_voice(settings: Settings, state: dict) -> str:
    picked = state.get("voice")
    return picked if picked in {v["id"] for v in voices(settings)} else default_voice(settings)


def voice_cache(voice: str, spoken: str) -> Path:
    """Where the voice for this exact line and voice is kept (render and preview share it)."""
    cache = data_dir() / "video-cache"
    cache.mkdir(exist_ok=True)
    return cache / (hashlib.sha1(f"{voice}|{INSTRUCTION}|{spoken}".encode()).hexdigest()[:16] + ".wav")


def timed_lines(todo: list[tuple[str, Path, Path]], vocab: str, use_whisper: bool, each=None) -> list[dict]:
    """For each (spoken, cached voice, scene clip): the voice padded into the clip,
    and when each character is said (cached next to the voice). Lines without
    saved times are heard by Whisper together, in one child process;
    ``each(n, of)`` is called as the n-th of them is done."""
    lines, fresh = [], []
    for spoken, raw, clip in todo:
        ffmpeg("-i", str(raw), "-af", f"adelay={int(LEAD_S * 1000)},apad=pad_dur={TAIL_S}", "-ar", "48000", "-ac", "1", str(clip))
        line = {"clip": clip, "raw": raw, "duration": max(2.0, duration_of(clip)), "times": None}
        saved = raw.with_suffix(f".{LEAD_S}-{TAIL_S}{'-w' if use_whisper else ''}.times.json")
        if saved.exists():
            line["times"] = [tuple(t) if t else None for t in json.loads(saved.read_text())]
        else:
            fresh.append((line, spoken, saved))
        lines.append(line)
    heard = [[] for _ in fresh]
    if use_whisper and fresh:
        heard = heard_chars([line["clip"] for line, _, _ in fresh], vocab, each=each and (lambda n: each(n, len(fresh))))
    for (line, spoken, saved), chars in zip(fresh, heard):
        line["times"] = align(spoken, chars, line["duration"])
        saved.write_text(json.dumps(line["times"]))
    return lines


def timed_line(spoken: str, raw: Path, clip: Path, vocab: str, use_whisper: bool) -> dict:
    return timed_lines([(spoken, raw, clip)], vocab, use_whisper)[0]


def draft_style(state: dict) -> vs.Style:
    return vs.style_for(state.get("style"))


def build_project(settings: Settings, draft_id: int, fmt: str = "vertical") -> tuple[Path, list[dict], list[float], str]:
    """Voice, timing and every composition, ready for the HyperFrames CLI."""
    draft = db.row("SELECT * FROM drafts WHERE id=?", (draft_id,))
    state = load(draft)
    frame, style = vs.frame_for(fmt), draft_style(state)
    scenes = [s for s in state.get("scenes") or [] if str(s.get("say", "")).strip()]
    if not scenes:
        raise VideoError("脚本是空的")
    voice = draft_voice(settings, state)
    key = tts_key(settings, voice)
    root = work_dir(draft_id, fmt)
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

    # 2. when each character is said (cached next to the voice clip; new lines in one Whisper run)
    _progress(draft_id, "对齐字幕", 30)
    timed = timed_lines([(spoken, raw, assets / "audio" / f"voice-{n:02d}.wav") for n, (spoken, _segs, raw) in enumerate(todo, 1)],
                        settings.transcribe_vocab, whisper_available(),
                        each=lambda n, of: _progress(draft_id, f"对齐字幕 {n}/{of}", int(30 + 15 * n / of)))
    lines = [{**line, "spoken": spoken, "segs": segs} for line, (spoken, segs, _raw) in zip(timed, todo)]

    # 3. compositions
    _progress(draft_id, "准备画面", 46)
    starts, t = [], 0.0
    for line in lines:
        starts.append(round(t, 3))
        t += line["duration"]
    total = round(t, 3)
    chunks = []
    for scene, line, start in zip(scenes, lines, starts):
        for ch in mark_keys(caption_chunks(scene["say"], line["spoken"], line["segs"], line["times"]), caption_keys(scene)):
            chunks.append({**ch, "start": round(ch["start"] + start, 3), "end": round(ch["end"] + start, 3)})
    groups = caption_groups(chunks, frame.CAP_CELLS)
    sounds = []  # a clip's own sound, where a scene keeps it: (file, start, seconds)
    for i, (scene, line) in enumerate(zip(scenes, lines)):
        sid = f"s{i + 1:02d}"
        data = dict(scene)
        if scene.get("image"):
            data["image"] = _image_for(scene, assets)
        if scene["type"] == "clip":
            _progress(draft_id, f"截取片段（镜头 {i + 1}）", 47)
            data["clip"] = _clip_for(scene, assets, frame)
            sound = assets / "audio" / f"clip-{i + 1:02d}.wav"
            if data["clip"] and (span := clip_sound(scene, line["duration"], sound)):
                sounds.append((f"assets/audio/{sound.name}", starts[i], span))
        cue = make_cue(scene["say"], line["segs"], line["times"], 0.0, line["duration"])
        host = line["duration"] + (vs.OVERLAP if i + 1 < len(scenes) else 0)
        (root / "compositions" / f"{sid}.html").write_text(
            vs.scene_html(sid, data, host, cue, final=(i + 1 == len(scenes)), frame=frame, style=style), encoding="utf-8")
    (root / "compositions" / "captions.html").write_text(vs.captions_html(groups, total, frame, style), encoding="utf-8")
    author = settings.author or prompts.persona(settings)["name"]
    headline, head_keys = headline_of(state, draft["title"])
    (root / "compositions" / "chrome.html").write_text(
        vs.chrome_html(author, settings.video_tagline, total, frame, style, headline=headline, head_keys=head_keys,
                       marks=[s / total for s in starts[1:]]), encoding="utf-8")
    bgm = None
    bgm_src = Path(settings.video_bgm).expanduser() if settings.video_bgm else data_dir() / "video" / "bgm.wav"
    if state.get("bgm", True) and bgm_src.is_file():
        ffmpeg("-i", str(bgm_src), "-t", f"{total:.3f}", "-af",
               f"afade=t=in:st=0:d=0.6,afade=t=out:st={max(0.0, total - 3.0):.3f}:d=3.0", "-ar", "48000", "-ac", "2",
               str(assets / "audio" / "bgm.wav"))
        bgm = "assets/audio/bgm.wav"
    voices = [f"assets/audio/voice-{n:02d}.wav" for n in range(1, len(lines) + 1)]
    (root / "index.html").write_text(vs.index_html(scenes, starts, [ln["duration"] for ln in lines], voices, bgm, 0.11, total,
                                                   frame, style, sounds=sounds), encoding="utf-8")
    (root / "hyperframes.json").write_text(json.dumps({"paths": {"assets": "assets"}}), encoding="utf-8")
    shown = vs.all_text(scenes) + "".join(w["text"] for g in groups for w in g["words"]) + author + settings.video_tagline + "".join(headline)
    subset_fonts(shown, assets / "fonts", style.faces)
    return root, lines, starts, style.key


def render(settings: Settings, draft_id: int, fmt: str = "vertical") -> dict:
    root, lines, starts, style = build_project(settings, draft_id, fmt)
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
    final = video_path(draft_id, fmt)
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(out), final)
    info = {"duration": duration_of(final), "size": final.stat().st_size, "rendered_at": db.now(), "style": style,
            "scenes": [{"start": s, "duration": ln["duration"]} for s, ln in zip(starts, lines)]}
    return update(draft_id, **{render_key(fmt): info}, status="", stage="", pct=100, error=None, rendering=None)


# ------------------------------------------------------------------ instant preview of one scene

PREVIEW_KEEP = 8  # built previews kept per draft
# a preview is kept under a hash of what it shows, and of the code that drew it
_PREVIEW_CODE = hashlib.sha1(b"".join(Path(m.__file__).read_bytes() for m in (vs, vs_styles)) + Path(__file__).read_bytes()).hexdigest()[:8]


def preview_dir(draft_id: int) -> Path:
    return data_dir() / "video-preview" / f"draft-{draft_id}"


def preview(settings: Settings, draft_id: int, index: int, scene: dict, last: bool = False, voice_now: bool = False,
            fmt: str = "vertical") -> dict:
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
    s = normalize(scene, {im["id"] for im in images.for_draft(draft_id)}, keep_type=True, allowed_clips=draft_clips(draft_id))
    if not s["say"].strip():
        raise VideoError("这个镜头还没写口播")
    shown_clip = clips.get(s["clip"]) if s["type"] == "clip" and s.get("clip") else None
    voice = draft_voice(settings, state)
    frame, style = vs.frame_for(fmt), draft_style(state)
    spoken, segs = speakable(s["say"])
    raw = voice_cache(voice_key(settings, voice), spoken)
    if voice_now and not raw.exists():
        speak(settings, tts_key(settings, voice), spoken, voice, raw)
    voiced = raw.exists()
    author = settings.author or prompts.persona(settings)["name"]
    key = hashlib.sha1(json.dumps([s, voice_key(settings, voice), voiced, index, last, author, settings.video_tagline, frame.key, style.key,
                                   headline_of(state, draft["title"]), [estimate_seconds([x]) for x in state.get("scenes") or []],
                                   shown_clip and shown_clip["file"], _PREVIEW_CODE], ensure_ascii=False,
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
        sound = []
        if shown_clip:
            data["clip"] = _clip_for(s, assets, frame)
            if data["clip"] and (seconds := clip_sound(s, duration, assets / "audio" / "clip.wav")):
                sound = [f'<audio id="clip-sound" src="assets/audio/clip.wav" data-start="0" data-duration="{seconds:.3f}"></audio>']
        sid = f"s{index + 1:02d}"
        cue = make_cue(s["say"], segs, times, 0.0, duration)
        groups = caption_groups(mark_keys(caption_chunks(s["say"], spoken, segs, times), caption_keys(s)), frame.CAP_CELLS)
        headline, head_keys = headline_of(state, draft["title"])
        est = [estimate_seconds([x]) for x in state.get("scenes") or []] or [duration]
        before, whole = sum(est[:index]), sum(est) or 1.0
        span = (min(1.0, before / whole), min(1.0, (before + (est[index] if index < len(est) else duration)) / whole))
        marks = [sum(est[:i]) / whole for i in range(1, len(est))]
        parts = [vs.scene_html(sid, data, duration, cue, final=last, frame=frame, style=style), vs.captions_html(groups, duration, frame, style),
                 vs.chrome_html(author, settings.video_tagline, duration, frame, style, headline=headline, head_keys=head_keys,
                                marks=marks, fade=False, span=span)] + sound
        # the mark has long finished fading in by the second scene
        (tmp / "index.html").write_text(vs.preview_html(parts, duration, {"chrome": 0.0 if index == 0 else 1.0}, frame, style), encoding="utf-8")
        shown = vs.all_text([s]) + "".join(w["text"] for g in groups for w in g["words"]) + author + settings.video_tagline + "".join(headline)
        subset_fonts(shown, assets / "fonts", style.faces)
        (tmp / "meta.json").write_text(json.dumps({"duration": duration, "voiced": voiced, "format": frame.key}), encoding="utf-8")
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


# ------------------------------------------------------------------ title, intro and cover for publishing
#
# Written by the model after the script (and on request), edited by the author,
# sent with the render: the title and intro go into the X post and the 公众号
# 素材库 entry and draft; the cover is drawn in the video's style.

META = """给 {name} 的这条口播短视频写发布用的标题、简介和封面字。视频内容来自下面这篇长文，脚本在后面。

<文风指南摘要>
{guide}
</文风指南摘要>

{lessons}

<长文标题>
{title}
</长文标题>

<长文正文（节选）>
{body}
</长文正文（节选）>

<视频脚本（口播）>
{script}
</视频脚本（口播）>

{context}要求：
- title：视频标题，14 到 24 个字，说清楚这条视频讲什么、看完能得到什么；可以有钩子，但不要标题党，不要“震惊”“必看”“保姆级”“干货”这类词，不要用引号或书名号包住整句。
- intro：简介，2 到 4 句、60 到 110 个字，{name} 第一人称、口语，像发视频时随手写的一段话：这事是什么、为什么做，再落到一个具体的结果或数字；不要“家人们”，不要堆 emoji，不要 # 话题标签，不要“点赞关注”。
- cover：封面大字。lines 是 1 到 2 行，每行不超过 8 个字，是整条视频最抓人的那句话（可以和标题不一样，更短更狠）；key 是 lines 里原样出现的一个词（2 到 5 个字），会标成强调色；kicker 是大字上面的小标签（不超过 8 个字，比如“已开源”“实测 3 天”），没有合适的就写空字符串。
- 只用长文和脚本里有的事实和数字，不编。

只输出一个 JSON 对象：{{"title": "...", "intro": "...", "cover": {{"lines": ["...", "..."], "key": "...", "kicker": "..."}}}}
"""
META_TITLE_MAX, META_INTRO_MAX = 30, 160


def default_cover_image(scenes: list[dict]) -> int | None:
    """The first screenshot the video shows, for the cover."""
    for s in scenes:
        if s.get("type") in ("shot", "cta") and s.get("image"):
            return int(s["image"])
    return None


def clean_meta(raw: dict, allowed_images: set[int], old: dict | None = None) -> dict:
    """Title, intro and cover words from the model or the editor, within the limits."""
    old = old or {}
    cover = raw.get("cover") if isinstance(raw.get("cover"), dict) else {}
    old_cover = old.get("cover") or {}
    lines = [_clip(x, 12) for x in cover.get("lines", old_cover.get("lines") or []) if str(x).strip()][:2]
    key = _clip(cover.get("key", old_cover.get("key")), 8)
    image = cover.get("image", old_cover.get("image"))
    try:
        image = int(image) if image not in (None, "", 0) else None
    except (TypeError, ValueError):
        image = None
    return {"title": _clip(raw.get("title", old.get("title")), META_TITLE_MAX).replace("\n", " "),
            "intro": _clip(raw.get("intro", old.get("intro")), META_INTRO_MAX),
            "cover": {"lines": lines, "key": key if key and any(key in x for x in lines) else "",
                      "kicker": _clip(cover.get("kicker", old_cover.get("kicker")), 10),
                      "image": image if image in allowed_images else None}}


def write_meta(settings: Settings, draft_id: int) -> dict:
    draft = db.row("SELECT * FROM drafts WHERE id=?", (draft_id,))
    if not draft:
        raise VideoError("找不到这篇长文")
    state = load(draft)
    scenes = state.get("scenes") or []
    script = "\n".join(s["say"] for s in scenes if s.get("say")) or "（还没有脚本）"
    who = prompts.persona(settings)
    prompt = META.format(name=who["name"], guide=prompts.voice_guide(settings), lessons=prompts.style_lessons_text(),
                         title=draft["title"], body=draft["body"][:8000], script=script, context=prompts.context_block(settings, draft))
    result = llm.extract_json(llm.run(prompt, timeout=300))
    if not isinstance(result, dict) or not str(result.get("title") or "").strip():
        raise VideoError("模型没有给出标题和简介")
    allowed = {im["id"] for im in images.for_draft(draft_id)}
    meta = clean_meta(result, allowed)
    if meta["cover"]["image"] is None:
        meta["cover"]["image"] = default_cover_image(scenes) if default_cover_image(scenes) in allowed else None
    meta["generated_at"] = meta["edited_at"] = db.now()
    state = update(draft_id, meta=meta, meta_status="", meta_error=None)
    for fmt in FORMATS:  # drawn now, so the 发布 page opens on them
        try:
            cover(settings, draft_id, fmt)
        except Exception as exc:  # drawn again when the page asks
            print(f"cover of draft {draft_id} ({fmt}) not drawn: {exc}", flush=True)
    return state


_COVER_LOCK = threading.Lock()


def cover_path(draft_id: int, fmt: str = "vertical") -> Path:
    return data_dir() / "videos" / f"draft-{draft_id}-cover{_suffix(fmt)}.jpg"


def cover(settings: Settings, draft_id: int, fmt: str = "vertical") -> Path:
    """The cover for one format, drawn again only when what it shows has changed."""
    draft = db.row("SELECT * FROM drafts WHERE id=?", (draft_id,))
    state = load(draft or {})
    meta = state.get("meta")
    if not meta:
        raise VideoError("还没有封面，先生成标题和简介")
    frame, style = vs.frame_for(fmt), draft_style(state)
    author = settings.author or prompts.persona(settings)["name"]
    c = meta.get("cover") or {}
    image = images.get(c["image"]) if c.get("image") else None
    key = hashlib.sha1(json.dumps([c, image and image["file"], author, settings.video_tagline, fmt, style.key, _PREVIEW_CODE],
                                  ensure_ascii=False).encode()).hexdigest()[:12]
    out = cover_path(draft_id, fmt)
    stamp = out.with_suffix(".key")
    with _COVER_LOCK:
        if out.exists() and stamp.exists() and stamp.read_text() == key:
            return out
        folder = work_dir(draft_id, fmt) / "cover"
        shutil.rmtree(folder, ignore_errors=True)
        (folder / "assets").mkdir(parents=True)
        shown = None
        if image:
            shown = _image_for({"image": image["id"]}, folder / "assets")
        text = "".join(c.get("lines") or []) + (c.get("kicker") or "") + author + settings.video_tagline + "（封面大字）"
        subset_fonts(text, folder / "assets" / "fonts", style.faces)
        page = folder / "index.html"
        page.write_text(vs.cover_html(c.get("lines") or [], c.get("key") or "", c.get("kicker") or "", author,
                                      settings.video_tagline, frame, style, shown), encoding="utf-8")
        from playwright.sync_api import sync_playwright
        out.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                tab = browser.new_page(viewport={"width": frame.W, "height": frame.H}, device_scale_factor=1)
                tab.goto(page.as_uri())
                tab.evaluate("document.fonts.ready")
                tab.wait_for_load_state("networkidle")
                tab.screenshot(path=str(out), type="jpeg", quality=92)
            finally:
                browser.close()
        stamp.write_text(key)
        shutil.rmtree(folder, ignore_errors=True)
    return out


def cover_key(draft_id: int, fmt: str) -> str:
    stamp = cover_path(draft_id, fmt).with_suffix(".key")
    return stamp.read_text() if stamp.exists() else ""


# ------------------------------------------------------------------ sending a render to X / 公众号
#
# X takes the render as it is and posts it straight away (X has no drafts for
# posts). 公众号's draft/add cannot carry a video, so the render goes into the
# 素材库 and the author inserts it in the editor. Each send is a publications
# row: ``pending`` while it runs, then sent / failed / unknown. Progress lives
# in memory; a send cut off by a restart is closed by recover_sends.

SEND_PLATFORMS = {"x": "x_video", "wechat": "wechat_video"}
_SENDING: dict[int, dict] = {}


def shrink(src: Path, fmt: str, max_bytes: int) -> Path:
    """The render itself, or a 720p copy that fits an upload cap (two-pass H.264)."""
    if src.stat().st_size <= max_bytes:
        return src
    out = src.with_name(src.stem + "-small.mp4")
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime and out.stat().st_size <= max_bytes:
        return out
    seconds = max(duration_of(src), 1.0)
    log = str(src.with_name(src.stem + "-pass"))
    try:
        for share in (0.9, 0.78, 0.66):
            rate = int(max_bytes * 8 * share / seconds) - 96_000
            if rate < 250_000:
                break
            common = ["-i", str(src), "-vf", "scale=720:-2" if fmt == "vertical" else "scale=-2:720", "-pix_fmt", "yuv420p",
                      "-c:v", "libx264", "-preset", "slow", "-b:v", str(rate), "-passlogfile", log]
            ffmpeg(*common, "-pass", "1", "-an", "-f", "mp4", os.devnull)
            ffmpeg(*common, "-pass", "2", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out))
            if out.stat().st_size <= max_bytes:
                return out
    finally:
        for p in src.parent.glob(src.stem + "-pass*"):
            p.unlink(missing_ok=True)
    out.unlink(missing_ok=True)
    raise VideoError(f"视频有 {seconds:.0f} 秒，压到 {max_bytes // 1024 // 1024}MB 以内会糊，去公众号后台手动上传原片")


def sends(draft_id: int) -> list[dict]:
    rows = db.rows("SELECT * FROM publications WHERE draft_id=? AND platform IN ('x_video', 'wechat_video') ORDER BY id DESC",
                   (draft_id,))
    return [{**r, **_SENDING.get(r["id"], {})} for r in rows]


def send(settings: Settings, store, draft: dict, target: str, fmt: str, text: str = "") -> dict:
    """Start sending one render; returns the pending publications row."""
    platform = SEND_PLATFORMS[target]
    info = load(draft).get(render_key(fmt))
    if not info or not video_path(draft["id"], fmt).exists():
        raise VideoError(f"还没出{'竖屏' if fmt == 'vertical' else '横屏'}片")
    detail = {"format": fmt, "rendered_at": info.get("rendered_at"), "stage": "upload"}
    if target == "x":
        detail["text"] = text.strip()
    with db.tx() as conn:
        if conn.execute("SELECT 1 FROM publications WHERE draft_id=? AND platform=? AND state='pending'",
                        (draft["id"], platform)).fetchone():
            raise VideoError("上一次还没发完，等它结束")
        pid = conn.execute("INSERT INTO publications(draft_id, platform, state, detail, created_at) VALUES (?,?,?,?,?)",
                           (draft["id"], platform, "pending", db.dumps(detail), db.now())).lastrowid
    _SENDING[pid] = {"stage": "排队中", "pct": 0}
    threading.Thread(target=_send, args=(settings, store, dict(draft), pid, target, fmt, detail), daemon=True,
                     name=f"cw-send-{pid}").start()
    return db.row("SELECT * FROM publications WHERE id=?", (pid,))


def _uploaded_to_wechat(draft_id: int, fmt: str, rendered_at) -> str | None:
    """This very render already in the 素材库 (a draft that failed after it, or a second send)."""
    for r in db.rows("SELECT detail FROM publications WHERE draft_id=? AND platform='wechat_video' AND state!='pending' ORDER BY id DESC",
                     (draft_id,)):
        d = db.loads(r["detail"], {}) or {}
        if d.get("video_media_id") and d.get("format") == fmt and d.get("rendered_at") == rendered_at:
            return d["video_media_id"]
    return None


def _send(settings: Settings, store, draft: dict, pid: int, target: str, fmt: str, detail: dict) -> None:
    from . import publish
    from .app import wechat_crops
    from .textutil import digest, wechat_html

    def step(stage: str, pct: float) -> None:
        _SENDING[pid] = {"stage": stage, "pct": int(pct)}

    def write(state: str, remote: str | None = None, **extra) -> None:
        detail.update(extra)
        with db.tx() as conn:
            conn.execute("UPDATE publications SET state=?, remote_id=?, detail=? WHERE id=?", (state, remote, db.dumps(detail), pid))

    def past_the_point() -> None:
        # from here on a cut-off request may still have gone through
        detail["stage"] = "posting"
        write("pending")

    path = video_path(draft["id"], fmt)
    meta = load(draft).get("meta") or {}
    title = (meta.get("title") or draft.get("title") or "视频").strip()
    intro = (meta.get("intro") or draft.get("summary") or "").strip() or digest(draft.get("body") or "")
    try:
        if target == "x":
            step("传到 X", 0)
            media = publish.x_upload_video(store, path, lambda done: step("传到 X", done * 85))
            if meta:
                step("设封面", 88)
                try:
                    data = cover(settings, draft["id"], fmt).read_bytes()
                    image_id, _ = publish.x_upload_image(store, data, "image/jpeg", "cover.jpg")
                    publish.x_set_video_cover(store, media, image_id)
                    detail["cover"] = True
                except (publish.PublishError, VideoError) as exc:
                    detail["cover_error"] = str(exc)  # the post still goes out, with X's own frame
            step("发帖", 95)
            past_the_point()
            result = publish.x_post_video(store, detail.get("text", ""), media)
            detail.pop("stage")
            write("sent", result["ids"][0], **result)
        else:
            media = _uploaded_to_wechat(draft["id"], fmt, detail.get("rendered_at"))
            if not media:
                step("压到 10MB 以内", 5)
                small = shrink(path, fmt, publish.WX_VIDEO_MAX)
                step("传到公众号素材库", 40)
                past_the_point()
                media = publish.wx_upload_video(store, small, title, intro)
                write("pending", video_media_id=media, bytes=small.stat().st_size)
            detail["video_media_id"] = media
            step("建公众号草稿", 85)
            thumb, crop_235, crop_11 = store.get("wx_default_cover"), "", ""
            if meta:
                art = cover(settings, draft["id"], "landscape")
                thumb = publish.wx_upload_material(store, art.read_bytes(), "image/jpeg", art.name)
                crop_235, crop_11 = wechat_crops(vs.LANDSCAPE.W, vs.LANDSCAPE.H, 0.5)
            past_the_point()
            try:
                made = publish.wx_draft(store, title, intro, settings.author, thumb, html=wechat_html(intro), summary=intro,
                                        crop_235=crop_235, crop_11=crop_11)
            except publish.PublishError as exc:
                raise publish.PublishError(f"视频已经进了素材库，但图文草稿没建成：{exc}", uncertain=exc.uncertain, detail=exc.detail) from None
            detail.pop("stage")
            write("sent", media, draft_media_id=made["media_id"])
    except Exception as exc:  # recorded on the row; nothing is retried
        known = isinstance(exc, (publish.PublishError, VideoError))
        unsure = getattr(exc, "uncertain", False) or (not known and detail.get("stage") == "posting")
        detail.pop("stage", None)
        write("unknown" if unsure else "failed", error=str(exc) if known else f"出错了：{exc}", **getattr(exc, "detail", {}))
    finally:
        _SENDING.pop(pid, None)


def recover_sends() -> None:
    """Close sends that a restart cut off. One that had started the final request may have gone out."""
    for r in db.rows("SELECT id, detail FROM publications WHERE state='pending'"):
        detail = db.loads(r["detail"], {}) or {}
        posting = detail.pop("stage", "") == "posting"
        detail["error"] = "上次发到一半服务重启了，" + ("结果不确定，去平台上看一眼" if posting else "没发出去，可以重新发")
        with db.tx() as conn:
            conn.execute("UPDATE publications SET state=?, detail=? WHERE id=?",
                         ("unknown" if posting else "failed", db.dumps(detail), r["id"]))


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
                    "style": draft_style(v).key, "video": v.get("video"), "video_landscape": v.get("video_landscape")})
    return out


def options(settings: Settings) -> dict:
    key_ok = any(f and Path(f).expanduser().is_file()
                 for f in (settings.evolink_key_file, minimax_clone(settings) and settings.minimax_key_file))
    return {"voices": voices(settings), "default_voice": default_voice(settings), "tts": key_ok,
            "whisper": whisper_available(), "types": TYPE_NAMES,
            "styles": [{"id": s.key, "name": s.name, "hint": s.hint, "headline": s.headline} for s in vs.STYLES.values()],
            "formats": [{"id": k, "name": n} for k, n in vs.FORMAT_NAMES.items()],
            "bgm": bool((Path(settings.video_bgm).expanduser() if settings.video_bgm else data_dir() / "video" / "bgm.wav").is_file())}
