"""SQLite storage. One file in the private data directory."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .config import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,            -- base64url credential id
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    rp_id TEXT NOT NULL,
    label TEXT,
    created_at REAL NOT NULL,
    last_used_at REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    user_agent TEXT
);
CREATE TABLE IF NOT EXISTS challenges (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    challenge BLOB NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,           -- note | audio | feishu_minutes | feishu_doc | paste
    source_ref TEXT,                -- minute token / doc url / file name
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ready',   -- ready | transcribing | failed
    error TEXT,
    created_at REAL NOT NULL,
    occurred_at REAL,
    UNIQUE(source, source_ref)
);
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER REFERENCES materials(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    angle TEXT,
    platforms TEXT,                 -- json list
    quotes TEXT,                    -- json list of the author's own words from the material
    notes TEXT,
    starred INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',     -- new | drafted | dropped
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER REFERENCES ideas(id) ON DELETE SET NULL,
    platform TEXT NOT NULL,         -- wechat | x
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '', -- 改稿备注, never published
    status TEXT NOT NULL DEFAULT 'draft',   -- draft | generating | published | failed
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS draft_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    title TEXT, body TEXT, created_at REAL NOT NULL, origin TEXT
);
CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER REFERENCES drafts(id) ON DELETE SET NULL,
    platform TEXT NOT NULL,
    state TEXT NOT NULL,            -- sent | failed | unknown
    remote_id TEXT,
    detail TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER REFERENCES drafts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,             -- original file name, for display
    file TEXT NOT NULL,             -- file name inside the private images directory
    mime TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS image_remotes (
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    target TEXT NOT NULL,           -- wx_cover | wx_inline | x_media
    remote_id TEXT,
    url TEXT,
    expires_at REAL,                -- X media ids expire if unused; NULL = permanent
    created_at REAL NOT NULL,
    PRIMARY KEY (image_id, target)
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,  -- NULL = start of a thread
    author TEXT NOT NULL,           -- gary | ai
    body TEXT NOT NULL,
    quote TEXT,                     -- anchored text as it reads in the article (no Markdown); NULL = whole article
    prefix TEXT,                    -- a few characters before/after the quote, to tell repeats apart
    suffix TEXT,
    status TEXT NOT NULL DEFAULT 'open',    -- open | resolved (thread starts only)
    created_at REAL NOT NULL,
    resolved_at REAL
);
CREATE TABLE IF NOT EXISTS edit_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    base_version INTEGER NOT NULL,  -- the AI-written version the author started from
    trigger TEXT NOT NULL,          -- before-ai | publish | idle | manual
    changes TEXT NOT NULL,          -- json list of {before, after, context}
    stats TEXT,                     -- json counts
    end_hash TEXT,
    learned INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(draft_id, base_version)
);
CREATE TABLE IF NOT EXISTS case_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER REFERENCES drafts(id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',   -- queued | done | failed
    results TEXT,                   -- json list of cases found in 生财 MCP
    error TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | failed
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

_lock = threading.RLock()


def db_path() -> Path:
    return data_dir() / "workbench.db"


def connect() -> sqlite3.Connection:
    path = db_path()
    new = not path.exists()
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if new:
        os.chmod(path, 0o600)
    return conn


_conn: sqlite3.Connection | None = None


# Columns added after the first release; ALTER TABLE keeps existing data.
MIGRATIONS = {
    "drafts": {
        "cover_wechat": "INTEGER REFERENCES images(id) ON DELETE SET NULL",
        "cover_x": "INTEGER REFERENCES images(id) ON DELETE SET NULL",
        "cover_crop": "TEXT",           # json {"square_x": 0..1}: where the 1:1 公众号 crop starts
        "summary": "TEXT NOT NULL DEFAULT ''",     # 公众号 摘要; empty = first lines of the body
        "source_url": "TEXT NOT NULL DEFAULT ''",  # 公众号 阅读原文
        # the one Feishu document that archives this draft
        "feishu_doc": "TEXT",
        "feishu_url": "TEXT",
        "feishu_rev": "INTEGER",            # revision right after our last write; differs → edited in Feishu
        "feishu_state": "TEXT",             # syncing | ok | conflict | failed
        "feishu_error": "TEXT",
        "feishu_synced_version": "REAL",    # drafts.updated_at that the archive reflects
        "feishu_tried_version": "REAL",     # drafts.updated_at of the last attempt
        "feishu_synced_at": "REAL",
        # the last AI revision from comments: {"before": version id, "after": version id, "summary", "at", "threads"}
        "last_revision": "TEXT",
        "xhs": "TEXT",                       # 小红书 version: title, body, tags, cards, picture ids
    },
}


def migrate(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def get() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = connect()
            _conn.executescript(SCHEMA)
            migrate(_conn)
        return _conn


def reset_for_tests() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


@contextmanager
def tx():
    conn = get()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def rows(sql: str, args=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in get().execute(sql, args).fetchall()]


def row(sql: str, args=()) -> dict | None:
    with _lock:
        r = get().execute(sql, args).fetchone()
        return dict(r) if r else None


def now() -> float:
    return time.time()


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value, default=None):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
