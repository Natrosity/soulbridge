"""FastAPI app: dashboard, manual search/grab, settings, and a JSON status API."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__, db, settings
from ..clients.abs import ABS
from ..core import worker

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

# short-lived cache of manual search results, so "Grab" doesn't re-search
_SEARCH_CACHE: dict[str, dict[str, Any]] = {}

app = FastAPI(title="Soulbridge", version=__version__)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.init()
    settings.seed_from_env()
    if not worker.STATUS.get("running"):
        worker.start()

STATUS_BADGES = {
    "pending": "wait", "searching": "wait", "downloading": "busy", "importing": "busy",
    "done": "ok", "no_results": "warn", "failed": "err", "skipped": "muted",
}


def _ctx(request: Request, **extra: Any) -> dict[str, Any]:
    ctx = {
        "request": request, "version": __version__,
        "instance": settings.get("instance_name") or "Soulbridge",
        "wstatus": worker.STATUS, "badges": STATUS_BADGES,
        "library_available": bool(settings.get("abs_url") and settings.get("abs_api_key")
                                  and settings.get("abs_library_id")),
    }
    ctx.update(extra)
    return ctx


def _dashboard_ctx(request: Request) -> dict[str, Any]:
    return _ctx(
        request,
        active=db.list_items(statuses=["pending", "searching", "downloading", "importing"]),
        attention=db.list_items(statuses=["no_results", "failed"]),
        done=db.list_items(statuses=["done"], limit=30),
        counts=db.counts_by_status(), events=db.recent_events(50),
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", _dashboard_ctx(request))


@app.get("/partials/dashboard", response_class=HTMLResponse)
def dashboard_partial(request: Request):
    return templates.TemplateResponse(request, "_dashboard_body.html", _dashboard_ctx(request))


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, title: str = "", author: str = "", item: Optional[int] = None):
    return templates.TemplateResponse(request, "search.html", _ctx(
        request, title=title, author=author, item_id=item, results=None, token=None,
    ))


@app.post("/search", response_class=HTMLResponse)
def do_search(request: Request, title: str = Form(...), author: str = Form(""),
              item: Optional[int] = Form(None)):
    results = worker.manual_search(title, author)
    token = uuid.uuid4().hex
    _SEARCH_CACHE[token] = {"ts": time.time(), "title": title, "author": author,
                            "item": item, "results": results}
    # evict old cache entries
    for k in [k for k, v in _SEARCH_CACHE.items() if time.time() - v["ts"] > 1800]:
        _SEARCH_CACHE.pop(k, None)
    return templates.TemplateResponse(request, "search.html", _ctx(
        request, title=title, author=author, item_id=item, results=results, token=token,
    ))


@app.post("/grab")
def do_grab(title: str = Form(...), author: str = Form(""), token: str = Form(...),
            index: int = Form(...), item: Optional[int] = Form(None)):
    cached = _SEARCH_CACHE.get(token)
    if not cached or index >= len(cached["results"]):
        return RedirectResponse("/search", status_code=303)
    chosen = cached["results"][index]
    item_id = item
    if not item_id:
        item_id = db.upsert_item("manual", uuid.uuid4().hex, title=title, author=author,
                                 status="pending")
    worker.grab(item_id, chosen["username"], chosen["file_list"], chosen["directory"])
    return RedirectResponse("/", status_code=303)


@app.post("/items/{item_id}/search")
def item_search_now(item_id: int):
    db.update_item(item_id, status="pending", attempts=0, error=None)
    worker.wake()
    return RedirectResponse("/", status_code=303)


@app.post("/items/{item_id}/retry")
def item_retry(item_id: int):
    db.update_item(item_id, status="pending", attempts=0, error=None)
    worker.wake()
    return RedirectResponse("/", status_code=303)


@app.post("/items/{item_id}/skip")
def item_skip(item_id: int):
    db.update_item(item_id, status="skipped")
    return RedirectResponse("/", status_code=303)


@app.get("/tags", response_class=HTMLResponse)
def tags_page(request: Request):
    return templates.TemplateResponse(request, "tags.html", _ctx(
        request, writes=db.recent_tag_writes(100),
    ))


@app.get("/library", response_class=HTMLResponse)
def library_page(request: Request, page: int = 0):
    url, key, lib = settings.get("abs_url"), settings.get("abs_api_key"), settings.get("abs_library_id")
    configured = bool(url and key and lib)
    per, items, total = 48, [], 0
    if configured:
        a = ABS(url, key)
        items, total = a.library_items(lib, limit=per, page=max(0, page), sort="addedAt", desc=True)
        a.close()
    pages = (total + per - 1) // per if per else 1
    return templates.TemplateResponse(request, "library.html", _ctx(
        request, items=items, total=total, page=max(0, page), pages=pages, configured=configured,
    ))


@app.get("/library/cover/{item_id}")
def library_cover(item_id: str):
    url, key = settings.get("abs_url"), settings.get("abs_api_key")
    if not (url and key):
        return Response(status_code=404)
    a = ABS(url, key)
    data, ct = a.item_cover(item_id)
    a.close()
    if not data:
        return Response(status_code=404)
    return Response(content=data, media_type=ct, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0):
    grouped: dict[str, list[Any]] = {g: [] for g in settings.groups()}
    for f in settings.SPEC:
        grouped[f.group].append({"field": f, "value": settings.get(f.key)})
    return templates.TemplateResponse(request, "settings.html", _ctx(
        request, grouped=grouped, saved=bool(saved),
    ))


@app.post("/settings")
async def save_settings(request: Request):
    form = await request.form()
    for f in settings.SPEC:
        if f.type == "bool":
            db.set_setting(f.key, "true" if form.get(f.key) else "false")
        elif f.key in form:
            db.set_setting(f.key, str(form.get(f.key)))
    worker.wake()
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.get("/api/status")
def api_status():
    return JSONResponse({
        "service": "soulbridge", "version": __version__,
        "worker": worker.STATUS, "counts": db.counts_by_status(),
    })


@app.get("/health")
def health():
    return {"status": "ok"}
