"""Images attached to drafts: covers and pictures in the body.

Files live in the private data directory (0600). The body refers to them as
``![说明](/api/images/<id>)``; on publish each platform gets its own upload
(公众号 inline URL, 公众号 cover material, X media id), cached per image so a
retry does not upload twice."""

from __future__ import annotations

import io
import re
import secrets
from pathlib import Path

from PIL import Image, ImageOps

from . import db
from .config import data_dir

ALLOWED = {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg"), "GIF": ("image/gif", ".gif"),
           "WEBP": ("image/webp", ".webp")}
MAX_UPLOAD = 20 * 1024 * 1024
REF_RE = re.compile(r"/api/images/(\d+)")


class ImageError(ValueError):
    pass


def images_dir() -> Path:
    path = data_dir() / "images"
    path.mkdir(exist_ok=True)
    path.chmod(0o700)
    return path


def save(draft_id: int, name: str, data: bytes) -> dict:
    if len(data) > MAX_UPLOAD:
        raise ImageError("图片太大了，单张不超过 20MB")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
    except Exception:
        raise ImageError("这不是一张能识别的图片") from None
    if img.format not in ALLOWED:
        raise ImageError("只支持 png、jpg、gif、webp")
    mime, ext = ALLOWED[img.format]
    file = f"{secrets.token_hex(10)}{ext}"
    path = images_dir() / file
    path.write_bytes(data)
    path.chmod(0o600)
    with db.tx() as conn:
        cur = conn.execute("INSERT INTO images(draft_id, name, file, mime, width, height, bytes, created_at) VALUES (?,?,?,?,?,?,?,?)",
                           (draft_id, (name or "图片")[:120], file, mime, img.width, img.height, len(data), db.now()))
    return get(cur.lastrowid)


def get(image_id: int) -> dict | None:
    return db.row("SELECT * FROM images WHERE id=?", (image_id,))


def path_of(image: dict) -> Path:
    return images_dir() / image["file"]


def for_draft(draft_id: int) -> list[dict]:
    return db.rows("SELECT id, name, mime, width, height, bytes, created_at FROM images WHERE draft_id=? ORDER BY id", (draft_id,))


def delete(image_id: int) -> None:
    image = get(image_id)
    if not image:
        return
    with db.tx() as conn:
        conn.execute("DELETE FROM images WHERE id=?", (image_id,))
    path_of(image).unlink(missing_ok=True)


def referenced(markdown: str) -> list[int]:
    """Image ids used in the body, in order, without duplicates."""
    seen: list[int] = []
    for m in REF_RE.finditer(markdown):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def encoded(image: dict, *, max_bytes: int, formats: tuple[str, ...] = ("image/jpeg", "image/png"),
            max_side: int = 4096) -> tuple[bytes, str, str]:
    """The image as bytes a platform accepts: original if it already fits,
    otherwise a JPEG that is scaled and compressed until it is under max_bytes."""
    raw = path_of(image).read_bytes()
    if image["mime"] in formats and len(raw) <= max_bytes and max(image["width"], image["height"]) <= max_side:
        return raw, image["mime"], Path(image["file"]).name
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    if img.mode not in ("RGB", "L"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        img = background
    side = min(max_side, max(img.size))
    for _ in range(8):
        scaled = img.copy()
        scaled.thumbnail((side, side), Image.LANCZOS)
        for quality in (90, 84, 78, 70):
            out = io.BytesIO()
            scaled.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
            if out.tell() <= max_bytes:
                return out.getvalue(), "image/jpeg", Path(image["file"]).stem + ".jpg"
        side = int(side * 0.8)
    raise ImageError(f"{image['name']} 压不到平台要求的大小")


def remote(image_id: int, target: str) -> dict | None:
    found = db.row("SELECT * FROM image_remotes WHERE image_id=? AND target=?", (image_id, target))
    if found and found["expires_at"] and found["expires_at"] < db.now():
        return None
    return found


def remember(image_id: int, target: str, remote_id: str | None, url: str | None, expires_at: float | None = None) -> None:
    with db.tx() as conn:
        conn.execute("""INSERT INTO image_remotes(image_id, target, remote_id, url, expires_at, created_at) VALUES (?,?,?,?,?,?)
                        ON CONFLICT(image_id, target) DO UPDATE SET remote_id=excluded.remote_id, url=excluded.url,
                        expires_at=excluded.expires_at, created_at=excluded.created_at""",
                     (image_id, target, remote_id, url, expires_at, db.now()))
