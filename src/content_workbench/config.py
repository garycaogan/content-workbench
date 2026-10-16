"""Paths, settings and owner-only secret storage.

All personal data lives under one private directory outside the repository
(default ``~/.local/share/deepsea/content-workbench``). The directory is 0700 and
every file written by this module is 0600.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_PORT = 8765


def data_dir() -> Path:
    default = Path.home() / ".local/share/content-workbench"
    legacy = Path.home() / ".local/share/deepsea/content-workbench"  # first installs used this
    path = Path(os.environ.get("CW_DATA_DIR") or (legacy if legacy.exists() else default))
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


@dataclass
class Settings:
    """Non-secret settings. ``rp_id`` and ``origins`` must match the URL the
    browser uses; passkeys are bound to the rp_id they were created under."""

    port: int = DEFAULT_PORT
    rp_id: str = "localhost"
    origins: list[str] = field(default_factory=lambda: [f"http://localhost:{DEFAULT_PORT}"])
    allowed_hosts: list[str] = field(default_factory=lambda: [f"localhost:{DEFAULT_PORT}", f"127.0.0.1:{DEFAULT_PORT}"])
    author: str = ""                    # byline, e.g. "Gary曹淦"
    session_days: int = 30
    # Who the drafts are written for. Keys: name, identity, speaker_hint,
    # table_rule, manual_title, x_style, domain (see prompts.persona for defaults).
    persona: dict = field(default_factory=dict)
    # Optional writing assets: a style guide (Markdown), an identity/boundaries
    # document, a knowledge base folder, and a skill folder with
    # scripts/ip_content_context.py + scripts/voice_check.py for exemplars and voice checks.
    voice_guide_path: str = ""
    manual_path: str = ""
    kb_root: str = ""
    skill_dir: str = ""
    workspace_root: str = ""            # older setting: <workspace>/.agents/skills/ip-content
    transcribe_vocab: str = ""          # words Whisper should expect, comma separated
    case_search: str = ""               # "scys": find cases through 生财有术's MCP while drafting
    zsxq_group_id: str = ""             # 知识星球 group for article drafts
    zsxq_group_name: str = ""
    # Optional file holding a `claude setup-token` OAuth token. The standalone
    # CLI does not share the desktop app's login, so it needs its own.
    claude_token_file: str = ""
    # short video (视频 tab): Evolink key file for the Chinese TTS, default voice,
    # the line under the author mark, and the music bed (default <data>/video/bgm.wav)
    evolink_key_file: str = ""
    video_voice: str = ""
    video_tagline: str = ""
    video_bgm: str = ""
    # the author's own voice: a recording of them reading (10–60 s, mono WAV) that
    # Evolink clones; it shows up as a voice ("clone") named video_clone_name
    video_clone_ref: str = ""
    video_clone_name: str = ""
    # Cloudflare Access in front of a public domain. When access_team is set,
    # every request must carry a valid Access token for access_aud.
    access_team: str = ""
    access_aud: str = ""
    access_emails: list[str] = field(default_factory=list)
    # "passkey": Access, then a passkey session. "access": the Cloudflare Access
    # email code is the login (the author's choice 2026-10-14, so new devices need no
    # passkey); only takes effect when Access is configured.
    login_mode: str = "passkey"

    @property
    def access_login(self) -> bool:
        return self.login_mode == "access" and bool(self.access_team and self.access_aud)

    @classmethod
    def load(cls) -> "Settings":
        path = data_dir() / "settings.json"
        if not path.exists():
            s = cls()
            s.save()
            return s
        os.chmod(path, 0o600)
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> None:
        _write_private(data_dir() / "settings.json", json.dumps(asdict(self), ensure_ascii=False, indent=1))

    @property
    def secure_cookies(self) -> bool:
        return all(o.startswith("https://") for o in self.origins)


class SecretStore:
    """Owner-only key/value secrets (platform credentials and tokens).

    Values are written by the owner through the settings page; nothing here is
    ever returned to the browser in full."""

    def __init__(self) -> None:
        self.path = data_dir() / "secrets.json"

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, key: str, default=None):
        return self._read().get(key, default)

    def set(self, key: str, value) -> None:
        data = self._read()
        if value in (None, ""):
            data.pop(key, None)
        else:
            data[key] = value
        _write_private(self.path, json.dumps(data, ensure_ascii=False, indent=1))

    def present(self, *keys: str) -> dict[str, bool]:
        data = self._read()
        return {k: bool(data.get(k)) for k in keys}


def setup_code_path() -> Path:
    return data_dir() / "setup-code.txt"


def ensure_setup_code() -> str:
    """One-time code that authorizes registering the first passkey."""
    path = setup_code_path()
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    code = "-".join(secrets.token_hex(3) for _ in range(3))
    _write_private(path, code + "\n")
    return code


def clear_setup_code() -> None:
    setup_code_path().unlink(missing_ok=True)
