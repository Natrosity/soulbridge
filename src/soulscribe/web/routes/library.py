"""The library browser: a grid of what's already in Audiobookshelf, with search,
sort, and token-free cover proxying."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from ... import settings
from ...clients.abs import ABS
from ...clients.audible import product_url
from ..common import ctx, templates

router = APIRouter()

# library sort options: label -> (ABS sort key, descending?)
LIBRARY_SORTS = {
    "added": ("addedAt", True),
    "title": ("media.metadata.title", False),
    "author": ("media.metadata.authorNameLF", False),
    "released": ("media.metadata.publishedYear", True),
}


@router.get("/library", response_class=HTMLResponse)
def library_page(request: Request, page: int = 0, q: str = "", sort: str = "added"):
    url, key, lib = settings.get("abs_url"), settings.get("abs_api_key"), settings.get("abs_library_id")
    configured = bool(url and key and lib)
    q = (q or "").strip()
    sort = sort if sort in LIBRARY_SORTS else "added"
    per, items, total = 48, [], 0
    searching = bool(q)
    if configured:
        a = ABS(url, key)
        if searching:                               # search ignores paging/sort (ABS ranks it)
            items = a.search_items(lib, q, limit=per)
            total = len(items)
        else:
            skey, desc = LIBRARY_SORTS[sort]
            items, total = a.library_items(lib, limit=per, page=max(0, page), sort=skey, desc=desc)
        a.close()
        region = settings.get("audible_region") or "us"
        for it in items:
            it["audible_url"] = product_url(it.get("asin") or "", region)
    pages = 1 if searching else ((total + per - 1) // per if per else 1)
    return templates.TemplateResponse(request, "library.html", ctx(
        request, items=items, total=total, page=max(0, page), pages=pages, configured=configured,
        q=q, sort=sort, searching=searching, sorts=list(LIBRARY_SORTS.keys()),
    ))


@router.get("/library/cover/{item_id}")
def library_cover(item_id: str):
    url, key = settings.get("abs_url"), settings.get("abs_api_key")
    if not (url and key):
        return Response(status_code=404)
    a = ABS(url, key)
    data, ct = a.item_cover(item_id)
    a.close()
    if not data:
        return Response(status_code=404)
    return Response(content=data, media_type=ct, headers={"Cache-Control": "private, max-age=86400"})
