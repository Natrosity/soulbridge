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


def test_legacy_db_gains_follows_table_and_follow_id_column():
    """A DB from before migration 2 (no follows table, no items.follow_id)
    should gain both without losing existing data."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "pre_follows.db")
        con = sqlite3.connect(path)
        con.executescript(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT NOT NULL,"
            " status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT '',"
            " updated_at TEXT NOT NULL DEFAULT '');"
            "INSERT INTO items(title) VALUES ('Old Book');"
            "PRAGMA user_version = 1;"                    # already past migration 1
        )
        con.commit()
        con.close()

        _with_db(path, db.init)

        cols = _cols(path, "items")
        assert "follow_id" in cols
        con = sqlite3.connect(path)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "follows" in tables
            assert con.execute("SELECT title FROM items").fetchone()[0] == "Old Book"
        finally:
            con.close()
        assert _user_version(path) == len(db.MIGRATIONS)


def test_hours_since():
    assert db.hours_since(None) == float("inf")
    assert db.hours_since("") == float("inf")
    assert db.hours_since("not-a-timestamp") == float("inf")
    just_now = db.now()
    assert db.hours_since(just_now) < 0.01
    from datetime import datetime, timedelta
    six_hours_ago = (datetime.strptime(just_now, "%Y-%m-%dT%H:%M:%S")
                     - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S")
    assert 5.9 < db.hours_since(six_hours_ago) < 6.1


def test_follow_crud():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "follows.db")

        def run():
            db.init()
            fid = db.create_follow("author", "Brandon Sanderson", "alice")
            assert db.create_follow("author", "Brandon Sanderson", "alice") == fid  # idempotent

            f = db.get_follow(fid)
            assert f["kind"] == "author" and f["name"] == "Brandon Sanderson"
            assert f["created_by"] == "alice" and f["last_checked_at"] is None

            # a different user following the same author gets a separate row
            fid2 = db.create_follow("author", "Brandon Sanderson", "bob")
            assert fid2 != fid

            assert {f["id"] for f in db.list_follows("alice")} == {fid}
            assert {f["id"] for f in db.list_follows()} == {fid, fid2}

            db.touch_follow(fid)
            assert db.get_follow(fid)["last_checked_at"] is not None

            db.delete_follow(fid)
            assert db.get_follow(fid) is None
            assert db.get_follow(fid2) is not None

        _with_db(path, run)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("\nall db tests passed")
