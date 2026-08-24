"""Admin dashboard: the live operational overview and its polled partial."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ... import db
from ..common import ctx, templates

router = APIRouter()


def _dashboard_ctx(request: Request) -> dict[str, Any]:
    return ctx(
        request,
        active=db.list_items(statuses=["scheduled", "selecting", "pending", "searching",
                                       "downloading", "importing"]),
        attention=db.list_items(statuses=["no_results", "failed"]),
        done=db.list_items(statuses=["done"], limit=30),
        counts=db.counts_by_status(), events=db.recent_events(50),
    )


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", _dashboard_ctx(request))


@router.get("/partials/dashboard", response_class=HTMLResponse)
def dashboard_partial(request: Request):
    return templates.TemplateResponse(request, "_dashboard_body.html", _dashboard_ctx(request))
