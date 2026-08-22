"""FastAPI app: authenticated request/fulfilment UI + JSON status API.

Security model (Phase 1):
- Every route requires a logged-in user except the public allowlist
  (login, setup, health, static). Enforced fail-closed in middleware.
- Admin-only surface (operational + settings + user management) is gated by
  path in the same middleware.
- Signed session cookies, argon2 passwords, per-session CSRF on unsafe methods,
  login lockout, and security headers incl. a strict CSP (no inline JS).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .. import __version__, db, settings
from ..clients.abs import ABS
from ..core import auth, worker

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

_SEARCH_CACHE: dict[str, dict[str, Any]] = {}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
       "img-src 'self' data: https:; font-src 'self'; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")

# public (no auth); prefixes matched with startswith
PUBLIC_EXACT = {"/login", "/setup", "/health", "/favicon.ico"}
PUBLIC_PREFIX = ("/static/",)
# admin-only; everything else that is authed is available to any logged-in user
ADMIN_EXACT = {"/"}
ADMIN_PREFIX = ("/settings", "/users", "/search", "/grab", "/items", "/partials", "/tags", "/api")


def _is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or any(path.startswith(p) for p in PUBLIC_PREFIX)


def _is_admin_path(path: str) -> bool:
    return path in ADMIN_EXACT or any(path.startswith(p) for p in ADMIN_PREFIX)


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "") and request.method == "GET"


class Guard(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        resp: Optional[Response] = None
        request.state.user = None
        if not _is_public(path):
            # first run: force admin creation
            if db.user_count() == 0:
                resp = RedirectResponse("/setup", status_code=303)
            else:
                user = auth.current_user(request)
                if not user:
                    resp = (RedirectResponse("/login", status_code=303)
                            if _wants_html(request) else Response("Unauthorized", 401))
                elif _is_admin_path(path) and not auth.is_admin(user):
                    resp = (RedirectResponse("/library", status_code=303)
                            if _wants_html(request) else Response("Forbidden", 403))
                else:
                    request.state.user = user
        if resp is None:
            resp = await call_next(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = CSP
        return resp


app = FastAPI(title="Soulbridge", version=__version__)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
app.add_middleware(Guard)
app.add_middleware(SessionMiddleware, secret_key=auth.get_secret(), same_site="lax",
                   https_only=False, max_age=14 * 24 * 3600)


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


async def csrf_protect(request: Request) -> None:
    form = await request.form()
    if not auth.csrf_ok(request, form.get("csrf")):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def _require_admin(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not auth.is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def _ctx(request: Request, **extra: Any) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    ctx = {
        "request": request, "version": __version__,
        "instance": settings.get("instance_name") or "Soulbridge",
        "wstatus": worker.STATUS, "badges": STATUS_BADGES,
        "library_available": bool(settings.get("abs_url") and settings.get("abs_api_key")
                                  and settings.get("abs_library_id")),
        "user": user, "is_admin": auth.is_admin(user),
        "csrf": auth.csrf_token(request),
    }
    ctx.update(extra)
    return ctx


# ----------------------------------------------------------------- auth pages
@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if db.user_count() > 0:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", _ctx(request, error=None))


@app.post("/setup", dependencies=[Depends(csrf_protect)])
def setup_submit(request: Request, username: str = Form(...), password: str = Form(...),
                 confirm: str = Form(...)):
    if db.user_count() > 0:
        return RedirectResponse("/login", status_code=303)
    err = _password_problem(username, password, confirm)
    if err:
        return templates.TemplateResponse(request, "setup.html", _ctx(request, error=err))
    uid = db.create_user(username.strip(), auth.hash_pw(password), role="admin")
    auth.login_session(request, db.get_user(uid))
    db.log_event(f"Admin account '{username.strip()}' created")
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if db.user_count() == 0:
        return RedirectResponse("/setup", status_code=303)
    if auth.current_user(request):
        return RedirectResponse("/", status_code=303)
    auth.csrf_token(request)                       # ensure token exists for the form
    return templates.TemplateResponse(request, "login.html", _ctx(request, error=None))


@app.post("/login", dependencies=[Depends(csrf_protect)])
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_name(username.strip())
    fail = templates.TemplateResponse(
        request, "login.html", _ctx(request, error="Invalid username or password."))
    if not user:
        return fail
    if auth.is_locked(user):
        return templates.TemplateResponse(request, "login.html", _ctx(
            request, error="Account temporarily locked. Try again later."))
    if not auth.verify_pw(user.get("password_hash"), password):
        auth.register_failure(user)
        return fail
    auth.register_success(user)
    auth.login_session(request, user)
    return RedirectResponse("/", status_code=303)


@app.post("/logout", dependencies=[Depends(csrf_protect)])
def logout(request: Request):
    auth.logout_session(request)
    return RedirectResponse("/login", status_code=303)


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, saved: int = 0):
    return templates.TemplateResponse(request, "account.html",
                                      _ctx(request, saved=bool(saved), error=None))


@app.post("/account", dependencies=[Depends(csrf_protect)])
def account_submit(request: Request, current: str = Form(""), password: str = Form(""),
                   confirm: str = Form("")):
    user = request.state.user
    if password:
        if not auth.verify_pw(user.get("password_hash"), current):
            return templates.TemplateResponse(request, "account.html",
                                              _ctx(request, error="Current password is incorrect."))
        err = _password_problem(user["username"], password, confirm, check_user=False)
        if err:
            return templates.TemplateResponse(request, "account.html", _ctx(request, error=err))
        db.update_user(user["id"], password_hash=auth.hash_pw(password))
    return RedirectResponse("/account?saved=1", status_code=303)


# ------------------------------------------------------------- user management
@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    _require_admin(request)
    return templates.TemplateResponse(request, "users.html", _ctx(
        request, users=db.list_users(), roles=auth.ROLES, error=None))


@app.post("/users", dependencies=[Depends(csrf_protect)])
def users_create(request: Request, username: str = Form(...), password: str = Form(...),
                 role: str = Form("standard")):
    _require_admin(request)
    role = role if role in auth.ROLES else "standard"
    err = _password_problem(username, password, password)
    if err:
        return templates.TemplateResponse(request, "users.html", _ctx(
            request, users=db.list_users(), roles=auth.ROLES, error=err))
    db.create_user(username.strip(), auth.hash_pw(password), role=role)
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{user_id}/role", dependencies=[Depends(csrf_protect)])
def users_set_role(request: Request, user_id: int, role: str = Form(...)):
    admin = _require_admin(request)
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404)
    if role not in auth.ROLES:
        raise HTTPException(400, "bad role")
    # don't let an admin demote the last admin (including themselves)
    if target["role"] == "admin" and role != "admin" and _admin_count() <= 1:
        raise HTTPException(400, "cannot demote the last admin")
    db.update_user(user_id, role=role)
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{user_id}/password", dependencies=[Depends(csrf_protect)])
def users_reset_pw(request: Request, user_id: int, password: str = Form(...)):
    _require_admin(request)
    if not db.get_user(user_id):
        raise HTTPException(404)
    if len(password) < 10:
        raise HTTPException(400, "password too short")
    db.update_user(user_id, password_hash=auth.hash_pw(password),
                   failed_attempts=0, locked_until=None)
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{user_id}/delete", dependencies=[Depends(csrf_protect)])
def users_delete(request: Request, user_id: int):
    admin = _require_admin(request)
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404)
    if target["id"] == admin["id"]:
        raise HTTPException(400, "cannot delete yourself")
    if target["role"] == "admin" and _admin_count() <= 1:
        raise HTTPException(400, "cannot delete the last admin")
    db.delete_user(user_id)
    return RedirectResponse("/users", status_code=303)


def _admin_count() -> int:
    return sum(1 for u in db.list_users() if u["role"] == "admin")


def _password_problem(username: str, password: str, confirm: str, check_user: bool = True) -> Optional[str]:
    if check_user:
        if not username.strip():
            return "Username is required."
        if db.get_user_by_name(username.strip()):
            return "That username is taken."
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if password != confirm:
        return "Passwords do not match."
    return None


# ------------------------------------------------------------------ dashboard
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


@app.post("/search", response_class=HTMLResponse, dependencies=[Depends(csrf_protect)])
def do_search(request: Request, title: str = Form(...), author: str = Form(""),
              item: Optional[int] = Form(None)):
    results = worker.manual_search(title, author)
    token = uuid.uuid4().hex
    _SEARCH_CACHE[token] = {"ts": time.time(), "title": title, "author": author,
                            "item": item, "results": results}
    for k in [k for k, v in _SEARCH_CACHE.items() if time.time() - v["ts"] > 1800]:
        _SEARCH_CACHE.pop(k, None)
    return templates.TemplateResponse(request, "search.html", _ctx(
        request, title=title, author=author, item_id=item, results=results, token=token,
    ))


@app.post("/grab", dependencies=[Depends(csrf_protect)])
def do_grab(title: str = Form(...), author: str = Form(""), token: str = Form(...),
            index: int = Form(...), item: Optional[int] = Form(None)):
    cached = _SEARCH_CACHE.get(token)
    if not cached or index >= len(cached["results"]):
        return RedirectResponse("/search", status_code=303)
    chosen = cached["results"][index]
    item_id = item or db.upsert_item("manual", uuid.uuid4().hex, title=title, author=author,
                                     status="pending")
    worker.grab(item_id, chosen["username"], chosen["file_list"], chosen["directory"])
    return RedirectResponse("/", status_code=303)


@app.post("/items/{item_id}/search", dependencies=[Depends(csrf_protect)])
def item_search_now(item_id: int):
    db.update_item(item_id, status="pending", attempts=0, error=None)
    worker.wake()
    return RedirectResponse("/", status_code=303)


@app.post("/items/{item_id}/retry", dependencies=[Depends(csrf_protect)])
def item_retry(item_id: int):
    db.update_item(item_id, status="pending", attempts=0, error=None)
    worker.wake()
    return RedirectResponse("/", status_code=303)


@app.post("/items/{item_id}/skip", dependencies=[Depends(csrf_protect)])
def item_skip(item_id: int):
    db.update_item(item_id, status="skipped")
    return RedirectResponse("/", status_code=303)


@app.get("/tags", response_class=HTMLResponse)
def tags_page(request: Request):
    return templates.TemplateResponse(request, "tags.html", _ctx(
        request, writes=db.recent_tag_writes(100),
    ))


# -------------------------------------------------------------------- library
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
    return Response(content=data, media_type=ct, headers={"Cache-Control": "private, max-age=86400"})


# -------------------------------------------------------------------- settings
@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0):
    grouped: dict[str, list[Any]] = {g: [] for g in settings.groups()}
    for f in settings.SPEC:
        secret = settings.is_secret(f.key)
        grouped[f.group].append({
            "field": f,
            "value": "" if secret else settings.get(f.key),
            "is_set": bool(settings.get(f.key)) if secret else False,
        })
    return templates.TemplateResponse(request, "settings.html", _ctx(
        request, grouped=grouped, saved=bool(saved),
    ))


@app.post("/settings", dependencies=[Depends(csrf_protect)])
async def save_settings(request: Request):
    form = await request.form()
    for f in settings.SPEC:
        if f.type == "bool":
            db.set_setting(f.key, "true" if form.get(f.key) else "false")
        elif settings.is_secret(f.key):
            v = form.get(f.key)
            if v:                                   # blank = keep existing secret
                db.set_setting(f.key, str(v))
        elif f.key in form:
            val = str(form.get(f.key))
            if f.key == "default_role" and val not in ("standard", "trusted"):
                continue
            db.set_setting(f.key, val)
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
