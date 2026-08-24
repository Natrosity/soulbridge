"""Discovery hero-row logic (Phase 8): series completion, author heroes, and
the similarity row. Pure logic tested via dependency injection — a fake
Audible-like object is passed directly, no network involved. Runnable with
pytest.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from soulscribe.web.routes import requests as r  # noqa: E402


class FakeAudible:
    """Duck-types the two Audible methods the hero-row builders call."""

    def __init__(self, similar_by_asin=None, search_by_query=None):
        self.similar_by_asin = similar_by_asin or {}
        self.search_by_query = search_by_query or {}
        self.similar_calls = []
        self.search_calls = []

    def similar(self, asin, similarity_type="RawSimilarities", num=12):
        self.similar_calls.append((asin, similarity_type))
        return list(self.similar_by_asin.get(asin, []))

    def search(self, keywords, num=10):
        self.search_calls.append(keywords)
        return list(self.search_by_query.get(keywords, []))


def _book(asin, title, authors=None, series=None):
    return {"asin": asin, "title": title, "authors": authors or [],
           "series": series, "subtitle": None, "narrators": [], "cover": None,
           "year": None, "release_date": None, "runtime_min": None}


@pytest.fixture(autouse=True)
def no_mark_results(monkeypatch):
    # _mark_results normally hits the DB + ABS for status marks; the hero-row
    # unit tests only care about which candidates survive filtering, so make
    # it a no-op (route-level tests cover the marked-up rendering separately).
    monkeypatch.setattr(r, "_mark_results", lambda results: None)


# --------------------------------------------------------------- _not_owned
def test_not_owned_filters_by_asin_and_key():
    candidates = [
        _book("A1", "Owned By Asin", ["X"]),
        _book("A2", "Owned By Key", ["Author Y"]),
        _book("A3", "Wanted", ["Author Z"]),
        _book(None, "No Asin", ["Author Q"]),
    ]
    asins = {"A1"}
    keys = {r.library_key("Owned By Key", "Author Y")}
    out = r._not_owned(candidates, asins, keys, set())
    assert [b["asin"] for b in out] == ["A3"]


def test_not_owned_dedupes_within_batch():
    candidates = [_book("A1", "One"), _book("A1", "One dup")]
    out = r._not_owned(candidates, set(), set(), set())
    assert len(out) == 1


# ------------------------------------------------------- series completion
def test_series_completion_finds_missing_books(monkeypatch):
    sample = [
        {"asin": "OWNED1", "title": "Mistborn", "author": "Brandon Sanderson",
         "series": "Mistborn"},
    ]
    monkeypatch.setattr(r, "_library_sample", lambda: sample)
    monkeypatch.setattr(r, "_library_index", lambda: ({"OWNED1"}, set()))
    sibs = [
        _book("OWNED1", "Mistborn", ["Brandon Sanderson"]),          # already owned -> filtered
        _book("MISSING1", "The Well of Ascension", ["Brandon Sanderson"]),
    ]
    aud = FakeAudible(similar_by_asin={"OWNED1": sibs})
    row = r._series_completion_row(aud)
    assert row["label"] == "Complete the series"
    assert [b["asin"] for b in row["books"]] == ["MISSING1"]
    assert row["books"][0]["series"] == "Mistborn"
    assert aud.similar_calls == [("OWNED1", "InTheSameSeries")]


def test_series_completion_empty_when_nothing_missing(monkeypatch):
    sample = [{"asin": "OWNED1", "title": "X", "author": "A", "series": "S"}]
    monkeypatch.setattr(r, "_library_sample", lambda: sample)
    monkeypatch.setattr(r, "_library_index", lambda: ({"OWNED1"}, set()))
    aud = FakeAudible(similar_by_asin={"OWNED1": [_book("OWNED1", "X", ["A"])]})
    assert r._series_completion_row(aud) == {}


def test_series_completion_ignores_items_without_series_or_asin(monkeypatch):
    sample = [{"asin": None, "title": "No Asin", "author": "A", "series": "S"},
              {"asin": "X", "title": "No Series", "author": "A", "series": ""}]
    monkeypatch.setattr(r, "_library_sample", lambda: sample)
    monkeypatch.setattr(r, "_library_index", lambda: (set(), set()))
    aud = FakeAudible()
    assert r._series_completion_row(aud) == {}
    assert aud.similar_calls == []          # no representative found -> no API call


def test_series_completion_caps_at_three_series(monkeypatch):
    sample = [{"asin": f"OWN{i}", "title": f"T{i}", "author": "A", "series": f"S{i}"}
             for i in range(5)]
    monkeypatch.setattr(r, "_library_sample", lambda: sample)
    monkeypatch.setattr(r, "_library_index", lambda: (set(sample[i]["asin"] for i in range(5)), set()))
    similar_by_asin = {f"OWN{i}": [_book(f"MISS{i}", f"Missing {i}", ["A"])] for i in range(5)}
    aud = FakeAudible(similar_by_asin=similar_by_asin)
    r._series_completion_row(aud)
    assert len(aud.similar_calls) == 3       # capped, not all 5 series queried


# ------------------------------------------------------------- author heroes
def test_author_hero_row_finds_new_books(monkeypatch):
    sample = [{"asin": "O1", "title": "Owned", "author": "Brandon Sanderson", "series": ""}]
    monkeypatch.setattr(r, "_library_sample", lambda: sample)
    monkeypatch.setattr(r, "_library_index", lambda: ({"O1"}, set()))
    results = [_book("O1", "Owned", ["Brandon Sanderson"]),
              _book("NEW1", "Warbreaker", ["Brandon Sanderson"])]
    aud = FakeAudible(search_by_query={"Brandon Sanderson": results})
    row = r._author_hero_row(aud)
    assert row["label"] == "More from authors you own"
    assert [b["asin"] for b in row["books"]] == ["NEW1"]
    assert aud.search_calls == ["Brandon Sanderson"]


def test_author_hero_row_dedupes_authors_case_insensitively(monkeypatch):
    sample = [{"asin": "O1", "title": "A", "author": "Brandon Sanderson", "series": ""},
              {"asin": "O2", "title": "B", "author": "brandon sanderson", "series": ""}]
    monkeypatch.setattr(r, "_library_sample", lambda: sample)
    monkeypatch.setattr(r, "_library_index", lambda: (set(), set()))
    aud = FakeAudible()
    r._author_hero_row(aud)
    assert aud.search_calls == ["Brandon Sanderson"]      # only queried once


def test_author_hero_row_empty_with_no_authors(monkeypatch):
    monkeypatch.setattr(r, "_library_sample", lambda: [])
    monkeypatch.setattr(r, "_library_index", lambda: (set(), set()))
    aud = FakeAudible()
    assert r._author_hero_row(aud) == {}


# ------------------------------------------------------------ similar hero
def test_similar_hero_row_seeds_from_most_recent_owned_book(monkeypatch):
    sample = [{"asin": None, "title": "No Asin", "author": "A", "series": ""},
              {"asin": "SEED", "title": "Dark Matter", "author": "Blake Crouch", "series": ""}]
    monkeypatch.setattr(r, "_library_sample", lambda: sample)
    monkeypatch.setattr(r, "_library_index", lambda: ({"SEED"}, set()))
    aud = FakeAudible(similar_by_asin={"SEED": [_book("SIM1", "Recursion", ["Blake Crouch"])]})
    row = r._similar_hero_row(aud)
    assert row["label"] == "Because you have Dark Matter"
    assert [b["asin"] for b in row["books"]] == ["SIM1"]
    assert aud.similar_calls == [("SEED", "RawSimilarities")]


def test_similar_hero_row_empty_with_no_asin_in_library(monkeypatch):
    monkeypatch.setattr(r, "_library_sample", lambda: [{"asin": None, "title": "X"}])
    assert r._similar_hero_row(FakeAudible()) == {}


# ---------------------------------------------------------------- orchestrator
def test_owned_hero_rows_returns_nothing_when_abs_not_configured(monkeypatch):
    from soulscribe import settings
    monkeypatch.setattr(settings, "get", lambda key: "")
    assert r._owned_hero_rows() == []
