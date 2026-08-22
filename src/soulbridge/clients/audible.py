"""Audible catalog client — book discovery search (the source ABR uses).
Keyword search returns real audiobook listings with ASINs, which then flow into
the normal pipeline (Soulseek search + Audnexus tagging by ASIN). No auth."""
from __future__ import annotations

from typing import Any

import httpx

# Audible region -> API domain suffix
DOMAINS = {
    "us": "com", "ca": "ca", "uk": "co.uk", "au": "com.au", "fr": "fr", "de": "de",
    "jp": "co.jp", "it": "it", "in": "in", "es": "es", "br": "com.br",
}
RESPONSE_GROUPS = "product_desc,product_attrs,media,series,contributors"


class Audible:
    def __init__(self, region: str = "us", timeout: float = 20.0):
        self.base = f"https://api.audible.{DOMAINS.get(region, 'com')}/1.0/catalog/products"
        self._c = httpx.Client(timeout=timeout, headers={"User-Agent": "soulbridge"})

    def close(self) -> None:
        self._c.close()

    def search(self, keywords: str, num: int = 24) -> list[dict[str, Any]]:
        try:
            r = self._c.get(self.base, params={
                "keywords": keywords, "num_results": min(max(num, 1), 40),
                "products_sort_by": "Relevance", "response_groups": RESPONSE_GROUPS,
                "image_sizes": "500",
            })
            if r.status_code >= 400:
                return []
            products = r.json().get("products", [])
        except Exception:
            return []
        out = []
        for p in products:
            title = p.get("title")
            if not title:
                continue
            series = p.get("series") or []
            out.append({
                "asin": p.get("asin"),
                "title": title,
                "subtitle": p.get("subtitle"),
                "authors": [a["name"] for a in p.get("authors", []) if a.get("name")],
                "narrators": [n["name"] for n in p.get("narrators", []) if n.get("name")],
                "series": series[0].get("title") if series else None,
                "cover": (p.get("product_images") or {}).get("500"),
                "year": (p.get("release_date") or "")[:4] or None,
                "runtime_min": p.get("runtime_length_min"),
            })
        return out
