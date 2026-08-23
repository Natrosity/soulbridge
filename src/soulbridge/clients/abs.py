"""Audiobookshelf client — only used to trigger a library scan after import
(optional; ABS also has a filesystem watcher)."""
from __future__ import annotations

import httpx


class ABS:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self._c = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )

    def close(self) -> None:
        self._c.close()

    @property
    def configured(self) -> bool:
        return bool(self.base and self._c.headers.get("Authorization"))

    def ping(self) -> bool:
        if not self.configured:
            return False
        try:
            return self._c.get(self.base + "/api/libraries").status_code < 400
        except Exception:
            return False

    def scan(self, library_id: str) -> bool:
        if not (self.base and library_id):
            return False
        try:
            r = self._c.post(f"{self.base}/api/libraries/{library_id}/scan")
            return r.status_code < 300
        except Exception:
            return False

    def libraries(self) -> list:
        try:
            r = self._c.get(f"{self.base}/api/libraries")
            return (r.json() or {}).get("libraries", []) if r.status_code < 400 else []
        except Exception:
            return []

    @staticmethod
    def _norm_item(it: dict) -> dict:
        md = (it.get("media") or {}).get("metadata") or {}
        media = it.get("media") or {}
        return {
            "id": it.get("id"),
            "title": md.get("title") or "Unknown",
            "author": md.get("authorName") or "",
            "narrator": md.get("narratorName") or "",
            "series": md.get("seriesName") or "",
            "year": md.get("publishedYear") or "",
            "asin": md.get("asin") or "",
            "duration_min": round((media.get("duration") or 0) / 60),
        }

    def library_items(self, library_id: str, limit: int = 48, page: int = 0,
                      sort: str = "addedAt", desc: bool = True) -> tuple[list, int]:
        """Return (items, total) for one page of a library, normalised to the
        fields the Library page needs."""
        try:
            r = self._c.get(
                f"{self.base}/api/libraries/{library_id}/items",
                params={"limit": limit, "page": page, "sort": sort, "desc": 1 if desc else 0},
            )
            if r.status_code >= 400:
                return [], 0
            d = r.json()
        except Exception:
            return [], 0
        out = [self._norm_item(it) for it in d.get("results", [])]
        return out, d.get("total", len(out))

    def search_items(self, library_id: str, q: str, limit: int = 48) -> list:
        """Search a library (title/author/series/narrator) → normalised book items."""
        try:
            r = self._c.get(f"{self.base}/api/libraries/{library_id}/search",
                            params={"q": q, "limit": limit})
            if r.status_code >= 400:
                return []
            d = r.json()
        except Exception:
            return []
        # the search endpoint returns {book:[{libraryItem,...}], ...}
        return [self._norm_item(hit["libraryItem"])
                for hit in d.get("book", []) if hit.get("libraryItem")]

    def library_index(self, library_id: str, cap: int = 5000) -> tuple[set, set]:
        """(asins, title|surname keys) for everything in the library — for spotting
        books the user already owns during discovery. Paginated + minified."""
        asins: set = set()
        keys: set = set()
        page, per = 0, 500
        while len(asins) + len(keys) < cap * 2:
            try:
                r = self._c.get(
                    f"{self.base}/api/libraries/{library_id}/items",
                    params={"limit": per, "page": page, "minified": 1, "sort": "addedAt"},
                )
                if r.status_code >= 400:
                    break
                d = r.json()
            except Exception:
                break
            results = d.get("results", [])
            if not results:
                break
            for it in results:
                md = (it.get("media") or {}).get("metadata") or {}
                if md.get("asin"):
                    asins.add(md["asin"].strip())
                k = library_key(md.get("title"), md.get("authorName"))
                if k:
                    keys.add(k)
            if len(results) < per or (page + 1) * per >= d.get("total", 0):
                break
            page += 1
        return asins, keys


def library_key(title: str | None, author: str | None) -> str:
    """A loose title|author-surname key for matching a listing to a library book
    when ASINs differ or are missing. Kept here so callers match consistently."""
    from ..core.matching import norm
    t = norm(title or "")
    surname = (norm(author or "").split() or [""])[-1]
    return f"{t}|{surname}" if t else ""

    def item_cover(self, item_id: str) -> tuple[bytes | None, str]:
        try:
            r = self._c.get(f"{self.base}/api/items/{item_id}/cover")
            if r.status_code < 400:
                return r.content, r.headers.get("content-type", "image/jpeg")
        except Exception:
            pass
        return None, "image/jpeg"
