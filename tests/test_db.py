"""Schema migration framework. Runnable with pytest or `python tests/test_db.py`."""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from soulscribe import db  # noqa: E402


def _cols(path, table):
    con = sqlite3.connect(path)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def _user_version(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


def _with_db(path, fn):
    """Run fn() with db pointed at a temp file (and config dir), then restore."""
    saved_db, saved_dir = db.DB_PATH, db.CONFIG_DIR
    db.DB_PATH = path
    db.CONFIG_DIR = os.path.dirname(path)
    try:
        return fn()
    finally:
        db.DB_PATH, db.CONFIG_DIR = saved_db, saved_dir


def test_fresh_db_lands_at_latest_version():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "fresh.db")
        _with_db(path, db.init)
        assert _user_version(path) == len(db.MIGRATIONS)
        cols = _cols(path, "items")
        assert {"attempts", "mode", "release_date", "note"} <= cols


def test_legacy_db_gets_columns_added():
    """A pre-versioning DB (items without the newer columns, user_version 0)
    should have them added by migration 1 without losing its rows."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "legacy.db")
        con = sqlite3.connect(path)
        con.executescript(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT NOT NULL,"
            " status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT '',"
            " updated_at TEXT NOT NULL DEFAULT '');"
            "INSERT INTO items(title) VALUES ('Old Book');"
        )
        con.commit()
        con.close()
        assert "attempts" not in _cols(path, "items")   # precondition

        _with_db(path, db.init)

        cols = _cols(path, "items")
        assert {"attempts", "mode", "release_date", "note"} <= cols
        assert _user_version(path) == len(db.MIGRATIONS)
        con = sqlite3.connect(path)
        assert con.execute("SELECT title FROM items").fetchone()[0] == "Old Book"
        con.close()


def test_init_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "again.db")
        _with_db(path, db.init)
        _with_db(path, db.init)                          # second run must not error
        assert _user_version(path) == len(db.MIGRATIONS)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("\nall db tests passed")
