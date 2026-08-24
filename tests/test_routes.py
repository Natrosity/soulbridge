"""End-to-end route/auth characterisation tests. These pin the app's routing and
access-control behaviour so the router split (and future route changes) can be
made with confidence. Runnable with pytest.

The worker thread is disabled and the Audible client is stubbed, so nothing here
touches the network or a real /config.
"""
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture()
def app_ctx(monkeypatch):
    """A TestClient bound to a throwaway DB, worker off, Audible stubbed offline."""
    from starlette.testclient import TestClient

    from soulscribe import db
    from soulscribe.core import worker
    from soulscribe.clients.audible import Audible

    d = tempfile.mkdtemp()
    monkeypatch.setattr(db, "CONFIG_DIR", d)
    monkeypatch.setattr(db, "DB_PATH", os.path.join(d, "test.db"))
    monkeypatch.setitem(worker.STATUS, "running", True)     # skip worker.start()
    monkeypatch.setattr(Audible, "browse", lambda self, *a, **k: [])
    monkeypatch.setattr(Audible, "search", lambda self, *a, **k: [])

    from soulscribe.web.server import app
    # Browsers send an HTML Accept header; the Guard uses it to choose a redirect
    # (303) over a bare 401/403, so mimic a browser for realistic routing.
    with TestClient(app, headers={"accept": "text/html"}) as client:
        yield client


def _csrf(client, path="/login"):
    m = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)
    return m.group(1) if m else ""


def _make_admin(client, username="admin", password="adminpassword"):
    csrf = _csrf(client, "/setup")
    r = client.post("/setup", data={"username": username, "password": password,
                                    "confirm": password, "csrf": csrf},
                    follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    return username, password


def _login(client, username, password):
    csrf = _csrf(client, "/login")
    r = client.post("/login", data={"username": username, "password": password,
                                    "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303


# ---------------------------------------------------------------- public routes
def test_health_is_public(app_ctx):
    r = app_ctx.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_first_run_forces_setup(app_ctx):
    # no users yet -> any protected route bounces to /setup
    r = app_ctx.get("/discover", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"


# ---------------------------------------------------------------- admin journey
def test_admin_can_reach_every_surface(app_ctx):
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    for path in ["/", "/discover", "/requests", "/library", "/tags", "/settings",
                 "/users", "/account", "/requests/all", "/search", "/api/status",
                 "/partials/dashboard"]:
        r = app_ctx.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"


def test_unauthenticated_is_bounced(app_ctx):
    _make_admin(app_ctx)                       # users now exist
    fresh = app_ctx                            # same client, but not logged in yet
    fresh.cookies.clear()
    r = fresh.get("/discover", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


# ------------------------------------------------------------- access control
def test_standard_user_is_denied_admin_surface(app_ctx):
    au, ap = _make_admin(app_ctx)
    _login(app_ctx, au, ap)
    # create a standard user via the admin API
    csrf = _csrf(app_ctx, "/users")
    app_ctx.post("/users", data={"username": "reader", "password": "readerpass1",
                                 "role": "standard", "csrf": csrf},
                 follow_redirects=False)
    # log in as that standard user
    app_ctx.post("/logout", data={"csrf": _csrf(app_ctx, "/account")},
                 follow_redirects=False)
    _login(app_ctx, "reader", "readerpass1")
    # admin GET surfaces are denied — either a middleware redirect (303 to
    # /discover) or a route-level 403, depending on how each is gated today.
    for path in ["/settings", "/users", "/", "/tags", "/requests/all"]:
        r = app_ctx.get(path, follow_redirects=False)
        assert r.status_code in (303, 403), f"{path} not denied, got {r.status_code}"
    # a normal user surface is still fine
    assert app_ctx.get("/discover").status_code == 200


def test_csrf_required_on_post(app_ctx):
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    # missing/invalid token -> 403
    r = app_ctx.post("/settings", data={"instance_name": "x"}, follow_redirects=False)
    assert r.status_code == 403
    # valid token -> accepted (303 redirect back)
    csrf = _csrf(app_ctx, "/settings")
    r = app_ctx.post("/settings", data={"instance_name": "Scribe Test", "csrf": csrf},
                     follow_redirects=False)
    assert r.status_code == 303


def test_settings_save_persists(app_ctx):
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    csrf = _csrf(app_ctx, "/settings")
    app_ctx.post("/settings", data={"instance_name": "My Scribe", "csrf": csrf},
                 follow_redirects=False)
    from soulscribe import settings
    assert settings.get("instance_name") == "My Scribe"
