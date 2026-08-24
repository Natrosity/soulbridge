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


# --------------------------------------------------------- Phase 7: matching UI
def test_settings_page_renders_matching_group_with_presets(app_ctx):
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    r = app_ctx.get("/settings")
    assert r.status_code == 200
    assert "Matching" in r.text
    assert 'id="weight_coverage_floor"' in r.text
    assert 'id="keyword_spam"' in r.text
    assert 'id="matching-presets"' in r.text
    for name in ("balanced", "single_m4b", "bitrate", "lenient"):
        assert f'data-preset="{name}"' in r.text
    block = r.text[r.text.index('id="weight_coverage_floor"'):][:300]
    assert 'min="0.2"' in block and 'max="0.9"' in block and 'step="0.05"' in block


def test_settings_save_clamps_out_of_range_weight(app_ctx):
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    from soulscribe import settings
    csrf = _csrf(app_ctx, "/settings")
    app_ctx.post("/settings", data={"weight_coverage_floor": "5.0", "instance_name": "T",
                                    "csrf": csrf}, follow_redirects=False)
    assert settings.get_float("weight_coverage_floor") == 0.9      # clamped to max_val

    csrf2 = _csrf(app_ctx, "/settings")
    app_ctx.post("/settings", data={"weight_coverage_floor": "-9", "instance_name": "T",
                                    "csrf": csrf2}, follow_redirects=False)
    assert settings.get_float("weight_coverage_floor") == 0.2      # clamped to min_val


def test_settings_save_in_range_weight_passes_through(app_ctx):
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    from soulscribe import settings
    csrf = _csrf(app_ctx, "/settings")
    app_ctx.post("/settings", data={"weight_coverage_floor": "0.5", "instance_name": "T",
                                    "csrf": csrf}, follow_redirects=False)
    assert settings.get_float("weight_coverage_floor") == 0.5


def test_settings_save_whole_number_weight_stays_clean(app_ctx):
    # clamping must not turn "25" into "25.0" — that value round-trips into the
    # number input's value= attribute and is visible in the UI every save.
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    from soulscribe import settings
    csrf = _csrf(app_ctx, "/settings")
    app_ctx.post("/settings", data={"weight_music_penalty": "25", "instance_name": "T",
                                    "csrf": csrf}, follow_redirects=False)
    assert settings.get("weight_music_penalty") == "25"
    # a genuinely fractional value keeps its decimal
    csrf2 = _csrf(app_ctx, "/settings")
    app_ctx.post("/settings", data={"weight_coverage_floor": "0.45", "instance_name": "T",
                                    "csrf": csrf2}, follow_redirects=False)
    assert settings.get("weight_coverage_floor") == "0.45"


def test_search_page_renders_score_breakdown(app_ctx, monkeypatch):
    from soulscribe.core import worker
    fake = [
        {"username": "good", "directory": "x", "label": "Dark Matter", "free_slot": True,
         "size_mb": 320, "files": 1, "exts": ["m4b"], "bitrate": 256, "score": 118.5,
         "acceptable": True, "file_list": [], "breakdown": [
             {"label": "Title match (100%)", "points": 100.0},
             {"label": "Preferred format", "points": 8.0},
         ]},
        {"username": "bad", "directory": "y", "label": "Dark Matter (Remix)", "free_slot": False,
         "size_mb": 6, "files": 1, "exts": ["mp3"], "bitrate": None, "score": -1.0,
         "acceptable": False, "file_list": [], "breakdown": [
             {"label": "Rejected: looks like music, not an audiobook", "points": None},
         ]},
    ]
    monkeypatch.setattr(worker, "manual_search", lambda *a, **k: fake)
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    csrf = _csrf(app_ctx, "/search")
    r = app_ctx.post("/search", data={"title": "Dark Matter", "author": "", "csrf": csrf})
    assert r.status_code == 200
    assert "Title match (100%)" in r.text
    assert "+100.0" in r.text and "+8.0" in r.text
    assert "Rejected: looks like music, not an audiobook" in r.text
    assert '<details class="breakdown">' in r.text


def test_settings_save_bad_number_is_ignored(app_ctx):
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    from soulscribe import settings
    settings.db.set_setting("weight_coverage_floor", "0.55")
    csrf = _csrf(app_ctx, "/settings")
    app_ctx.post("/settings", data={"weight_coverage_floor": "not-a-number",
                                    "instance_name": "T", "csrf": csrf}, follow_redirects=False)
    assert settings.get_float("weight_coverage_floor") == 0.55     # untouched, not zeroed


# --------------------------------------------------------- Phase 8: follows
def _make_standard(client, admin_user, admin_pass, username="reader", password="readerpass1"):
    """Create a standard user via the admin API (must already be logged in as admin)."""
    csrf = _csrf(client, "/users")
    client.post("/users", data={"username": username, "password": password,
                                "role": "standard", "csrf": csrf}, follow_redirects=False)
    return username, password


def test_follows_page_available_to_standard_user(app_ctx):
    au, ap = _make_admin(app_ctx)
    _login(app_ctx, au, ap)
    su, sp = _make_standard(app_ctx, au, ap)
    app_ctx.post("/logout", data={"csrf": _csrf(app_ctx, "/account")}, follow_redirects=False)
    _login(app_ctx, su, sp)
    r = app_ctx.get("/follows")
    assert r.status_code == 200
    assert "You're not following anyone yet" in r.text


def test_create_and_list_follow(app_ctx):
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    csrf = _csrf(app_ctx, "/follows")
    r = app_ctx.post("/follows", data={"kind": "author", "name": "Brandon Sanderson",
                                       "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    r = app_ctx.get("/follows")
    assert "Brandon Sanderson" in r.text
    assert "Author" in r.text


def test_series_follow_requires_ref_asin(app_ctx):
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    csrf = _csrf(app_ctx, "/follows")
    r = app_ctx.post("/follows", data={"kind": "series", "name": "Mistborn", "csrf": csrf})
    assert r.status_code == 400


def test_follow_search_marks_already_followed(app_ctx, monkeypatch):
    from soulscribe.clients.audible import Audible
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    csrf = _csrf(app_ctx, "/follows")
    app_ctx.post("/follows", data={"kind": "author", "name": "Brandon Sanderson", "csrf": csrf})

    monkeypatch.setattr(Audible, "search", lambda self, *a, **k: [
        {"asin": "X1", "title": "Mistborn", "subtitle": None,
         "authors": ["Brandon Sanderson"], "narrators": [], "series": None,
         "cover": None, "year": None, "release_date": None, "runtime_min": None},
    ])
    r = app_ctx.post("/follows/search", data={"q": "mistborn", "csrf": csrf})
    assert r.status_code == 200
    assert "Following Brandon Sanderson" in r.text
    assert "Follow Brandon Sanderson" not in r.text


def test_unfollow_by_owner(app_ctx):
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    csrf = _csrf(app_ctx, "/follows")
    app_ctx.post("/follows", data={"kind": "author", "name": "X", "csrf": csrf})
    from soulscribe import db
    fid = db.list_follows(u)[0]["id"]
    r = app_ctx.post(f"/follows/{fid}/delete", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert db.get_follow(fid) is None


def test_cannot_unfollow_someone_elses_follow(app_ctx):
    au, ap = _make_admin(app_ctx)
    _login(app_ctx, au, ap)
    csrf = _csrf(app_ctx, "/follows")
    app_ctx.post("/follows", data={"kind": "author", "name": "X", "csrf": csrf})
    from soulscribe import db
    fid = db.list_follows(au)[0]["id"]

    su, sp = _make_standard(app_ctx, au, ap)
    app_ctx.post("/logout", data={"csrf": _csrf(app_ctx, "/account")}, follow_redirects=False)
    _login(app_ctx, su, sp)
    csrf2 = _csrf(app_ctx, "/follows")
    r = app_ctx.post(f"/follows/{fid}/delete", data={"csrf": csrf2})
    assert r.status_code == 403
    assert db.get_follow(fid) is not None            # untouched


def test_admin_can_unfollow_anyones_follow(app_ctx):
    au, ap = _make_admin(app_ctx)
    _login(app_ctx, au, ap)
    su, sp = _make_standard(app_ctx, au, ap)
    app_ctx.post("/logout", data={"csrf": _csrf(app_ctx, "/account")}, follow_redirects=False)
    _login(app_ctx, su, sp)
    csrf = _csrf(app_ctx, "/follows")
    app_ctx.post("/follows", data={"kind": "author", "name": "X", "csrf": csrf})
    from soulscribe import db
    fid = db.list_follows(su)[0]["id"]
    app_ctx.post("/logout", data={"csrf": _csrf(app_ctx, "/account")}, follow_redirects=False)

    _login(app_ctx, au, ap)
    csrf2 = _csrf(app_ctx, "/follows")
    r = app_ctx.post(f"/follows/{fid}/delete", data={"csrf": csrf2}, follow_redirects=False)
    assert r.status_code == 303
    assert db.get_follow(fid) is None


def test_follow_csrf_required(app_ctx):
    u, p = _make_admin(app_ctx)
    _login(app_ctx, u, p)
    r = app_ctx.post("/follows", data={"kind": "author", "name": "X"})
    assert r.status_code == 403
