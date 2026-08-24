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

from .. import __version__, cache, db, settings
from ..env import env
from ..clients import notify, plextv
from ..clients.abr import ABR
from ..clients.abs import ABS, library_key
from ..clients.audible import Audible, product_url
from ..clients.plex import Plex
from ..core import auth, worker

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

_SEARCH_CACHE = cache.TTLCache(1800)            # token -> a manual/interactive search result set
_PLEX_PENDING = cache.TTLCache(900)             # oauth state -> {pin_id}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
       "img-src 'self' data: https:; font-src 'self'; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")

# public (no auth); prefixes matched with startswith
PUBLIC_EXACT = {"/login", "/setup", "/health", "/favicon.ico"}
PUBLIC_PREFIX = ("/static/", "/auth/plex")
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
                    resp = (RedirectResponse("/discover", status_code=303)
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


app = FastAPI(title="Soulscribe", version=__version__)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
app.add_middleware(Guard)
# Mark the session cookie Secure when serving strictly over HTTPS. Off by default
# because the container also publishes plain-http :8793 for direct LAN access (a
# Secure cookie wouldn't be sent there); set SOULSCRIBE_SECURE_COOKIES=true once
# all access is via the HTTPS reverse proxy.
_SECURE_COOKIES = (env("SECURE_COOKIES", "") or "").strip().lower() in (
    "1", "true", "yes", "on")
app.add_middleware(SessionMiddleware, secret_key=auth.get_secret(), same_site="lax",
                   https_only=_SECURE_COOKIES, max_age=14 * 24 * 3600)


@app.on_event("startup")
def _startup() -> None:
    db.init()
    settings.seed_from_env()
    if not worker.STATUS.get("running"):
        worker.start()


STATUS_BADGES = {
    "awaiting_approval": "wait", "scheduled": "wait", "selecting": "wait",
    "pending": "wait", "searching": "wait",
    "downloading": "busy", "importing": "busy",
    "done": "ok", "no_results": "warn", "failed": "err", "skipped": "muted", "denied": "muted",
}
STATUS_LABELS = {
    "awaiting_approval": "awaiting approval", "scheduled": "awaiting release",
    "selecting": "choosing a source",
    "pending": "queued", "searching": "searching",
    "downloading": "downloading", "importing": "importing", "done": "in your library",
    "no_results": "not found on Soulseek", "failed": "failed", "skipped": "skipped",
    "denied": "declined",
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
        "instance": settings.get("instance_name") or "Soulscribe",
        "wstatus": worker.STATUS, "badges": STATUS_BADGES, "status_labels": STATUS_LABELS,
        "library_available": bool(settings.get("abs_url") and settings.get("abs_api_key")
                                  and settings.get("abs_library_id")),
        "user": user, "is_admin": auth.is_admin(user),
        "csrf": auth.csrf_token(request),
        "pending_approvals": (db.counts_by_status().get("awaiting_approval", 0)
                              if auth.is_admin(user) else 0),
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


_PLEX_LOGIN_ERRORS = {
    "plex": "Plex sign-in failed. Please try again.",
    "plex_denied": "That Plex account doesn't have access to this server.",
}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    if db.user_count() == 0:
        return RedirectResponse("/setup", status_code=303)
    if auth.current_user(request):
        return RedirectResponse("/", status_code=303)
    auth.csrf_token(request)                       # ensure token exists for the form
    return templates.TemplateResponse(request, "login.html", _ctx(
        request, error=_PLEX_LOGIN_ERRORS.get(error), plex_login=_plex_enabled()))


@app.post("/login", dependencies=[Depends(csrf_protect)])
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_name(username.strip())
    fail = templates.TemplateResponse(
        request, "login.html", _ctx(request, error="Invalid username or password.",
                                    plex_login=_plex_enabled()))
    if not user:
        auth.waste_time(password)          # equalise timing so usernames can't be enumerated
        return fail
    if auth.is_locked(user):
        return templates.TemplateResponse(request, "login.html", _ctx(
            request, error="Account temporarily locked. Try again later.",
            plex_login=_plex_enabled()))
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


# --------------------------------------------------------------- Sign in with Plex
def _plex_enabled() -> bool:
    return bool(settings.get_bool("plex_login_enabled")
                and settings.get("plex_url") and settings.get("plex_token"))


def _plex_client_id() -> str:
    """A stable client identifier for this install (Plex ties the PIN to it)."""
    cid = db.get_setting("plex_client_id")
    if not cid:
        cid = uuid.uuid4().hex
        db.set_setting("plex_client_id", cid)
    return cid


def _public_base(request: Request) -> str:
    return (settings.get("public_url") or str(request.base_url)).rstrip("/")


def _unique_username(base: str) -> str:
    name = (base or "").strip() or "plexuser"
    if not db.get_user_by_name(name):
        return name
    i = 2
    while db.get_user_by_name(f"{name}{i}"):
        i += 1
    return f"{name}{i}"


def _plex_provision(acct: dict[str, Any]) -> dict[str, Any]:
    """Find the Soulscribe user for a verified Plex account (match by plex_id only —
    never by name, so a matching username can't hijack an internal account), creating
    one at the default role on first sign-in."""
    existing = db.get_user_by_plex_id(acct["id"])
    if existing:
        return existing
    role = settings.get("default_role")
    role = role if role in ("standard", "trusted") else "standard"
    uname = _unique_username(acct.get("username") or f"plex-{acct['id']}")
    uid = db.create_user(uname, None, role=role, email=acct.get("email"), plex_id=acct["id"])
    db.log_event(f"Provisioned Plex user '{uname}' (role {role})")
    return db.get_user(uid)


@app.get("/auth/plex/start")
def plex_start(request: Request):
    if not _plex_enabled():
        return RedirectResponse("/login", status_code=303)
    cid = _plex_client_id()
    ptv = plextv.PlexTV(cid, version=__version__)
    try:
        pin = ptv.create_pin()
    except Exception as e:
        db.log_event(f"Plex sign-in: PIN creation failed: {e}", "warn")
        return RedirectResponse("/login?error=plex", status_code=303)
    finally:
        ptv.close()
    state = uuid.uuid4().hex
    _PLEX_PENDING.set(state, {"pin_id": pin["id"]})
    request.session["plex_state"] = state          # bind the flow to this browser
    forward = f"{_public_base(request)}/auth/plex/callback?state={state}"
    return RedirectResponse(plextv.auth_url(cid, pin["code"], forward), status_code=303)


@app.get("/auth/plex/callback")
def plex_callback(request: Request, state: str = ""):
    if not _plex_enabled():
        return RedirectResponse("/login", status_code=303)
    pending = _PLEX_PENDING.pop(state, None)
    sess_state = request.session.pop("plex_state", None)
    if not state or not pending or state != sess_state:
        return RedirectResponse("/login?error=plex", status_code=303)
    cid = _plex_client_id()
    ptv = plextv.PlexTV(cid, version=__version__)
    try:
        token = None
        for _ in range(6):                          # PIN is usually authorised by now
            token = ptv.check_pin(pending["pin_id"])
            if token:
                break
            time.sleep(1)
        if not token:
            return RedirectResponse("/login?error=plex", status_code=303)
        acct = ptv.account(token)
        if not acct or not acct.get("id"):
            return RedirectResponse("/login?error=plex", status_code=303)
        # Gate: the account must be able to reach the configured Plex server.
        p = Plex(settings.get("plex_url"), settings.get("plex_token"))
        machine = p.machine_identifier()
        p.close()
        if not machine or not plextv.server_in_resources(ptv.resources(token), machine):
            db.log_event(f"Plex sign-in denied for '{acct.get('username')}' "
                         "(no access to the server)", "warn")
            return RedirectResponse("/login?error=plex_denied", status_code=303)
    finally:
        ptv.close()
    user = _plex_provision(acct)
    auth.login_session(request, user)
    db.log_event(f"{user['username']} signed in with Plex")
    return RedirectResponse("/", status_code=303)


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, saved: int = 0):
    user = request.state.user
    return templates.TemplateResponse(request, "account.html", _ctx(
        request, saved=bool(saved), error=None, has_password=bool(user.get("password_hash"))))


@app.post("/account", dependencies=[Depends(csrf_protect)])
def account_submit(request: Request, current: str = Form(""), password: str = Form(""),
                   confirm: str = Form("")):
    user = request.state.user
    has_pw = bool(user.get("password_hash"))

    def _err(msg: str):
        return templates.TemplateResponse(request, "account.html",
                                          _ctx(request, error=msg, has_password=has_pw))

    if password:
        # A Plex-provisioned account has no password yet, so there's nothing to verify —
        # let them set an initial one. Accounts that already have a password must confirm it.
        if has_pw and not auth.verify_pw(user.get("password_hash"), current):
            return _err("Current password is incorrect.")
        err = _password_problem(user["username"], password, confirm, check_user=False)
        if err:
            return _err(err)
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
        active=db.list_items(statuses=["scheduled", "selecting", "pending", "searching", "downloading", "importing"]),
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
    _SEARCH_CACHE.set(token, {"title": title, "author": author,
                              "item": item, "results": results})
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


@app.post("/items/{item_id}/approve", dependencies=[Depends(csrf_protect)])
def item_approve(request: Request, item_id: int):
    admin = _require_admin(request)
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(404)
    if item["status"] != "awaiting_approval":
        return RedirectResponse("/requests/all", status_code=303)   # already handled
    # standard-user requests are stored mode=auto, so approval queues an auto-grab —
    # unless the book isn't out yet, in which case hold it until its release date.
    rd = (item.get("release_date") or "").strip()
    scheduled = bool(rd and rd > db.today())
    db.update_item(item_id, status="scheduled" if scheduled else "pending",
                   attempts=0, error=None)
    if not scheduled:
        worker.wake()
    db.log_event(f"{admin['username']} approved '{item['title']}'"
                 + (f" — scheduled for {rd}" if scheduled else "")
                 + f" (requested by {item.get('requested_by') or 'unknown'})", item_id=item_id)
    return RedirectResponse("/requests/all", status_code=303)


@app.post("/items/{item_id}/deny", dependencies=[Depends(csrf_protect)])
def item_deny(request: Request, item_id: int):
    admin = _require_admin(request)
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(404)
    if item["status"] != "awaiting_approval":
        return RedirectResponse("/requests/all", status_code=303)
    db.update_item(item_id, status="denied", error=None)
    db.log_event(f"{admin['username']} declined '{item['title']}'"
                 f" (requested by {item.get('requested_by') or 'unknown'})", "warn", item_id)
    return RedirectResponse("/requests/all", status_code=303)


_IMPORT_MSGS = {
    "none": ("warn", "AudioBookRequest isn't configured, so there's nothing to import."),
    "error": ("err", "Import failed — check the AudioBookRequest connection."),
    "0": ("ok", "Nothing new to import — Soulscribe is already up to date."),
}


@app.get("/requests/all", response_class=HTMLResponse)
def all_requests(request: Request, imported: str = ""):
    _require_admin(request)
    awaiting = db.list_items(statuses=["awaiting_approval"])
    others = [it for it in db.list_items(limit=300) if it["status"] != "awaiting_approval"]
    banner = None
    if imported:
        level, msg = _IMPORT_MSGS.get(
            imported, ("ok", f"Imported {imported} request(s) from AudioBookRequest."))
        banner = {"level": level, "msg": msg}
    return templates.TemplateResponse(request, "all_requests.html", _ctx(
        request, awaiting=awaiting, items=others, banner=banner, blocks=db.list_blocks(),
        abr_configured=bool(settings.get("abr_url") and settings.get("abr_api_key"))))


@app.post("/blocklist/{block_id}/remove", dependencies=[Depends(csrf_protect)])
def blocklist_remove(request: Request, block_id: int):
    _require_admin(request)
    db.remove_block(block_id)
    return RedirectResponse("/requests/all", status_code=303)


@app.post("/requests/import-abr", dependencies=[Depends(csrf_protect)])
def import_abr(request: Request):
    """One-off cutover aid: pull existing AudioBookRequest history into Soulscribe so
    past requests survive after ABR is retired. New books only (upsert dedupes);
    already-fulfilled ABR requests come in as 'done', the rest as 'pending'."""
    _require_admin(request)
    if not (settings.get("abr_url") and settings.get("abr_api_key")):
        return RedirectResponse("/requests/all?imported=none", status_code=303)
    abr = ABR(settings.get("abr_url"), settings.get("abr_api_key"))
    imported = 0
    try:
        all_reqs = abr.list_requests(only_pending=False)
        pending_asins = {(_abr_book(r).get("asin")) for r in abr.list_requests(only_pending=True)}
        for r in all_reqs:
            book = _abr_book(r)
            asin = book.get("asin")
            if not asin or db.get_item_by_source("abr", asin):
                continue
            authors = book.get("authors") or []
            narrators = book.get("narrators") or []
            reqrs = r.get("requests") or []
            db.upsert_item(
                "abr", asin, title=book.get("title", ""),
                author=authors[0] if authors else "",
                narrator=narrators[0] if narrators else "",
                cover=book.get("cover_image"),
                status="pending" if asin in pending_asins else "done",
                requested_by=(reqrs[0].get("user_username") if reqrs else None),
            )
            imported += 1
    except Exception as e:
        db.log_event(f"ABR history import failed: {e}", "warn")
        return RedirectResponse("/requests/all?imported=error", status_code=303)
    finally:
        abr.close()
    if imported:
        worker.wake()
        db.log_event(f"Imported {imported} request(s) from AudioBookRequest history")
    return RedirectResponse(f"/requests/all?imported={imported}", status_code=303)


def _abr_book(r: dict[str, Any]) -> dict[str, Any]:
    return r.get("book") or r


@app.post("/notify/test", dependencies=[Depends(csrf_protect)])
def notify_test(request: Request):
    _require_admin(request)
    ok, _msg = notify.test()
    code = "ok" if ok else ("none" if not notify.urls() else "bad")
    return RedirectResponse(f"/settings?ntest={code}", status_code=303)


def _default_mode() -> str:
    m = settings.get("default_request_mode")
    return m if m in ("auto", "interactive") else "auto"


def _quota_message(user: dict[str, Any]) -> Optional[str]:
    """Return a human message if this user is at their open-request quota, else None.
    Admins are exempt; quota 0 means unlimited."""
    if auth.is_admin(user):
        return None
    quota = settings.get_int("request_quota", 0)
    if quota <= 0:
        return None
    if db.count_open_requests(user["username"]) >= quota:
        return (f"You've reached your limit of {quota} open request"
                f"{'s' if quota != 1 else ''}. Wait for one to finish, then try again.")
    return None


# cached discovery aids
_LIB_INDEX = cache.TTLCache(300)                # "lib" -> (asins, keys), 5 min
_BROWSE = cache.TTLCache(6 * 3600)              # Audible sort -> browse items, 6h
# (row label, Audible sort, which releases to show: 'released' | 'upcoming')
HERO_ROWS = (("Bestsellers", "BestSellers", "released"),
             ("Releasing Soon", "-ReleaseDate", "upcoming"))


def _library_index() -> tuple[set, set]:
    """(asins, title|surname keys) of the ABS library, cached for 5 min."""
    url, key, lib = settings.get("abs_url"), settings.get("abs_api_key"), settings.get("abs_library_id")
    if not (url and key and lib):
        return set(), set()
    hit = _LIB_INDEX.get("lib")
    if hit is not None:
        return hit
    a = ABS(url, key)
    asins, keys = a.library_index(lib)
    a.close()
    _LIB_INDEX.set("lib", (asins, keys))
    return asins, keys


def _mark_results(results: list[dict[str, Any]]) -> None:
    """Flag each discover listing with its prior-request status, whether the book is
    already in the Audiobookshelf library (so we don't allow a duplicate), whether
    it's still upcoming, and its Audible page URL."""
    asins, keys = _library_index()
    today = db.today()
    region = settings.get("audible_region") or "us"
    for r in results:
        if "requested_status" not in r:
            ex = db.get_item_by_source("user", r["asin"]) if r.get("asin") else None
            r["requested_status"] = ex["status"] if ex else None
        author = (r.get("authors") or [""])[0] if r.get("authors") else ""
        k = library_key(r.get("title"), author)
        r["in_library"] = bool((r.get("asin") and r["asin"] in asins) or (k and k in keys))
        rd = r.get("release_date") or ""
        r["upcoming"] = bool(rd and rd > today)
        r["audible_url"] = product_url(r.get("asin") or "", region)


def _hero_rows() -> list[dict[str, Any]]:
    """Curated Audible browse rows for the discovery page (cached 6h; status marks
    refreshed per request). Bestsellers shows only already-released titles;
    'Releasing Soon' shows only not-yet-out titles."""
    region = settings.get("audible_region") or "us"
    today = db.today()
    rows: list[dict[str, Any]] = []
    for label, sort, which in HERO_ROWS:
        items = _BROWSE.get(sort)
        if items is None:
            aud = Audible(region)
            fetched = aud.browse(sort, 40)          # fetch wide, then filter by release
            aud.close()
            if fetched:
                _BROWSE.set(sort, fetched)
                items = fetched
            else:
                items = _BROWSE.peek(sort) or []    # keep last-good on a transient failure
        if which == "upcoming":
            items = [b for b in items if (b.get("release_date") or "") > today]
        else:
            items = [b for b in items if not b.get("release_date") or b["release_date"] <= today]
        items = items[:18]
        if items:
            deco = [dict(it) for it in items]       # fresh status marks each render
            _mark_results(deco)
            rows.append({"label": label, "books": deco})   # not 'items' (Jinja dict-method clash)
    return rows


@app.get("/discover", response_class=HTMLResponse)
def discover_page(request: Request, q: str = "", err: str = ""):
    return templates.TemplateResponse(request, "discover.html", _ctx(
        request, q=q, results=None, heroes=_hero_rows(), default_mode=_default_mode(),
        can_interactive=auth.is_trusted(request.state.user),
        error=(_quota_message(request.state.user) if err == "quota" else None)))


@app.post("/discover", response_class=HTMLResponse, dependencies=[Depends(csrf_protect)])
def discover_search(request: Request, q: str = Form(...)):
    aud = Audible(settings.get("audible_region") or "us")
    results = aud.search(q)
    aud.close()
    _mark_results(results)
    return templates.TemplateResponse(request, "discover.html", _ctx(
        request, q=q, results=results, heroes=None, default_mode=_default_mode(),
        can_interactive=auth.is_trusted(request.state.user),
        error=_quota_message(request.state.user)))


@app.post("/request", dependencies=[Depends(csrf_protect)])
def do_request(request: Request, asin: str = Form(...), title: str = Form(...),
               author: str = Form(""), narrator: str = Form(""), cover: str = Form(""),
               year: str = Form(""), mode: str = Form("auto"), release_date: str = Form("")):
    user = request.state.user
    # Enforce the per-user open-request quota (admins exempt; 0 = unlimited). A book
    # already requested is an upsert (no new row), so it never trips the quota.
    if not db.get_item_by_source("user", asin) and _quota_message(user):
        return RedirectResponse("/discover?err=quota", status_code=303)
    release_date = (release_date or "").strip()[:10] or None
    upcoming = bool(release_date and release_date > db.today())
    # Interactive picking is only meaningful for users who can auto-download; a
    # standard user's request is held for approval regardless of the mode chosen.
    interactive = mode == "interactive" and auth.is_trusted(user)
    if not auth.is_trusted(user):
        status, mode = "awaiting_approval", "auto"
    elif upcoming:
        status, mode = "scheduled", "auto"   # not released yet — hold until the date
    elif interactive:
        status = "selecting"     # worker skips this; the user picks a source next
    else:
        status = "pending"
    item_id = db.upsert_item("user", asin, title=title, author=author, narrator=narrator,
                             cover=cover or None, status=status, mode=mode,
                             release_date=release_date, requested_by=user["username"])
    if status == "selecting":
        db.log_event(f"{user['username']} requested '{title}' (choosing a source)", item_id=item_id)
        return RedirectResponse(f"/request/{item_id}/candidates", status_code=303)
    if status == "scheduled":
        db.log_event(f"{user['username']} requested '{title}' — scheduled for {release_date}",
                     item_id=item_id)
        notify.event("request", "New request — scheduled",
                     f"{user['username']} requested '{title}'; it releases {release_date} "
                     "and will be searched for then.")
    elif status == "pending":
        worker.wake()
        db.log_event(f"{user['username']} requested '{title}'", item_id=item_id)
        notify.event("request", "New request", f"{user['username']} requested '{title}'")
    else:
        db.log_event(f"{user['username']} requested '{title}' (awaiting approval)", item_id=item_id)
        notify.event("request", "New request — needs approval",
                     f"{user['username']} requested '{title}' and it's awaiting your approval.")
    return RedirectResponse("/requests", status_code=303)


def _owned_item(request: Request, item_id: int) -> dict[str, Any]:
    """Fetch an item the current user is allowed to act on (owner or admin)."""
    user = request.state.user
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(404)
    if not auth.is_admin(user) and item.get("requested_by") != user["username"]:
        raise HTTPException(403, "Not your request")
    return item


@app.get("/request/{item_id}/candidates", response_class=HTMLResponse)
def request_candidates(request: Request, item_id: int):
    item = _owned_item(request, item_id)
    if not auth.is_trusted(request.state.user):
        raise HTTPException(403, "Interactive requests require a trusted account")
    siblings, booknum = worker.series_siblings(item)
    edition = {"narrator": item.get("narrator"), "year": (item.get("release_date") or "")[:4],
               "book_number": booknum}
    results = worker.manual_search(item["title"], item.get("author") or "", edition, siblings)
    token = uuid.uuid4().hex
    _SEARCH_CACHE.set(token, {"item": item_id, "results": results})
    return templates.TemplateResponse(request, "candidates.html", _ctx(
        request, item=item, results=results, token=token))


@app.post("/request/{item_id}/pick", dependencies=[Depends(csrf_protect)])
def request_pick(request: Request, item_id: int, token: str = Form(...), index: int = Form(...)):
    item = _owned_item(request, item_id)
    if not auth.is_trusted(request.state.user):
        raise HTTPException(403, "Interactive requests require a trusted account")
    cached = _SEARCH_CACHE.get(token)
    if not cached or cached.get("item") != item_id or index >= len(cached["results"]):
        return RedirectResponse(f"/request/{item_id}/candidates", status_code=303)
    chosen = cached["results"][index]
    worker.grab(item_id, chosen["username"], chosen["file_list"], chosen["directory"])
    db.log_event(f"{request.state.user['username']} picked a source for '{item['title']}' "
                 f"({chosen['username']}, score {chosen['score']})", item_id=item_id)
    return RedirectResponse("/requests", status_code=303)


@app.post("/request/{item_id}/mismatch", dependencies=[Depends(csrf_protect)])
def request_mismatch(request: Request, item_id: int):
    """User (or admin) flags a completed request as the wrong content: blocklist the
    source, remove the imported files, and retry with a different upload."""
    item = _owned_item(request, item_id)
    if item["status"] != "done":
        return RedirectResponse("/requests", status_code=303)
    worker.reject_mismatch_manual(item, request.state.user["username"])
    return RedirectResponse("/requests", status_code=303)


@app.post("/request/{item_id}/auto", dependencies=[Depends(csrf_protect)])
def request_auto(request: Request, item_id: int):
    """Abandon interactive selection and let the worker grab the best match."""
    item = _owned_item(request, item_id)
    if not auth.is_trusted(request.state.user):
        raise HTTPException(403)
    db.update_item(item_id, status="pending", mode="auto", attempts=0, error=None)
    worker.wake()
    db.log_event(f"{request.state.user['username']} switched '{item['title']}' to auto",
                 item_id=item_id)
    return RedirectResponse("/requests", status_code=303)


@app.get("/requests", response_class=HTMLResponse)
def my_requests(request: Request):
    user = request.state.user
    mine = db.list_items_by_user(user["username"])
    return templates.TemplateResponse(request, "requests.html", _ctx(request, items=mine))


@app.get("/tags", response_class=HTMLResponse)
def tags_page(request: Request):
    return templates.TemplateResponse(request, "tags.html", _ctx(
        request, writes=db.recent_tag_writes(100),
    ))


# library sort options: label -> (ABS sort key, descending?)
LIBRARY_SORTS = {
    "added": ("addedAt", True),
    "title": ("media.metadata.title", False),
    "author": ("media.metadata.authorNameLF", False),
    "released": ("media.metadata.publishedYear", True),
}


# -------------------------------------------------------------------- library
@app.get("/library", response_class=HTMLResponse)
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
    return templates.TemplateResponse(request, "library.html", _ctx(
        request, items=items, total=total, page=max(0, page), pages=pages, configured=configured,
        q=q, sort=sort, searching=searching, sorts=list(LIBRARY_SORTS.keys()),
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
_NTEST_MSGS = {
    "ok": ("ok", "Test notification sent."),
    "none": ("warn", "No Apprise URLs configured yet."),
    "bad": ("err", "Apprise rejected the URLs — check their format/credentials."),
}


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0, ntest: str = ""):
    grouped: dict[str, list[Any]] = {g: [] for g in settings.groups()}
    for f in settings.SPEC:
        secret = settings.is_secret(f.key)
        grouped[f.group].append({
            "field": f,
            "value": "" if secret else settings.get(f.key),
            "is_set": bool(settings.get(f.key)) if secret else False,
        })
    ntest_banner = None
    if ntest in _NTEST_MSGS:
        level, msg = _NTEST_MSGS[ntest]
        ntest_banner = {"level": level, "msg": msg}
    return templates.TemplateResponse(request, "settings.html", _ctx(
        request, grouped=grouped, saved=bool(saved), ntest_banner=ntest_banner,
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
            if f.key == "default_request_mode" and val not in ("auto", "interactive"):
                continue
            db.set_setting(f.key, val)
    worker.wake()
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.get("/api/status")
def api_status():
    return JSONResponse({
        "service": "soulscribe", "version": __version__,
        "worker": worker.STATUS, "counts": db.counts_by_status(),
    })


@app.get("/health")
def health():
    return {"status": "ok"}
