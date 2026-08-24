"""Author/series follows: browse and manage what you follow, and search
Audible to find someone new. New releases matching a follow are auto-requested
by the background worker (core/worker.py's _check_follows), gated by the
follower's role exactly like a manual request. Available to any logged-in
user — the role gate on the resulting request is what keeps this safe for
standard users too."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import db, settings
from ...clients.audible import Audible
from ...core import auth
from ..common import csrf_protect, ctx, templates

router = APIRouter()


def _following_names(username: str) -> tuple[set, set]:
    follows = db.list_follows(username)
    authors = {f["name"] for f in follows if f["kind"] == "author"}
    series = {f["name"] for f in follows if f["kind"] == "series"}
    return authors, series


@router.get("/follows", response_class=HTMLResponse)
def follows_page(request: Request, q: str = ""):
    user = request.state.user
    return templates.TemplateResponse(request, "follows.html", ctx(
        request, follows=db.list_follows(user["username"]), q=q, results=None))


@router.post("/follows/search", response_class=HTMLResponse, dependencies=[Depends(csrf_protect)])
def follows_search(request: Request, q: str = Form(...)):
    user = request.state.user
    aud = Audible(settings.get("audible_region") or "us")
    results = aud.search(q)
    aud.close()
    authors, series = _following_names(user["username"])
    for b in results:
        author = (b.get("authors") or [None])[0]
        b["following_author"] = bool(author and author in authors)
        b["following_series"] = bool(b.get("series") and b["series"] in series)
    return templates.TemplateResponse(request, "follows.html", ctx(
        request, follows=db.list_follows(user["username"]), q=q, results=results))


@router.post("/follows", dependencies=[Depends(csrf_protect)])
def follows_create(request: Request, kind: str = Form(...), name: str = Form(...),
                   ref_asin: str = Form("")):
    user = request.state.user
    if kind not in ("author", "series"):
        raise HTTPException(400, "bad kind")
    name = name.strip()
    if not name:
        raise HTTPException(400, "name required")
    if kind == "series" and not ref_asin:
        raise HTTPException(400, "a series follow needs a representative book")
    db.create_follow(kind, name, user["username"], ref_asin=ref_asin or None)
    db.log_event(f"{user['username']} followed {kind} '{name}'")
    return RedirectResponse("/follows", status_code=303)


@router.post("/follows/{follow_id}/delete", dependencies=[Depends(csrf_protect)])
def follows_delete(request: Request, follow_id: int):
    user = request.state.user
    f = db.get_follow(follow_id)
    if not f:
        raise HTTPException(404)
    if not auth.is_admin(user) and f["created_by"] != user["username"]:
        raise HTTPException(403, "Not your follow")
    db.delete_follow(follow_id)
    db.log_event(f"{user['username']} unfollowed {f['kind']} '{f['name']}'")
    return RedirectResponse("/follows", status_code=303)
