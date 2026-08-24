"""FastAPI composition root: security middleware, app wiring, and the small
public/system endpoints. All feature routes live in `web/routes/*` and are
included here; shared web primitives live in `web/common.py`.

Security model (Phase 1):
- Every route requires a logged-in user except the public allowlist
  (login, setup, health, static). Enforced fail-closed in middleware.
- Admin-only surface (operational + settings + user management) is gated by
  path in the same middleware.
- Signed session cookies, argon2 passwords, per-session CSRF on unsafe methods,
  login lockout, and security headers incl. a strict CSP (no inline JS).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .. import __version__, db, settings
from ..env import env
from ..core import auth, worker
from .routes import auth as auth_routes
from .routes import dashboard, library, manual, requests, settings_routes, users

HERE = Path(__file__).parent

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

# Feature routes.
app.include_router(auth_routes.router)
app.include_router(users.router)
app.include_router(dashboard.router)
app.include_router(manual.router)
app.include_router(requests.router)
app.include_router(library.router)
app.include_router(settings_routes.router)


@app.on_event("startup")
def _startup() -> None:
    db.init()
    settings.seed_from_env()
    if not worker.STATUS.get("running"):
        worker.start()


@app.get("/api/status")
def api_status():
    return JSONResponse({
        "service": "soulscribe", "version": __version__,
        "worker": worker.STATUS, "counts": db.counts_by_status(),
    })


@app.get("/health")
def health():
    return {"status": "ok"}
