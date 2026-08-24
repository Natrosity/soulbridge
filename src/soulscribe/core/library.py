"""Cached views of "what's already in the library" — shared by discovery (web)
and the follow-checker (worker) so both agree on "already own this," and so
there's exactly one seam to update when Phase 10 introduces a local
books/editions model. Today this is backed by the Audiobookshelf API; a future
version can consult a local `books` table instead (or as well) without any
caller here needing to change.
"""
from __future__ import annotations

from typing import Any

from .. import cache, settings
from ..clients.abs import ABS, library_key

_INDEX = cache.TTLCache(300)      # "lib" -> (asins, keys), 5 min
_SAMPLE = cache.TTLCache(300)     # "sample" -> recent library items, 5 min


def _configured() -> tuple[str, str, str]:
    return (settings.get("abs_url"), settings.get("abs_api_key"), settings.get("abs_library_id"))


def index() -> tuple[set, set]:
    """(asins, title|author-surname keys) for the whole library."""
    url, key, lib = _configured()
    if not (url and key and lib):
        return set(), set()
    hit = _INDEX.get("lib")
    if hit is not None:
        return hit
    a = ABS(url, key)
    result = a.library_index(lib)
    a.close()
    _INDEX.set("lib", result)
    return result


def sample(limit: int = 40) -> list[dict[str, Any]]:
    """A recent slice of the library (title/author/series/asin per item) —
    seeds discovery's owned-library hero rows and the series-follow lookup."""
    url, key, lib = _configured()
    if not (url and key and lib):
        return []
    hit = _SAMPLE.get("sample")
    if hit is not None:
        return hit
    a = ABS(url, key)
    items, _total = a.library_items(lib, limit=limit, sort="addedAt", desc=True)
    a.close()
    _SAMPLE.set("sample", items)
    return items


def owns(asin: str | None, title: str | None, author: str | None,
        asins: set | None = None, keys: set | None = None) -> bool:
    """Is this book already in the library (by ASIN, or a loose title|author
    key when ASINs differ or are missing)? Pass a pre-fetched (asins, keys)
    pair when checking many candidates in a loop to avoid re-fetching."""
    if asins is None or keys is None:
        asins, keys = index()
    if asin and asin in asins:
        return True
    k = library_key(title, author)
    return bool(k and k in keys)
