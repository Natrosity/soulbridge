"""Identity routes: first-run setup, login/logout, per-user account settings, and
the Overseerr-style 'Sign in with Plex' PIN OAuth flow."""
from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import __version__, cache, db, settings
from ...clients import plextv
from ...clients.plex import Plex
from ...core import auth
from ..common import csrf_protect, ctx, password_problem, templates

router = APIRouter()

_PLEX_PENDING = cache.TTLCache(900)             # oauth state -> {pin_id}

_PLEX_LOGIN_ERRORS = {
    "plex": "Plex sign-in failed. Please try again.",
    "plex_denied": "That Plex account doesn't have access to this server.",
}


# ----------------------------------------------------------------- setup / login
@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if db.user_count() > 0:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", ctx(request, error=None))


@router.post("/setup", dependencies=[Depends(csrf_protect)])
def setup_submit(request: Request, username: str = Form(...), password: str = Form(...),
                 confirm: str = Form(...)):
    if db.user_count() > 0:
        return RedirectResponse("/login", status_code=303)
    err = password_problem(username, password, confirm)
    if err:
        return templates.TemplateResponse(request, "setup.html", ctx(request, error=err))
    uid = db.create_user(username.strip(), auth.hash_pw(password), role="admin")
    auth.login_session(request, db.get_user(uid))
    db.log_event(f"Admin account '{username.strip()}' created")
    return RedirectResponse("/", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    if db.user_count() == 0:
        return RedirectResponse("/setup", status_code=303)
    if auth.current_user(request):
        return RedirectResponse("/", status_code=303)
    auth.csrf_token(request)                       # ensure token exists for the form
    return templates.TemplateResponse(request, "login.html", ctx(
        request, error=_PLEX_LOGIN_ERRORS.get(error), plex_login=_plex_enabled()))


@router.post("/login", dependencies=[Depends(csrf_protect)])
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_name(username.strip())
    fail = templates.TemplateResponse(
        request, "login.html", ctx(request, error="Invalid username or password.",
                                   plex_login=_plex_enabled()))
    if not user:
        auth.waste_time(password)          # equalise timing so usernames can't be enumerated
        return fail
    if auth.is_locked(user):
        return templates.TemplateResponse(request, "login.html", ctx(
            request, error="Account temporarily locked. Try again later.",
            plex_login=_plex_enabled()))
    if not auth.verify_pw(user.get("password_hash"), password):
        auth.register_failure(user)
        return fail
    auth.register_success(user)
    auth.login_session(request, user)
    return RedirectResponse("/", status_code=303)


@router.post("/logout", dependencies=[Depends(csrf_protect)])
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


@router.get("/auth/plex/start")
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


@router.get("/auth/plex/callback")
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


# -------------------------------------------------------------------- account
@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request, saved: int = 0):
    user = request.state.user
    return templates.TemplateResponse(request, "account.html", ctx(
        request, saved=bool(saved), error=None, has_password=bool(user.get("password_hash"))))


@router.post("/account", dependencies=[Depends(csrf_protect)])
def account_submit(request: Request, current: str = Form(""), password: str = Form(""),
                   confirm: str = Form("")):
    user = request.state.user
    has_pw = bool(user.get("password_hash"))

    def _err(msg: str):
        return templates.TemplateResponse(request, "account.html",
                                          ctx(request, error=msg, has_password=has_pw))

    if password:
        # A Plex-provisioned account has no password yet, so there's nothing to verify —
        # let them set an initial one. Accounts that already have a password must confirm it.
        if has_pw and not auth.verify_pw(user.get("password_hash"), current):
            return _err("Current password is incorrect.")
        err = password_problem(user["username"], password, confirm, check_user=False)
        if err:
            return _err(err)
        db.update_user(user["id"], password_hash=auth.hash_pw(password))
    return RedirectResponse("/account?saved=1", status_code=303)
