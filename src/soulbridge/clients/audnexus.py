"""Audnexus client — canonical Audible metadata by ASIN (https://audnex.us).
Free, no auth. Used to tag downloaded files to match the requested listing."""
from __future__ import annotations

import re
from typing import Any, Optional

import httpx

BASE = "https://api.audnex.us"


class Audnexus:
    def __init__(self, region: str = "us", timeout: float = 20.0):
        self.region = region
        self._c = httpx.Client(timeout=timeout, headers={"User-Agent": "soulbridge"})

    def close(self) -> None:
        self._c.close()

    def book(self, asin: str) -> Optional[dict[str, Any]]:
        try:
            r = self._c.get(f"{BASE}/books/{asin}", params={"region": self.region})
            return r.json() if r.status_code < 400 else None
        except Exception:
            return None

    def fetch_bytes(self, url: str) -> Optional[bytes]:
        try:
            r = self._c.get(url)
            return r.content if r.status_code < 400 else None
        except Exception:
            return None


def to_meta(data: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Normalise an Audnexus book payload into Soulbridge's logical fields."""
    if not data:
        return None
    authors = [a.get("name") for a in data.get("authors", []) if a.get("name")]
    narrators = [n.get("name") for n in data.get("narrators", []) if n.get("name")]
    series = data.get("seriesPrimary") or {}
    genres = [g.get("name") for g in data.get("genres", [])
              if g.get("name") and g.get("type") in (None, "genre")]
    desc = re.sub(r"<[^>]+>", "", data.get("summary") or data.get("description") or "").strip()
    return {
        "title": data.get("title"),
        "subtitle": data.get("subtitle"),
        "authors": authors,
        "narrators": narrators,
        "series": series.get("name"),
        "series_position": str(series.get("position")) if series.get("position") else None,
        "publisher": data.get("publisherName"),
        "year": (data.get("releaseDate") or "")[:4] or None,
        "genres": genres,
        "description": desc or None,
        "asin": data.get("asin"),
        "cover_url": data.get("image"),
        "language": (data.get("language") or "").title() or None,
    }
