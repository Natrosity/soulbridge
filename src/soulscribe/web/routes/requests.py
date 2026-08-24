"""The request lifecycle — the app's core: discovery search + hero rows, creating
a request (auto / interactive / approval / scheduled), the interactive candidate
picker, a user's own request list, and the admin approval / blocklist / import
surface."""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import cache, db, settings
from ...clients import notify
from ...clients.abr import ABR
from ...clients.abs import ABS, library_key
from ...clients.audible import Audible, product_url
from ...core import auth, worker
from ..common import SEARCH_CACHE, csrf_protect, ctx, require_admin, templates

router = APIRouter()


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


# ------------------------------------------------------------- discovery aids
_LIB_INDEX = cache.TTLCache(300)                # "lib" -> (asins, keys), 5 min
_LIB_SAMPLE = cache.TTLCache(300)                # "sample" -> recent library items, 5 min
_BROWSE = cache.TTLCache(6 * 3600)              # Audible sort -> browse items, 6h
_OWNED_HEROES = cache.TTLCache(6 * 3600)        # "series"|"authors"|"similar" -> a hero row, 6h
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


def _library_sample(limit: int = 40) -> list[dict[str, Any]]:
    """A recent slice of the ABS library (title/author/series/asin per item) —
    seeds the 'owned-library' discovery hero rows. Cached like the library index."""
    url, key, lib = settings.get("abs_url"), settings.get("abs_api_key"), settings.get("abs_library_id")
    if not (url and key and lib):
        return []
    hit = _LIB_SAMPLE.get("sample")
    if hit is not None:
        return hit
    a = ABS(url, key)
    items, _total = a.library_items(lib, limit=limit, sort="addedAt", desc=True)
    a.close()
    _LIB_SAMPLE.set("sample", items)
    return items


def _not_owned(candidates: list[dict[str, Any]], asins: set, keys: set,
              seen_asins: set) -> list[dict[str, Any]]:
    """Filter Audible listings down to ones not already owned (by ASIN or
    title|author key) and not already picked by an earlier row in this batch."""
    out = []
    for b in candidates:
        asin = b.get("asin")
        if not asin or asin in asins or asin in seen_asins:
            continue
        k = library_key(b.get("title"), (b.get("authors") or [""])[0])
        if k in keys:
            continue
        seen_asins.add(asin)
        out.append(b)
    return out


def _series_completion_row(aud: Audible) -> dict[str, Any]:
    """'Complete the series' — for a few series you own at least one book of,
    find what you're missing via Audible's own series graph (InTheSameSeries)."""
    by_series: dict[str, dict[str, Any]] = {}
    for it in _library_sample():
        s = (it.get("series") or "").strip()
        if s and it.get("asin") and s not in by_series:
            by_series[s] = it            # first hit = most recently added in that series
    asins, keys = _library_index()
    missing: list[dict[str, Any]] = []
    seen: set = set()
    for series_name, rep in list(by_series.items())[:3]:      # cap Audible calls
        sibs = aud.similar(rep["asin"], "InTheSameSeries", num=12)
        for b in sibs:
            b["series"] = b.get("series") or series_name
        missing += _not_owned(sibs, asins, keys, seen)
        if len(missing) >= 18:
            break
    if not missing:
        return {}
    _mark_results(missing)
    return {"label": "Complete the series", "books": missing[:18]}


def _author_hero_row(aud: Audible) -> dict[str, Any]:
    """'More from authors you own' — search Audible for a few authors already
    in your library, skipping what you already own or have requested."""
    authors: list[str] = []
    seen_authors: set = set()
    for it in _library_sample():
        a = (it.get("author") or "").strip()
        if a and a.lower() not in seen_authors:
            seen_authors.add(a.lower())
            authors.append(a)
        if len(authors) >= 3:
            break
    asins, keys = _library_index()
    picks: list[dict[str, Any]] = []
    seen: set = set()
    for author in authors:
        picks += _not_owned(aud.search(author, num=10), asins, keys, seen)
        if len(picks) >= 18:
            break
    if not picks:
        return {}
    _mark_results(picks)
    return {"label": "More from authors you own", "books": picks[:18]}


def _similar_hero_row(aud: Audible) -> dict[str, Any]:
    """'Because you have X' — Audible's raw-similarity graph seeded from one
    recently-added library book."""
    rep = next((it for it in _library_sample() if it.get("asin")), None)
    if not rep:
        return {}
    asins, keys = _library_index()
    picks = _not_owned(aud.similar(rep["asin"], "RawSimilarities", num=12), asins, keys, set())
    if not picks:
        return {}
    _mark_results(picks)
    return {"label": f"Because you have {rep['title']}", "books": picks[:18]}


def _owned_hero_rows() -> list[dict[str, Any]]:
    """Hero rows derived from the ABS library: series completion, more from
    known authors, and Audible's similarity graph. Cached 6h like the generic
    Bestsellers/Releasing Soon rows; an empty result is cached too, so a
    library with nothing to surface doesn't re-query Audible every page load."""
    url, key, lib = settings.get("abs_url"), settings.get("abs_api_key"), settings.get("abs_library_id")
    if not (url and key and lib):
        return []
    builders = (("series", _series_completion_row), ("authors", _author_hero_row),
               ("similar", _similar_hero_row))
    to_build = [(k, fn) for k, fn in builders if _OWNED_HEROES.get(k) is None]
    if to_build:
        region = settings.get("audible_region") or "us"
        aud = Audible(region)
        try:
            for k, fn in to_build:
                _OWNED_HEROES.set(k, fn(aud))
        finally:
            aud.close()
    return [row for k, _ in builders if (row := _OWNED_HEROES.get(k))]


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
    """Curated Audible rows for the discovery page (cached 6h; status marks
    refreshed per request). Personalised rows from the ABS library come first
    (series completion, familiar authors, similar titles), then the generic
    Bestsellers (already-released titles) / Releasing Soon (not-yet-out) rows."""
    rows: list[dict[str, Any]] = _owned_hero_rows()
    region = settings.get("audible_region") or "us"
    today = db.today()
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


@router.get("/discover", response_class=HTMLResponse)
def discover_page(request: Request, q: str = "", err: str = ""):
    return templates.TemplateResponse(request, "discover.html", ctx(
        request, q=q, results=None, heroes=_hero_rows(), default_mode=_default_mode(),
        can_interactive=auth.is_trusted(request.state.user),
        error=(_quota_message(request.state.user) if err == "quota" else None)))


@router.post("/discover", response_class=HTMLResponse, dependencies=[Depends(csrf_protect)])
def discover_search(request: Request, q: str = Form(...)):
    aud = Audible(settings.get("audible_region") or "us")
    results = aud.search(q)
    aud.close()
    _mark_results(results)
    return templates.TemplateResponse(request, "discover.html", ctx(
        request, q=q, results=results, heroes=None, default_mode=_default_mode(),
        can_interactive=auth.is_trusted(request.state.user),
        error=_quota_message(request.state.user)))


@router.post("/request", dependencies=[Depends(csrf_protect)])
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


@router.get("/request/{item_id}/candidates", response_class=HTMLResponse)
def request_candidates(request: Request, item_id: int):
    item = _owned_item(request, item_id)
    if not auth.is_trusted(request.state.user):
        raise HTTPException(403, "Interactive requests require a trusted account")
    siblings, booknum = worker.series_siblings(item)
    edition = {"narrator": item.get("narrator"), "year": (item.get("release_date") or "")[:4],
               "book_number": booknum}
    results = worker.manual_search(item["title"], item.get("author") or "", edition, siblings)
    token = uuid.uuid4().hex
    SEARCH_CACHE.set(token, {"item": item_id, "results": results})
    return templates.TemplateResponse(request, "candidates.html", ctx(
        request, item=item, results=results, token=token))


@router.post("/request/{item_id}/pick", dependencies=[Depends(csrf_protect)])
def request_pick(request: Request, item_id: int, token: str = Form(...), index: int = Form(...)):
    item = _owned_item(request, item_id)
    if not auth.is_trusted(request.state.user):
        raise HTTPException(403, "Interactive requests require a trusted account")
    cached = SEARCH_CACHE.get(token)
    if not cached or cached.get("item") != item_id or index >= len(cached["results"]):
        return RedirectResponse(f"/request/{item_id}/candidates", status_code=303)
    chosen = cached["results"][index]
    worker.grab(item_id, chosen["username"], chosen["file_list"], chosen["directory"])
    db.log_event(f"{request.state.user['username']} picked a source for '{item['title']}' "
                 f"({chosen['username']}, score {chosen['score']})", item_id=item_id)
    return RedirectResponse("/requests", status_code=303)


@router.post("/request/{item_id}/mismatch", dependencies=[Depends(csrf_protect)])
def request_mismatch(request: Request, item_id: int):
    """User (or admin) flags a completed request as the wrong content: blocklist the
    source, remove the imported files, and retry with a different upload."""
    item = _owned_item(request, item_id)
    if item["status"] != "done":
        return RedirectResponse("/requests", status_code=303)
    worker.reject_mismatch_manual(item, request.state.user["username"])
    return RedirectResponse("/requests", status_code=303)


@router.post("/request/{item_id}/auto", dependencies=[Depends(csrf_protect)])
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


@router.get("/requests", response_class=HTMLResponse)
def my_requests(request: Request):
    user = request.state.user
    mine = db.list_items_by_user(user["username"])
    return templates.TemplateResponse(request, "requests.html", ctx(request, items=mine))


# ------------------------------------------------------- admin request surface
_IMPORT_MSGS = {
    "none": ("warn", "AudioBookRequest isn't configured, so there's nothing to import."),
    "error": ("err", "Import failed — check the AudioBookRequest connection."),
    "0": ("ok", "Nothing new to import — Soulscribe is already up to date."),
}


def _abr_book(r: dict[str, Any]) -> dict[str, Any]:
    return r.get("book") or r


@router.get("/requests/all", response_class=HTMLResponse)
def all_requests(request: Request, imported: str = ""):
    require_admin(request)
    awaiting = db.list_items(statuses=["awaiting_approval"])
    others = [it for it in db.list_items(limit=300) if it["status"] != "awaiting_approval"]
    banner = None
    if imported:
        level, msg = _IMPORT_MSGS.get(
            imported, ("ok", f"Imported {imported} request(s) from AudioBookRequest."))
        banner = {"level": level, "msg": msg}
    return templates.TemplateResponse(request, "all_requests.html", ctx(
        request, awaiting=awaiting, items=others, banner=banner, blocks=db.list_blocks(),
        abr_configured=bool(settings.get("abr_url") and settings.get("abr_api_key"))))


@router.post("/items/{item_id}/approve", dependencies=[Depends(csrf_protect)])
def item_approve(request: Request, item_id: int):
    admin = require_admin(request)
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


@router.post("/items/{item_id}/deny", dependencies=[Depends(csrf_protect)])
def item_deny(request: Request, item_id: int):
    admin = require_admin(request)
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(404)
    if item["status"] != "awaiting_approval":
        return RedirectResponse("/requests/all", status_code=303)
    db.update_item(item_id, status="denied", error=None)
    db.log_event(f"{admin['username']} declined '{item['title']}'"
                 f" (requested by {item.get('requested_by') or 'unknown'})", "warn", item_id)
    return RedirectResponse("/requests/all", status_code=303)


@router.post("/blocklist/{block_id}/remove", dependencies=[Depends(csrf_protect)])
def blocklist_remove(request: Request, block_id: int):
    require_admin(request)
    db.remove_block(block_id)
    return RedirectResponse("/requests/all", status_code=303)


@router.post("/requests/import-abr", dependencies=[Depends(csrf_protect)])
def import_abr(request: Request):
    """One-off cutover aid: pull existing AudioBookRequest history into Soulscribe so
    past requests survive after ABR is retired. New books only (upsert dedupes);
    already-fulfilled ABR requests come in as 'done', the rest as 'pending'."""
    require_admin(request)
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
