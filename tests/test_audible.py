"""Pure-function tests for the Audible client's response normalisation (no
network). Runnable with pytest or `python tests/test_audible.py`."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from soulscribe.clients.audible import Audible, product_url  # noqa: E402

RAW_PRODUCT = {
    "asin": "B004SOK2SE",
    "title": "Mistborn",
    "subtitle": "Mistborn, Book 1",
    "authors": [{"asin": "B001IGFHW6", "name": "Brandon Sanderson"}],
    "narrators": [{"name": "Michael Kramer"}],
    "series": [{"asin": "SERIESASIN", "title": "Mistborn"}],
    "product_images": {"500": "https://example.com/cover.jpg"},
    "release_date": "2006-07-17",
    "runtime_length_min": 1500,
}


def test_normalize_maps_fields():
    out = Audible._normalize([RAW_PRODUCT])
    assert len(out) == 1
    b = out[0]
    assert b["asin"] == "B004SOK2SE"
    assert b["title"] == "Mistborn"
    assert b["subtitle"] == "Mistborn, Book 1"
    assert b["authors"] == ["Brandon Sanderson"]
    assert b["narrators"] == ["Michael Kramer"]
    assert b["series"] == "Mistborn"
    assert b["cover"] == "https://example.com/cover.jpg"
    assert b["year"] == "2006"
    assert b["release_date"] == "2006-07-17"
    assert b["runtime_min"] == 1500


def test_normalize_skips_titleless_entries():
    out = Audible._normalize([{"asin": "X"}, RAW_PRODUCT])
    assert len(out) == 1 and out[0]["asin"] == "B004SOK2SE"


def test_normalize_handles_missing_optional_fields():
    minimal = {"asin": "Y", "title": "Solo Book"}
    out = Audible._normalize([minimal])
    assert out[0]["subtitle"] is None
    assert out[0]["authors"] == []
    assert out[0]["narrators"] == []
    assert out[0]["series"] is None
    assert out[0]["cover"] is None
    assert out[0]["year"] is None
    assert out[0]["release_date"] is None


def test_similar_returns_empty_without_asin():
    a = Audible("us")
    try:
        assert a.similar("") == []
        assert a.similar(None) == []
    finally:
        a.close()


def test_by_author_returns_empty_without_name():
    a = Audible("us")
    try:
        assert a.by_author("") == []
        assert a.by_author(None) == []
    finally:
        a.close()


def test_by_author_uses_the_author_filter_and_release_sort(monkeypatch):
    a = Audible("us")
    captured = {}

    def fake_products(params):
        captured.update(params)
        return []

    monkeypatch.setattr(a, "_products", fake_products)
    try:
        a.by_author("Brandon Sanderson", num=15)
    finally:
        a.close()
    assert captured["author"] == "Brandon Sanderson"
    assert captured["products_sort_by"] == "-ReleaseDate"
    assert "keywords" not in captured          # the dedicated filter, not a text search


def test_product_url_builds_region_domain():
    assert product_url("B004SOK2SE", "us") == "https://www.audible.com/pd/B004SOK2SE"
    assert product_url("B004SOK2SE", "au") == "https://www.audible.com.au/pd/B004SOK2SE"
    assert product_url("", "us") == ""


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print(f"\n{len(fns)} passed")
