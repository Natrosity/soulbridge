"""Phase 8 follow-checking (worker._process_follow / _check_follows). Uses a
real temp DB (so upsert/dedup/role-gating behave exactly as in production) and
a duck-typed FakeAudible so no network is touched. Runnable with pytest.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from soulscribe import db  # noqa: E402
from soulscribe.core import library, worker  # noqa: E402


class FakeAudible:
    def __init__(self, author_results=None, series_results=None):
        self.author_results = author_results or {}
        self.series_results = series_results or {}
        self.author_calls = []
        self.series_calls = []

    def by_author(self, name, num=15):
        self.author_calls.append(name)
        return list(self.author_results.get(name, []))

    def similar(self, asin, similarity_type="RawSimilarities", num=12):
        self.series_calls.append((asin, similarity_type))
        return list(self.series_results.get(asin, []))

    def close(self):
        pass


def _book(asin, title, release_date, authors=None, narrators=None):
    return {"asin": asin, "title": title, "release_date": release_date,
           "authors": authors or [], "narrators": narrators or [], "cover": None}


@pytest.fixture()
def db_ctx(monkeypatch, tmp_path):
    """A real temp DB, initialised, with owns() stubbed so no ABS call happens."""
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "CONFIG_DIR", str(tmp_path))
    db.init()
    monkeypatch.setattr(library, "owns", lambda *a, **k: False)   # nothing owned, by default
    yield db


def _days_ago(n):
    # Use db's own (timezone-aware) clock, not the system-local one — db.now()
    # may run in UTC (no TZ env set) while the test process's local clock could
    # be a different offset, which could put "yesterday" on the wrong side of
    # a same-day cutoff comparison.
    today = datetime.strptime(db.today(), "%Y-%m-%d")
    return (today - timedelta(days=n)).strftime("%Y-%m-%d")


# --------------------------------------------------------------- _process_follow
def test_author_follow_creates_pending_request_for_trusted_user(db_ctx):
    db.create_user("alice", "hash", role="trusted")
    fid = db.create_follow("author", "Brandon Sanderson", "alice")
    follow = db.get_follow(fid)
    new_book = _book("NEW1", "Wind and Truth", _days_ago(0), authors=["Brandon Sanderson"])
    aud = FakeAudible(author_results={"Brandon Sanderson": [new_book]})

    created = worker._process_follow(follow, aud)

    assert created == 1
    item = db.get_item_by_source("user", "NEW1")
    assert item["status"] == "pending"
    assert item["requested_by"] == "alice"
    assert item["follow_id"] == fid
    assert item["mode"] == "auto"
    assert aud.author_calls == ["Brandon Sanderson"]


def test_author_follow_routes_standard_user_to_approval(db_ctx):
    db.create_user("standarduser", "hash", role="standard")
    fid = db.create_follow("author", "X", "standarduser")
    follow = db.get_follow(fid)
    aud = FakeAudible(author_results={"X": [_book("NEW1", "Book", _days_ago(0))]})

    worker._process_follow(follow, aud)

    assert db.get_item_by_source("user", "NEW1")["status"] == "awaiting_approval"


def test_upcoming_release_is_scheduled_not_pending(db_ctx):
    db.create_user("alice", "hash", role="trusted")
    fid = db.create_follow("author", "X", "alice")
    follow = db.get_follow(fid)
    future = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    aud = FakeAudible(author_results={"X": [_book("NEW1", "Book", future)]})

    worker._process_follow(follow, aud)

    item = db.get_item_by_source("user", "NEW1")
    assert item["status"] == "scheduled"
    assert item["release_date"] == future


def test_release_before_follow_created_is_not_backfilled(db_ctx):
    db.create_user("alice", "hash", role="trusted")
    fid = db.create_follow("author", "X", "alice")
    follow = db.get_follow(fid)
    old_book = _book("OLD1", "Old Backlist Book", "2010-01-01")
    aud = FakeAudible(author_results={"X": [old_book]})

    created = worker._process_follow(follow, aud)

    assert created == 0
    assert db.get_item_by_source("user", "OLD1") is None


def test_implausible_placeholder_date_is_skipped(db_ctx):
    # Audible's catalog carries listings with a placeholder date (seen live:
    # 2200-01-01) for entries with no real release date — must not create a
    # 'scheduled' item that can never come due.
    db.create_user("alice", "hash", role="trusted")
    fid = db.create_follow("author", "X", "alice")
    follow = db.get_follow(fid)
    aud = FakeAudible(author_results={"X": [_book("PLACEHOLDER1", "Ghost Listing", "2200-01-01")]})

    assert worker._process_follow(follow, aud) == 0
    assert db.get_item_by_source("user", "PLACEHOLDER1") is None


def test_missing_release_date_is_skipped_conservatively(db_ctx):
    db.create_user("alice", "hash", role="trusted")
    fid = db.create_follow("author", "X", "alice")
    follow = db.get_follow(fid)
    no_date = {"asin": "ND1", "title": "Unknown Date", "release_date": "",
              "authors": [], "narrators": [], "cover": None}
    aud = FakeAudible(author_results={"X": [no_date]})

    assert worker._process_follow(follow, aud) == 0


def test_already_requested_is_not_duplicated(db_ctx):
    db.create_user("alice", "hash", role="trusted")
    db.upsert_item("user", "NEW1", title="Wind and Truth", status="done",
                   requested_by="someone_else")
    fid = db.create_follow("author", "X", "alice")
    follow = db.get_follow(fid)
    aud = FakeAudible(author_results={"X": [_book("NEW1", "Wind and Truth", _days_ago(0))]})

    created = worker._process_follow(follow, aud)

    assert created == 0
    assert db.get_item_by_source("user", "NEW1")["status"] == "done"   # untouched


def test_already_owned_is_skipped(db_ctx, monkeypatch):
    db.create_user("alice", "hash", role="trusted")
    fid = db.create_follow("author", "X", "alice")
    follow = db.get_follow(fid)
    monkeypatch.setattr(library, "owns", lambda *a, **k: True)
    aud = FakeAudible(author_results={"X": [_book("NEW1", "Book", _days_ago(0))]})

    assert worker._process_follow(follow, aud) == 0


def test_quota_is_not_enforced_for_follow_requests(db_ctx):
    # a standard user already at quota would be blocked by do_request()'s
    # manual-request path (web/routes/requests.py's _quota_message), but
    # _process_follow never calls that check — a follow's volume isn't
    # something the user clicked their way into, so it must not be capped.
    db.create_user("alice", "hash", role="trusted")
    db.set_setting("request_quota", "1")
    for i in range(5):                                # already well over any quota
        db.upsert_item("user", f"OPEN{i}", title=f"Open {i}", status="pending",
                       requested_by="alice")
    fid = db.create_follow("author", "X", "alice")
    follow = db.get_follow(fid)
    aud = FakeAudible(author_results={"X": [_book("NEW1", "Book", _days_ago(0))]})

    assert worker._process_follow(follow, aud) == 1


def test_deleted_follower_account_is_skipped_gracefully(db_ctx):
    fid = db.create_follow("author", "X", "ghost_user")
    follow = db.get_follow(fid)
    aud = FakeAudible(author_results={"X": [_book("NEW1", "Book", _days_ago(0))]})
    assert worker._process_follow(follow, aud) == 0     # no crash, nothing created


def test_max_items_per_check_cap(db_ctx):
    db.create_user("alice", "hash", role="trusted")
    fid = db.create_follow("author", "X", "alice")
    follow = db.get_follow(fid)
    many = [_book(f"B{i}", f"Book {i}", _days_ago(0)) for i in range(worker.MAX_FOLLOW_ITEMS_PER_CHECK + 5)]
    aud = FakeAudible(author_results={"X": many})

    created = worker._process_follow(follow, aud)

    assert created == worker.MAX_FOLLOW_ITEMS_PER_CHECK


# --------------------------------------------------------------- series follows
def test_series_follow_uses_ref_asin_and_in_same_series(db_ctx):
    db.create_user("alice", "hash", role="trusted")
    fid = db.create_follow("series", "Mistborn", "alice", ref_asin="SEED1")
    follow = db.get_follow(fid)
    aud = FakeAudible(series_results={"SEED1": [_book("NEXTBOOK", "Book 5", _days_ago(0))]})

    created = worker._process_follow(follow, aud)

    assert created == 1
    assert aud.series_calls == [("SEED1", "InTheSameSeries")]


def test_series_follow_without_ref_asin_finds_nothing(db_ctx):
    db.create_user("alice", "hash", role="trusted")
    fid = db.create_follow("series", "Mistborn", "alice")     # no ref_asin
    follow = db.get_follow(fid)
    aud = FakeAudible()
    assert worker._process_follow(follow, aud) == 0
    assert aud.series_calls == []


# --------------------------------------------------------------- _check_follows
def test_check_follows_throttles_recently_checked(db_ctx, monkeypatch):
    db.create_user("alice", "hash", role="trusted")
    fid = db.create_follow("author", "X", "alice")
    db.touch_follow(fid)                                   # just checked -> not due

    calls = []
    monkeypatch.setattr(worker, "_process_follow", lambda f, aud: calls.append(f["id"]) or 0)
    monkeypatch.setattr(worker, "Audible", lambda region: FakeAudible())

    worker._check_follows()

    assert calls == []


def test_check_follows_processes_due_follow_and_updates_last_checked(db_ctx, monkeypatch):
    db.create_user("alice", "hash", role="trusted")
    fid = db.create_follow("author", "X", "alice")   # last_checked_at is None -> due
    assert db.get_follow(fid)["last_checked_at"] is None

    calls = []
    monkeypatch.setattr(worker, "_process_follow", lambda f, aud: calls.append(f["id"]) or 0)
    monkeypatch.setattr(worker, "Audible", lambda region: FakeAudible())

    worker._check_follows()

    assert calls == [fid]
    assert db.get_follow(fid)["last_checked_at"] is not None


def test_check_follows_touches_last_checked_even_on_error(db_ctx, monkeypatch):
    db.create_user("alice", "hash", role="trusted")
    fid = db.create_follow("author", "X", "alice")

    def boom(f, aud):
        raise RuntimeError("Audible unavailable")

    monkeypatch.setattr(worker, "_process_follow", boom)
    monkeypatch.setattr(worker, "Audible", lambda region: FakeAudible())

    worker._check_follows()                       # must not raise

    assert db.get_follow(fid)["last_checked_at"] is not None


def test_check_follows_noop_with_no_follows(db_ctx, monkeypatch):
    called = []
    monkeypatch.setattr(worker, "Audible", lambda region: called.append(1) or FakeAudible())
    worker._check_follows()
    assert called == []          # never even constructs an Audible client
