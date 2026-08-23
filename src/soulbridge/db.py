"""SQLite persistence. Deliberately dependency-light: stdlib sqlite3 with WAL,
a new connection per call (safe across the worker thread and web requests)."""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:                                    # pragma: no cover
    ZoneInfo = None                                  # type: ignore

CONFIG_DIR = os.environ.get("SOULBRIDGE_CONFIG_DIR", "/config")
DB_PATH = os.environ.get("SOULBRIDGE_DB", os.path.join(CONFIG_DIR, "soulbridge.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT NOT NULL DEFAULT 'abr',   -- abr | manual
    source_id      TEXT,                          -- ABR asin (unique per source)
    title          TEXT NOT NULL,
    author         TEXT,
    narrator       TEXT,
    cover          TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    slskd_username TEXT,
    slskd_dir      TEXT,                           -- remote directory of chosen file(s)
    chosen_files   TEXT,                           -- JSON list of remote filenames grabbed
    size           INTEGER DEFAULT 0,
    dest_path      TEXT,                           -- final library folder
    error          TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,     -- search/grab attempts (for retry cap)
    mode           TEXT NOT NULL DEFAULT 'auto',   -- auto | interactive (how the source is chosen)
    release_date   TEXT,                           -- YYYY-MM-DD; if future, hold until then
    requested_by   TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE(source, source_id)
);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    level     TEXT NOT NULL DEFAULT 'info',
    item_id   INTEGER,
    message   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tag_writes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    item_id    INTEGER,
    book_title TEXT,
    file_name  TEXT,
    cover      TEXT,
    fields     TEXT NOT NULL DEFAULT '[]'   -- JSON: [{name, old, new, action}]
);
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email           TEXT,
    password_hash   TEXT,
    role            TEXT NOT NULL DEFAULT 'standard',   -- admin | trusted | standard
    plex_id         TEXT,
    preferences     TEXT NOT NULL DEFAULT '{}',
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    created_at      TEXT NOT NULL,
    last_login      TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_tagwrites_ts ON tag_writes(id);
"""


def _tzinfo():
    """The configured display timezone from the TZ env var (e.g. Australia/Brisbane),
    falling back to UTC. Set TZ on the container to localise all timestamps."""
    name = (os.environ.get("TZ") or "").strip()
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return timezone.utc


def now() -> str:
    """Current wall-clock timestamp in the configured timezone (no offset suffix)."""
    return datetime.now(_tzinfo()).strftime("%Y-%m-%dT%H:%M:%S")


def today() -> str:
    """Today's date in the configured timezone (for release-date comparisons)."""
    return datetime.now(_tzinfo()).strftime("%Y-%m-%d")


def _now() -> str:
    return now()


def init() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)
        # lightweight migrations for existing databases
        cols = {r["name"] for r in c.execute("PRAGMA table_info(items)")}
        if "attempts" not in cols:
            c.execute("ALTER TABLE items ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        if "mode" not in cols:
            c.execute("ALTER TABLE items ADD COLUMN mode TEXT NOT NULL DEFAULT 'auto'")
        if "release_date" not in cols:
            c.execute("ALTER TABLE items ADD COLUMN release_date TEXT")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---- settings ----
def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def all_settings() -> dict[str, str]:
    with connect() as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}


# ---- items ----
def upsert_item(source: str, source_id: Optional[str], **fields: Any) -> int:
    """Insert a new item or return the existing id for (source, source_id)."""
    with connect() as c:
        if source_id is not None:
            row = c.execute(
                "SELECT id FROM items WHERE source=? AND source_id=?", (source, source_id)
            ).fetchone()
            if row:
                return row["id"]
        cols = ["source", "source_id", "created_at", "updated_at"]
        vals: list[Any] = [source, source_id, _now(), _now()]
        for k, v in fields.items():
            cols.append(k)
            vals.append(v)
        placeholders = ",".join("?" for _ in cols)
        cur = c.execute(
            f"INSERT INTO items({','.join(cols)}) VALUES({placeholders})", vals
        )
        return int(cur.lastrowid)


def update_item(item_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    sets = ",".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE items SET {sets} WHERE id=?", (*fields.values(), item_id))


def get_item(item_id: int) -> Optional[dict[str, Any]]:
    with connect() as c:
        row = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return dict(row) if row else None


def list_items(statuses: Optional[Iterable[str]] = None, limit: int = 200) -> list[dict[str, Any]]:
    with connect() as c:
        if statuses:
            marks = ",".join("?" for _ in statuses)
            rows = c.execute(
                f"SELECT * FROM items WHERE status IN ({marks}) ORDER BY updated_at DESC LIMIT ?",
                (*statuses, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM items ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def list_items_by_user(username: str, limit: int = 200) -> list[dict[str, Any]]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM items WHERE requested_by=? ORDER BY updated_at DESC LIMIT ?",
            (username, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# Requests that still count against a user's quota (everything not terminal).
OPEN_STATUSES = ("awaiting_approval", "scheduled", "selecting", "pending", "searching",
                 "downloading", "importing")


def count_open_requests(username: str) -> int:
    """How many not-yet-resolved requests a user currently has (for quota checks)."""
    marks = ",".join("?" for _ in OPEN_STATUSES)
    with connect() as c:
        row = c.execute(
            f"SELECT COUNT(*) n FROM items WHERE requested_by=? AND status IN ({marks})",
            (username, *OPEN_STATUSES),
        ).fetchone()
        return row["n"]


def get_item_by_source(source: str, source_id: str) -> Optional[dict[str, Any]]:
    with connect() as c:
        r = c.execute("SELECT * FROM items WHERE source=? AND source_id=?",
                      (source, source_id)).fetchone()
        return dict(r) if r else None


def counts_by_status() -> dict[str, int]:
    with connect() as c:
        rows = c.execute("SELECT status, COUNT(*) n FROM items GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}


# ---- events ----
def log_event(message: str, level: str = "info", item_id: Optional[int] = None) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO events(ts,level,item_id,message) VALUES(?,?,?,?)",
            (_now(), level, item_id, message),
        )
        # keep the log bounded
        c.execute(
            "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 1000)"
        )


def recent_events(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as c:
        rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# ---- tag writes ----
def log_tag_write(item_id: Optional[int], book_title: str, file_name: str,
                  cover: Optional[str], fields: list[dict[str, Any]]) -> None:
    import json
    with connect() as c:
        c.execute(
            "INSERT INTO tag_writes(ts,item_id,book_title,file_name,cover,fields) VALUES(?,?,?,?,?,?)",
            (_now(), item_id, book_title, file_name, cover, json.dumps(fields)),
        )
        c.execute(
            "DELETE FROM tag_writes WHERE id NOT IN (SELECT id FROM tag_writes ORDER BY id DESC LIMIT 500)"
        )


def recent_tag_writes(limit: int = 100) -> list[dict[str, Any]]:
    import json
    with connect() as c:
        rows = c.execute("SELECT * FROM tag_writes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["fields"] = json.loads(d["fields"])
            except Exception:
                d["fields"] = []
            out.append(d)
        return out


# ---- users ----
def user_count() -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]


def create_user(username: str, password_hash: Optional[str], role: str = "standard",
                email: Optional[str] = None, plex_id: Optional[str] = None) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO users(username,email,password_hash,role,plex_id,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (username, email, password_hash, role, plex_id, _now()),
        )
        return int(cur.lastrowid)


def get_user(user_id: int) -> Optional[dict[str, Any]]:
    with connect() as c:
        r = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(r) if r else None


def get_user_by_name(username: str) -> Optional[dict[str, Any]]:
    with connect() as c:
        r = c.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        return dict(r) if r else None


def get_user_by_plex_id(plex_id: str) -> Optional[dict[str, Any]]:
    with connect() as c:
        r = c.execute("SELECT * FROM users WHERE plex_id=?", (plex_id,)).fetchone()
        return dict(r) if r else None


def list_users() -> list[dict[str, Any]]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM users ORDER BY id")]


def update_user(user_id: int, **fields: Any) -> None:
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE users SET {sets} WHERE id=?", (*fields.values(), user_id))


def delete_user(user_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
