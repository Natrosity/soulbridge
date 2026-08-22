"""SQLite persistence. Deliberately dependency-light: stdlib sqlite3 with WAL,
a new connection per call (safe across the worker thread and web requests)."""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional

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
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def init() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)


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
