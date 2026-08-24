"""Admin user management: create, set role, reset password, delete."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import db
from ...core import auth
from ..common import csrf_protect, ctx, password_problem, require_admin, templates

router = APIRouter()


def _admin_count() -> int:
    return sum(1 for u in db.list_users() if u["role"] == "admin")


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    require_admin(request)
    return templates.TemplateResponse(request, "users.html", ctx(
        request, users=db.list_users(), roles=auth.ROLES, error=None))


@router.post("/users", dependencies=[Depends(csrf_protect)])
def users_create(request: Request, username: str = Form(...), password: str = Form(...),
                 role: str = Form("standard")):
    require_admin(request)
    role = role if role in auth.ROLES else "standard"
    err = password_problem(username, password, password)
    if err:
        return templates.TemplateResponse(request, "users.html", ctx(
            request, users=db.list_users(), roles=auth.ROLES, error=err))
    db.create_user(username.strip(), auth.hash_pw(password), role=role)
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id}/role", dependencies=[Depends(csrf_protect)])
def users_set_role(request: Request, user_id: int, role: str = Form(...)):
    require_admin(request)
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


@router.post("/users/{user_id}/password", dependencies=[Depends(csrf_protect)])
def users_reset_pw(request: Request, user_id: int, password: str = Form(...)):
    require_admin(request)
    if not db.get_user(user_id):
        raise HTTPException(404)
    if len(password) < 10:
        raise HTTPException(400, "password too short")
    db.update_user(user_id, password_hash=auth.hash_pw(password),
                   failed_attempts=0, locked_until=None)
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id}/delete", dependencies=[Depends(csrf_protect)])
def users_delete(request: Request, user_id: int):
    admin = require_admin(request)
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404)
    if target["id"] == admin["id"]:
        raise HTTPException(400, "cannot delete yourself")
    if target["role"] == "admin" and _admin_count() <= 1:
        raise HTTPException(400, "cannot delete the last admin")
    db.delete_user(user_id)
    return RedirectResponse("/users", status_code=303)
