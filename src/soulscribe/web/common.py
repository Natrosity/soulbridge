"""Shared web primitives used across the route modules: templates, the template
context builder, CSRF + admin guards, status vocab, and the cross-route search
result cache. Kept dependency-light so every router can import it freely."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from .. import __version__, cache, db, settings
from ..core import auth, worker

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

# token -> a manual/interactive search result set. Shared by the manual-search
# tools and the interactive request picker, so it lives here.
SEARCH_CACHE = cache.TTLCache(1800)

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


def require_admin(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not auth.is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def ctx(request: Request, **extra: Any) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    data = {
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
    data.update(extra)
    return data


def password_problem(username: str, password: str, confirm: str,
                     check_user: bool = True) -> Optional[str]:
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
