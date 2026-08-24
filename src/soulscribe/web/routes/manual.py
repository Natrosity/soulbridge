"""Admin manual tools: free-form Soulseek search + grab, per-item actions
(search-now / retry / skip), and the tag-write history."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import db
from ...core import worker
from ..common import SEARCH_CACHE, csrf_protect, ctx, templates

router = APIRouter()


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, title: str = "", author: str = "", item: Optional[int] = None):
    return templates.TemplateResponse(request, "search.html", ctx(
        request, title=title, author=author, item_id=item, results=None, token=None,
    ))


@router.post("/search", response_class=HTMLResponse, dependencies=[Depends(csrf_protect)])
def do_search(request: Request, title: str = Form(...), author: str = Form(""),
              item: Optional[int] = Form(None)):
    results = worker.manual_search(title, author)
    token = uuid.uuid4().hex
    SEARCH_CACHE.set(token, {"title": title, "author": author,
                             "item": item, "results": results})
    return templates.TemplateResponse(request, "search.html", ctx(
        request, title=title, author=author, item_id=item, results=results, token=token,
    ))


@router.post("/grab", dependencies=[Depends(csrf_protect)])
def do_grab(title: str = Form(...), author: str = Form(""), token: str = Form(...),
            index: int = Form(...), item: Optional[int] = Form(None)):
    cached = SEARCH_CACHE.get(token)
    if not cached or index >= len(cached["results"]):
        return RedirectResponse("/search", status_code=303)
    chosen = cached["results"][index]
    item_id = item or db.upsert_item("manual", uuid.uuid4().hex, title=title, author=author,
                                     status="pending")
    worker.grab(item_id, chosen["username"], chosen["file_list"], chosen["directory"])
    return RedirectResponse("/", status_code=303)


@router.post("/items/{item_id}/search", dependencies=[Depends(csrf_protect)])
def item_search_now(item_id: int):
    db.update_item(item_id, status="pending", attempts=0, error=None)
    worker.wake()
    return RedirectResponse("/", status_code=303)


@router.post("/items/{item_id}/retry", dependencies=[Depends(csrf_protect)])
def item_retry(item_id: int):
    db.update_item(item_id, status="pending", attempts=0, error=None)
    worker.wake()
    return RedirectResponse("/", status_code=303)


@router.post("/items/{item_id}/skip", dependencies=[Depends(csrf_protect)])
def item_skip(item_id: int):
    db.update_item(item_id, status="skipped")
    return RedirectResponse("/", status_code=303)


@router.get("/tags", response_class=HTMLResponse)
def tags_page(request: Request):
    return templates.TemplateResponse(request, "tags.html", ctx(
        request, writes=db.recent_tag_writes(100),
    ))
