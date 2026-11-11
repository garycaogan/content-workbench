"""Videos inside a long-form body (not the 视频 tab, which makes a short video
out of the article).

The author picks a video in the editor. It comes up in pieces (the Cloudflare
gateway takes at most 100 MB per request), then a job turns it into an
H.264/AAC MP4 that X accepts and every browser plays, plus a still frame. The
body refers to it like a picture: ``![说明](/api/clips/<id>)``.

Where it goes:
- X Articles: uploaded as ``tweet_video`` and placed as a media block, so it
  plays inside the article. The Articles API carries it in an ``image`` entity
  whose media item says ``media_category: tweet_video`` (checked against a real
  draft on 2026-10-27: the editor on x.com plays it).
- 公众号: an API-uploaded video can't go into an article (see app._push_wechat),
  so the still frame stands in with a line saying which file to upload there.
- 知识星球: articles have no video at all (the editor has no video button,
  only image and audio), so the still frame stands in, with a "watch" line
  when the author gave the video a link.
- Feishu archive: an inline player."""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import subprocess
from pathlib import Path

from PIL import Image

from . import db
from .config import data_dir

MAX_BYTES = 1024 * 1024 * 1024          # one video, as picked
CHUNK_MAX = 32 * 1024 * 1024            # one upload request
REF_RE = re.compile(r"/api/clips/(\d+)")
# X: at most 1920×1200 (or 1200×1920) and 60 fps; anything bigger is scaled down
LONG_SIDE, SHORT_SIDE, FPS_MAX = 1920, 1200, 60


class ClipError(ValueError):
    pass


def clips_dir() -> Path:
    path = data_dir() / "clips"
    path.mkdir(exist_ok=True)
    path.chmod(0o700)
    return path


def _env() -> dict:
    env = dict(os.environ)  # launchd's PATH has no ffmpeg
    env["PATH"] = ":".join([str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "/usr/bin:/bin")])
    return env


def _run(*args: str, timeout: int = 3600) -> str:
    proc = subprocess.run(list(args), capture_output=True, text=True, env=_env(), timeout=timeout)
    if proc.returncode:
        raise ClipError(f"{args[0]} 出错：{proc.stderr.strip()[-300:]}")
    return proc.stdout


# ------------------------------------------------------------------ rows

def get(clip_id: int) -> dict | None:
    return db.row("SELECT * FROM clips WHERE id=?", (clip_id,))


def public(clip: dict) -> dict:
    return {k: clip[k] for k in ("id", "draft_id", "name", "state", "error", "width", "height", "duration", "bytes",
                                 "size", "received", "link", "created_at")}


def for_draft(draft_id: int) -> list[dict]:
    return [public(c) for c in db.rows("SELECT * FROM clips WHERE draft_id=? ORDER BY id", (draft_id,))]


def path_of(clip: dict) -> Path:
    return clips_dir() / f"{clip['file']}.mp4"


def poster_of(clip: dict) -> Path:
    return clips_dir() / f"{clip['file']}.jpg"


def _part(clip: dict) -> Path:
    return clips_dir() / f"{clip['file']}.part"


def referenced(markdown: str) -> list[int]:
    """Video ids used in the body, in order, without duplicates."""
    seen: list[int] = []
    for m in re.finditer(r"!\[[^\]]*\]\(/api/clips/(\d+)\)", markdown):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def as_frames(markdown: str) -> str:
    """The body with every video replaced by its still frame, for platforms
    whose articles cannot hold a video."""
    return re.sub(r"(!\[[^\]]*\]\(/api/clips/\d+)\)", r"\1/poster.jpg)", markdown)


def frame_ref(clip_id: int) -> str:
    return f"/api/clips/{clip_id}/poster.jpg"


def frames_with_notes(markdown: str, note) -> str:
    """Like as_frames, and a video on a line of its own also gets the paragraph
    ``note(clip_id)`` right under its frame (nothing when that is empty)."""
    def line(m: re.Match) -> str:
        extra = note(int(m.group(2)))
        return m.group(0) + (f"\n\n{extra}" if extra else "")
    return as_frames(re.sub(r"^(!\[[^\]]*\]\(/api/clips/(\d+)\))[ \t]*$", line, markdown, flags=re.M))


# ------------------------------------------------------------------ upload

def create(draft_id: int, name: str, size: int) -> dict:
    if size <= 0:
        raise ClipError("视频是空的")
    if size > MAX_BYTES:
        raise ClipError(f"视频太大了，单个不超过 {MAX_BYTES // 1024 // 1024 // 1024}GB")
    with db.tx() as conn:
        cur = conn.execute("INSERT INTO clips(draft_id, name, file, size, created_at) VALUES (?,?,?,?,?)",
                           (draft_id, (name or "视频")[:120], secrets.token_hex(10), size, db.now()))
    clip = get(cur.lastrowid)
    fd = os.open(_part(clip), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    return clip


def append(clip: dict, offset: int, data: bytes) -> int:
    """Write one piece at ``offset``; a piece sent twice is ignored. Returns bytes received."""
    if clip["state"] != "uploading":
        raise ClipError("这段视频已经传完了")
    if len(data) > CHUNK_MAX:
        raise ClipError("一次传的太多了")
    have = clip["received"]
    if offset + len(data) <= have:
        return have
    if offset != have:
        raise ClipError(f"顺序乱了：服务器收到 {have} 字节，这一段从 {offset} 开始")
    if have + len(data) > clip["size"]:
        raise ClipError("传上来的比文件本身还大")
    with _part(clip).open("ab") as f:
        f.write(data)
    with db.tx() as conn:
        conn.execute("UPDATE clips SET received=? WHERE id=?", (have + len(data), clip["id"]))
    return have + len(data)


def finish(clip: dict) -> None:
    if clip["state"] != "uploading":
        return
    if clip["received"] != clip["size"]:
        raise ClipError(f"还没传完：{clip['received']}/{clip['size']} 字节")
    with db.tx() as conn:
        conn.execute("UPDATE clips SET state='processing' WHERE id=?", (clip["id"],))


def fail(clip_id: int, message: str) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE clips SET state='failed', error=? WHERE id=?", (message[:300], clip_id))


def delete(clip_id: int) -> None:
    clip = get(clip_id)
    if not clip:
        return
    with db.tx() as conn:
        conn.execute("DELETE FROM clips WHERE id=?", (clip_id,))
    for p in (path_of(clip), poster_of(clip), _part(clip), clips_dir() / f"{clip['file']}-small.mp4"):  # -small: 10 MB copy an earlier build made
        p.unlink(missing_ok=True)


def set_link(clip_id: int, link: str) -> None:
    link = link.strip()
    if link and not re.match(r"^https?://\S+$", link):
        raise ClipError("观看链接要填一个完整的网址")
    with db.tx() as conn:
        conn.execute("UPDATE clips SET link=? WHERE id=?", (link[:500], clip_id))


def minutes(seconds: float | None) -> str:
    n = round(seconds or 0)
    return f"{n // 60}:{n % 60:02d}"


# ------------------------------------------------------------------ processing (a job)

def probe(path: Path) -> dict:
    out = json.loads(_run("ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path),
                          timeout=120) or "{}")
    streams = out.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    if not video:
        raise ClipError("这个文件里没有视频画面")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    rotation = 0
    for side in video.get("side_data_list") or []:
        rotation = rotation or int(float(side.get("rotation") or 0))
    rotation = rotation or int(float((video.get("tags") or {}).get("rotate") or 0))
    num, _, den = str(video.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num or 0) / float(den or 1) if float(den or 1) else 0.0
    return {"vcodec": video.get("codec_name"), "pix_fmt": video.get("pix_fmt"), "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0), "fps": fps, "rotation": rotation,
            "acodec": audio.get("codec_name") if audio else None,
            "container": (out.get("format") or {}).get("format_name") or "",
            "duration": float((out.get("format") or {}).get("duration") or video.get("duration") or 0)}


def playable(info: dict) -> bool:
    """Already what X and browsers take, so a remux (no re-encode) is enough."""
    long_side, short_side = max(info["width"], info["height"]), min(info["width"], info["height"])
    return (info["vcodec"] == "h264" and info["pix_fmt"] in ("yuv420p", "yuvj420p") and not info["rotation"]
            and long_side <= LONG_SIDE and short_side <= SHORT_SIDE and 0 < info["fps"] <= FPS_MAX
            and info["acodec"] in (None, "aac") and ("mp4" in info["container"] or "mov" in info["container"]))


def process(clip_id: int) -> None:
    clip = get(clip_id)
    if not clip or clip["state"] != "processing":
        return
    src, out, poster = _part(clip), path_of(clip), poster_of(clip)
    info = probe(src)
    tmp = out.with_suffix(".tmp.mp4")
    common = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1"]
    if playable(info):
        _run(*common, "-c", "copy", "-movflags", "+faststart", str(tmp))
    else:
        # the display size after rotation decides which way the 1920×1200 box lies
        landscape = (info["width"] >= info["height"]) != (abs(info["rotation"]) % 180 == 90)
        w, h = (LONG_SIDE, SHORT_SIDE) if landscape else (SHORT_SIDE, LONG_SIDE)
        _run(*common, "-vf", f"scale='min(iw,{w})':'min(ih,{h})':force_original_aspect_ratio=decrease:force_divisible_by=2",
             "-fpsmax", str(FPS_MAX), "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
             "-profile:v", "high", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart", str(tmp))
    os.replace(tmp, out)
    out.chmod(0o600)
    final = probe(out)
    seconds = final["duration"] or info["duration"]
    _run("ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.0, seconds / 3):.2f}", "-i", str(out), "-frames:v", "1",
         "-q:v", "3", str(poster), timeout=120)
    poster.chmod(0o600)
    src.unlink(missing_ok=True)
    with db.tx() as conn:
        conn.execute("UPDATE clips SET state='ready', error=NULL, width=?, height=?, duration=?, bytes=? WHERE id=?",
                     (final["width"], final["height"], round(seconds, 3), out.stat().st_size, clip_id))


def poster_bytes(clip: dict, *, max_bytes: int, max_side: int = 4096) -> bytes:
    """The still frame as a JPEG under ``max_bytes`` (scaled down if needed)."""
    raw = poster_of(clip).read_bytes()
    with Image.open(io.BytesIO(raw)) as img:
        if len(raw) <= max_bytes and max(img.size) <= max_side:
            return raw
        img = img.convert("RGB")
        side = min(max_side, max(img.size))
        for _ in range(8):
            scaled = img.copy()
            scaled.thumbnail((side, side), Image.LANCZOS)
            for quality in (88, 80, 72):
                out = io.BytesIO()
                scaled.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
                if out.tell() <= max_bytes:
                    return out.getvalue()
            side = int(side * 0.8)
    raise ClipError(f"{clip['name']} 的画面压不到平台要求的大小")


# ------------------------------------------------------------------ uploads to platforms, cached per video

def remote(clip_id: int, target: str) -> dict | None:
    found = db.row("SELECT * FROM clip_remotes WHERE clip_id=? AND target=?", (clip_id, target))
    if found and found["expires_at"] and found["expires_at"] < db.now():
        return None
    return found


def remember(clip_id: int, target: str, remote_id: str | None, url: str | None, expires_at: float | None = None) -> None:
    with db.tx() as conn:
        conn.execute("""INSERT INTO clip_remotes(clip_id, target, remote_id, url, expires_at, created_at) VALUES (?,?,?,?,?,?)
                        ON CONFLICT(clip_id, target) DO UPDATE SET remote_id=excluded.remote_id, url=excluded.url,
                        expires_at=excluded.expires_at, created_at=excluded.created_at""",
                     (clip_id, target, remote_id, url, expires_at, db.now()))
