"""Admin Server Settings: the settings form, saving it, and the notification
test button. (Module named settings_routes to avoid clashing with the app's
top-level settings module.)"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import db, settings
from ...clients import notify
from ...core import matching, worker
from ..common import csrf_protect, ctx, require_admin, templates

router = APIRouter()

_NTEST_MSGS = {
    "ok": ("ok", "Test notification sent."),
    "none": ("warn", "No Apprise URLs configured yet."),
    "bad": ("err", "Apprise rejected the URLs — check their format/credentials."),
}

# Full weight sets for the settings-page preset buttons (client-side fill only —
# presets aren't stored; the operator reviews and Saves).
_MATCHING_PRESETS = {
    name: {"label": matching.PRESET_LABELS[name], "weights": {**matching.DEFAULT_WEIGHTS, **overrides}}
    for name, overrides in matching.PRESETS.items()
}


@router.get("/settings", response_class=HTMLResponse)
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
    return templates.TemplateResponse(request, "settings.html", ctx(
        request, grouped=grouped, saved=bool(saved), ntest_banner=ntest_banner,
        matching_presets=_MATCHING_PRESETS,
    ))


@router.post("/settings", dependencies=[Depends(csrf_protect)])
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
            if f.type == "number" and (f.min_val is not None or f.max_val is not None):
                try:
                    num = float(val)
                except ValueError:
                    continue                        # not a number — leave the stored value alone
                if f.min_val is not None:
                    num = max(num, f.min_val)
                if f.max_val is not None:
                    num = min(num, f.max_val)
                # str(25.0) -> "25.0"; keep whole numbers clean ("25") and only
                # keep the decimal point for genuinely fractional values.
                val = str(int(num)) if num == int(num) else str(num)
            db.set_setting(f.key, val)
    worker.wake()
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/notify/test", dependencies=[Depends(csrf_protect)])
def notify_test(request: Request):
    require_admin(request)
    ok, _msg = notify.test()
    code = "ok" if ok else ("none" if not notify.urls() else "bad")
    return RedirectResponse(f"/settings?ntest={code}", status_code=303)
