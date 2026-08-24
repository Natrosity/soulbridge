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


def product_url(asin: str, region: str = "us") -> str:
    """Public Audible product page for an ASIN (redirects to the full slug URL)."""
    return f"https://www.audible.{DOMAINS.get(region, 'com')}/pd/{asin}" if asin else ""


class Audible:
    def __init__(self, region: str = "us", timeout: float = 20.0):
        self.base = f"https://api.audible.{DOMAINS.get(region, 'com')}/1.0/catalog/products"
        self._c = httpx.Client(timeout=timeout, headers={"User-Agent": "soulscribe"})

    def close(self) -> None:
        self._c.close()

    @staticmethod
    def _normalize(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for p in products:
            title = p.get("title")
            if not title:
                continue
            series = p.get("series") or []
            rel = p.get("release_date") or ""
            out.append({
                "asin": p.get("asin"),
                "title": title,
                "subtitle": p.get("subtitle"),
                "authors": [a["name"] for a in p.get("authors", []) if a.get("name")],
                "narrators": [n["name"] for n in p.get("narrators", []) if n.get("name")],
                "series": series[0].get("title") if series else None,
                "cover": (p.get("product_images") or {}).get("500"),
                "year": rel[:4] or None,
                "release_date": rel or None,          # full YYYY-MM-DD (for release gating)
                "runtime_min": p.get("runtime_length_min"),
            })
        return out

    def _products(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        base = {"response_groups": RESPONSE_GROUPS, "image_sizes": "500",
                "content_type": "Product"}
        base.update(params)
        try:
            r = self._c.get(self.base, params=base)
            if r.status_code >= 400:
                return []
            products = r.json().get("products", [])
        except Exception:
            return []
        return self._normalize(products)

    def search(self, keywords: str, num: int = 24) -> list[dict[str, Any]]:
        return self._products({"keywords": keywords, "num_results": min(max(num, 1), 40),
                               "products_sort_by": "Relevance"})

    def browse(self, sort_by: str = "BestSellers", num: int = 20) -> list[dict[str, Any]]:
        """Keyword-less catalog browse for the discovery 'hero' rows.
        sort_by: 'BestSellers' (popular) or '-ReleaseDate' (new & upcoming)."""
        return self._products({"num_results": min(max(num, 1), 40), "products_sort_by": sort_by})

    def similar(self, asin: str, similarity_type: str = "RawSimilarities",
               num: int = 12) -> list[dict[str, Any]]:
        """Books related to `asin` on Audible's own similarity graph — used for
        discovery hero rows. similarity_type: InTheSameSeries, NextInSameSeries,
        ByTheSameAuthor, ByTheSameNarrator, or RawSimilarities."""
        if not asin:
            return []
        params = {"response_groups": RESPONSE_GROUPS, "image_sizes": "500",
                  "similarity_type": similarity_type, "num_results": min(max(num, 1), 20)}
        try:
            r = self._c.get(f"{self.base}/{asin}/sims", params=params)
            if r.status_code >= 400:
                return []
            products = r.json().get("similar_products", [])
        except Exception:
            return []
        return self._normalize(products)
