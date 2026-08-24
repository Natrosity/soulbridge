"""core/library.py — the shared ownership-check module used by both discovery
(web) and the follow-checker (worker). This is the one seam Phase 10's
books/editions model will need to update, so it's worth pinning its contract
precisely. Runnable with pytest.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from soulscribe.core import library  # noqa: E402


def test_owns_by_asin():
    assert library.owns("A1", "Some Title", "Some Author",
                        asins={"A1", "A2"}, keys=set()) is True


def test_owns_by_title_author_key():
    from soulscribe.clients.abs import library_key
    k = library_key("Dark Matter", "Blake Crouch")
    assert library.owns(None, "Dark Matter", "Blake Crouch",
                        asins=set(), keys={k}) is True


def test_not_owned():
    assert library.owns("A9", "Nope", "Nobody", asins={"A1"}, keys=set()) is False


def test_owns_with_no_asin_falls_back_to_key():
    from soulscribe.clients.abs import library_key
    k = library_key("Title", "Author")
    assert library.owns(None, "Title", "Author", asins=set(), keys={k}) is True
    assert library.owns(None, "Title", "Someone Else", asins=set(), keys={k}) is False


def test_index_returns_empty_when_abs_not_configured(monkeypatch):
    from soulscribe import settings
    monkeypatch.setattr(settings, "get", lambda key: "")
    assert library.index() == (set(), set())


def test_sample_returns_empty_when_abs_not_configured(monkeypatch):
    from soulscribe import settings
    monkeypatch.setattr(settings, "get", lambda key: "")
    assert library.sample() == []


def test_index_uses_and_populates_cache(monkeypatch):
    from soulscribe import settings
    from soulscribe.clients.abs import ABS

    monkeypatch.setattr(settings, "get", lambda key: "x")
    calls = []

    def fake_library_index(self, lib_id, cap=5000):
        calls.append(lib_id)
        return {"A1"}, {"k1"}

    monkeypatch.setattr(ABS, "library_index", fake_library_index)
    monkeypatch.setattr(ABS, "close", lambda self: None)
    library._INDEX.pop("lib")

    first = library.index()
    second = library.index()
    assert first == ({"A1"}, {"k1"}) == second
    assert len(calls) == 1          # second call served from cache, not a fresh ABS hit
