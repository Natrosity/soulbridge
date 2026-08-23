"""Background worker: discover requests, search Soulseek, download the best
match, and organise it into the library. Runs on a single daemon thread; the
web layer reads DB state and can trigger manual actions."""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from typing import Any, Optional

from .. import db, settings
from ..clients import audnexus
from ..clients.abr import ABR
from ..clients.abs import ABS
from ..clients.audnexus import Audnexus
from ..clients.jellyfin import Jellyfin
from ..clients.plex import Plex
from ..clients.slskd import Slskd
from . import matching, tagging

STATUS: dict[str, Any] = {
    "running": False,
    "last_poll": None,
    "last_error": None,
    "slskd_connected": False,
    "abr_connected": False,
    "abs_connected": None,        # None = not configured; True/False = reachable
    "plex_connected": None,
    "jellyfin_connected": None,
}

_stop = threading.Event()
_wake = threading.Event()
_lock = threading.Lock()   # serialise slskd-mutating actions (worker vs web)


# ---------- helpers ----------
def _slskd() -> Slskd:
    return Slskd(settings.get("slskd_url"), settings.get("slskd_api_key"))


def _sanitize(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "").strip().rstrip(".")
    return re.sub(r"\s+", " ", name) or "Unknown"


def dest_folder(title: str, author: str, narrator: str) -> str:
    tmpl = settings.get("folder_template") or "{author}/{title}"
    parts = tmpl.split("/")
    rendered = [
        _sanitize(
            p.replace("{author}", author or "Unknown Author")
             .replace("{title}", title or "Unknown Title")
             .replace("{narrator}", narrator or "")
        )
        for p in parts
    ]
    return os.path.join(settings.get("library_path"), *rendered)


def _find_local(basename: str, root: str) -> Optional[str]:
    """Locate a completed file by basename under the slskd downloads path."""
    for dirpath, _dirs, files in os.walk(root):
        if basename in files:
            return os.path.join(dirpath, basename)
    return None


# ---------- public actions ----------
def manual_search(title: str, author: str = "") -> list[dict[str, Any]]:
    """Search Soulseek and return ranked candidate groups (for the web UI)."""
    sk = _slskd()
    try:
        prefs = matching.default_prefs(settings)
        responses: list[dict[str, Any]] = []
        for q in matching.build_queries(title, author):
            responses = sk.search(q, wait=35, floor=20)
            groups = matching.group_responses(responses)
            if any(matching.score_group(g, title, author, prefs) > 0 for g in groups):
                break
        groups = matching.group_responses(responses)
        for g in groups:
            g.score = matching.score_group(g, title, author, prefs)
        ranked = sorted(groups, key=lambda g: g.score, reverse=True)
        return [
            {
                "username": g.username, "directory": g.directory, "label": g.label,
                "free_slot": g.free_slot, "size_mb": round(g.total_size / 1_048_576),
                "files": len(g.files), "exts": sorted(e[1:] for e in g.exts),
                "score": round(g.score, 1), "acceptable": g.score > 0,
                "file_list": g.files,
            }
            for g in ranked[:25]
        ]
    finally:
        sk.close()


def grab(item_id: int, username: str, files: list[dict[str, Any]], directory: str) -> None:
    """Enqueue a chosen group in slskd and mark the item downloading."""
    sk = _slskd()
    try:
        with _lock:
            sk.enqueue(username, files)
        db.update_item(
            item_id, status="downloading", slskd_username=username,
            slskd_dir=directory, chosen_files=json.dumps([f["filename"] for f in files]),
            size=sum(int(f.get("size", 0)) for f in files), error=None,
        )
        db.log_event(f"Downloading {len(files)} file(s) from {username}", item_id=item_id)
    finally:
        sk.close()


MAX_ATTEMPTS = 6  # retries (across ticks) before an item is parked as no_results/failed


def _park_or_retry(item_id: int, attempts: int, title: str, kind: str, err: str = "") -> None:
    """A search/grab attempt didn't succeed. Retry on later ticks until the cap,
    then park terminally (no_results for empty searches, failed for errors)."""
    if attempts >= MAX_ATTEMPTS:
        status = "failed" if kind == "error" else "no_results"
        db.update_item(item_id, status=status, error=err or None)
        db.log_event(f"Giving up on '{title}' after {attempts} attempts"
                     + (f": {err}" if err else ""), "warn", item_id)
    else:
        db.update_item(item_id, status="pending", error=err or None)
        db.log_event(f"'{title}': no luck yet (attempt {attempts}/{MAX_ATTEMPTS}); will retry"
                     + (f" — {err}" if err else ""), "info", item_id)


def process_item(item_id: int) -> None:
    """Search for a pending item and grab the best match. Transient problems
    (slskd disconnected, momentary enqueue failure) leave the item pending to
    retry — only a genuine dead-end after MAX_ATTEMPTS is parked terminally."""
    item = db.get_item(item_id)
    if not item:
        return
    title, author = item["title"], item.get("author") or ""
    sk = _slskd()
    try:
        # Don't burn an attempt (or hard-fail) when Soulseek is simply mid-reconnect.
        if not sk.is_connected():
            sk.reconnect()
            db.log_event("slskd not connected to Soulseek; nudged reconnect, will retry",
                         "warn", item_id)
            return  # item stays pending
        attempts = int(item.get("attempts") or 0) + 1
        db.update_item(item_id, status="searching", attempts=attempts, error=None)

        prefs = matching.default_prefs(settings)
        best = None
        for q in matching.build_queries(title, author):
            best = matching.pick_best(sk.search(q), title, author, prefs)
            if best:
                break
        if not best:
            _park_or_retry(item_id, attempts, title, "empty")
            return

        try:
            with _lock:
                sk.enqueue(best.username, best.files)
        except Exception as e:
            # enqueue can fail transiently (peer offline, slskd reconnecting) — retry
            if not sk.is_connected():
                sk.reconnect()
            _park_or_retry(item_id, attempts, title, "error", str(e))
            return

        db.update_item(
            item_id, status="downloading", slskd_username=best.username,
            slskd_dir=best.directory,
            chosen_files=json.dumps([f["filename"] for f in best.files]),
            size=best.total_size,
        )
        db.log_event(
            f"Grabbing '{title}' from {best.username} "
            f"({len(best.files)} file(s), {round(best.total_size/1_048_576)}MB)",
            item_id=item_id,
        )
    except Exception as e:
        # search-level transient error — retry rather than fail outright
        attempts = int(item.get("attempts") or 0) + 1
        _park_or_retry(item_id, attempts, title, "error", str(e))
    finally:
        sk.close()


def _check_download(item: dict[str, Any], sk: Slskd) -> None:
    files = set(json.loads(item.get("chosen_files") or "[]"))
    if not files:
        return
    states = sk.transfer_states(item["slskd_username"], files)
    if not states:
        return
    vals = list(states.values())
    if any("Failed" in s or "Errored" in s or "Rejected" in s or "Cancelled" in s for s in vals):
        db.update_item(item["id"], status="failed", error="Soulseek transfer failed")
        db.log_event(f"Transfer failed for '{item['title']}'", "error", item["id"])
        return
    if all("Completed" in s and "Succeeded" in s for s in states.values()):
        _import(item)


def _import(item: dict[str, Any]) -> None:
    db.update_item(item["id"], status="importing")
    root = settings.get("slskd_downloads_path")
    dest = dest_folder(item["title"], item.get("author") or "", item.get("narrator") or "")
    os.makedirs(dest, exist_ok=True)
    moved_paths: list[str] = []
    for remote in json.loads(item.get("chosen_files") or "[]"):
        base = remote.rsplit("\\", 1)[-1]
        local = _find_local(base, root)
        if not local:
            continue
        target = os.path.join(dest, base)
        try:
            shutil.move(local, target)
            moved_paths.append(target)
        except Exception as e:
            db.log_event(f"Move failed for {base}: {e}", "error", item["id"])
    if not moved_paths:
        db.update_item(item["id"], status="failed",
                       error="download completed but no files found to import")
        db.log_event(f"Import found no files for '{item['title']}'", "error", item["id"])
        return
    _write_metadata(item, dest, moved_paths)    # tag before the library scans run
    db.update_item(item["id"], status="done", dest_path=dest, error=None)
    db.log_event(f"Imported '{item['title']}' → {dest} ({len(moved_paths)} file(s))",
                 item_id=item["id"])
    _post_import(item, dest)


def _write_metadata(item: dict[str, Any], dest: str, files: list[str]) -> None:
    """Tag the imported files from the Audible listing (best-effort)."""
    if not settings.get_bool("write_metadata"):
        return
    # ABR and in-app ('user') requests both carry the Audible ASIN in source_id;
    # 'manual' items store a random token there, so only tag by ASIN for the former.
    asin = item.get("source_id") if item.get("source") in ("abr", "user") else None
    meta = None
    cover_bytes = None
    aud = Audnexus(settings.get("audible_region") or "us")
    try:
        if asin:
            meta = audnexus.to_meta(aud.book(asin))
        if not meta:                                 # fallback to what we already know
            meta = {"title": item["title"],
                    "authors": [item["author"]] if item.get("author") else [],
                    "narrators": [item["narrator"]] if item.get("narrator") else [],
                    "asin": asin}
        if settings.get_bool("embed_cover") and meta.get("cover_url"):
            cover_bytes = aud.fetch_bytes(meta["cover_url"])
    finally:
        aud.close()

    cover_url = meta.get("cover_url") or item.get("cover")
    overwrite = settings.get_bool("overwrite_tags")
    embed = settings.get_bool("embed_cover")
    tagged = 0
    for p in files:
        try:
            decisions = tagging.write_file(p, meta, cover_bytes, "image/jpeg", overwrite, embed)
            if decisions:
                db.log_tag_write(item["id"], meta.get("title") or item["title"],
                                 os.path.basename(p), cover_url, decisions)
                tagged += 1
        except Exception as e:
            db.log_event(f"Tagging failed for {os.path.basename(p)}: {e}", "warn", item["id"])
    if cover_bytes and embed:
        try:
            with open(os.path.join(dest, "cover.jpg"), "wb") as f:
                f.write(cover_bytes)
        except Exception:
            pass
    if tagged:
        src = "Audible" if asin and meta.get("asin") else "request data"
        db.log_event(f"Tagged {tagged} file(s) from {src}", item_id=item["id"])


def _post_import(item: dict[str, Any], dest: str) -> None:
    # mark the ABR request fulfilled
    if item.get("source") == "abr" and item.get("source_id") and settings.get("abr_api_key"):
        try:
            abr = ABR(settings.get("abr_url"), settings.get("abr_api_key"))
            abr.mark_downloaded(item["source_id"])
            abr.close()
        except Exception:
            pass
    # trigger media-server scans (best-effort; targeted to the new folder where possible)
    scanned: list[str] = []
    if settings.get("abs_url") and settings.get("abs_library_id"):
        try:
            a = ABS(settings.get("abs_url"), settings.get("abs_api_key"))
            if a.scan(settings.get("abs_library_id")):
                scanned.append("Audiobookshelf")
            a.close()
        except Exception:
            pass
    if settings.get("plex_url") and settings.get("plex_token") and settings.get("plex_library_section_id"):
        try:
            p = Plex(settings.get("plex_url"), settings.get("plex_token"))
            if p.scan(settings.get("plex_library_section_id"), dest):
                scanned.append("Plex")
            p.close()
        except Exception:
            pass
    if settings.get("jellyfin_url") and settings.get("jellyfin_api_key"):
        try:
            j = Jellyfin(settings.get("jellyfin_url"), settings.get("jellyfin_api_key"))
            if j.scan(dest):
                scanned.append("Jellyfin")
            j.close()
        except Exception:
            pass
    if scanned:
        db.log_event("Triggered scan: " + ", ".join(scanned), item_id=item["id"])


# ---------- loop ----------
def _discover_requests() -> None:
    if not (settings.get("abr_url") and settings.get("abr_api_key")):
        STATUS["abr_connected"] = False
        return
    abr = ABR(settings.get("abr_url"), settings.get("abr_api_key"))
    try:
        reqs = abr.list_requests(only_pending=True)
        STATUS["abr_connected"] = True
        for r in reqs:
            book = r.get("book") or r
            asin = book.get("asin")
            if not asin:
                continue
            authors = book.get("authors") or []
            narrators = book.get("narrators") or []
            reqrs = r.get("requests") or []
            db.upsert_item(
                "abr", asin, title=book.get("title", ""),
                author=authors[0] if authors else "",
                narrator=narrators[0] if narrators else "",
                cover=book.get("cover_image"), status="pending",
                requested_by=(reqrs[0].get("user_username") if reqrs else None),
            )
    except Exception as e:
        STATUS["abr_connected"] = False
        STATUS["last_error"] = f"ABR: {e}"
    finally:
        abr.close()


def _update_connectivity() -> None:
    """Refresh cached reachability for optional media servers (shown in the UI).
    None means 'not configured' so the UI can hide the indicator entirely."""
    if settings.get("abs_url") and settings.get("abs_api_key"):
        a = ABS(settings.get("abs_url"), settings.get("abs_api_key"))
        STATUS["abs_connected"] = a.ping(); a.close()
    else:
        STATUS["abs_connected"] = None
    if settings.get("plex_url") and settings.get("plex_token"):
        p = Plex(settings.get("plex_url"), settings.get("plex_token"))
        STATUS["plex_connected"] = p.ping(); p.close()
    else:
        STATUS["plex_connected"] = None
    if settings.get("jellyfin_url") and settings.get("jellyfin_api_key"):
        j = Jellyfin(settings.get("jellyfin_url"), settings.get("jellyfin_api_key"))
        STATUS["jellyfin_connected"] = j.ping(); j.close()
    else:
        STATUS["jellyfin_connected"] = None


def tick() -> None:
    sk = _slskd()
    try:
        STATUS["slskd_connected"] = sk.is_connected()
    except Exception:
        STATUS["slskd_connected"] = False

    try:
        _update_connectivity()
    except Exception:
        pass

    _discover_requests()

    if settings.get_bool("auto_download"):
        for item in db.list_items(statuses=["pending"]):
            if _stop.is_set():
                break
            process_item(item["id"])

    downloading = db.list_items(statuses=["downloading"])
    if downloading:
        for item in downloading:
            try:
                _check_download(item, sk)
            except Exception as e:
                db.log_event(f"progress check failed: {e}", "error", item["id"])
    sk.close()
    STATUS["last_poll"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def _run() -> None:
    STATUS["running"] = True
    db.log_event("Soulbridge worker started")
    while not _stop.is_set():
        try:
            tick()
            STATUS["last_error"] = None
        except Exception as e:
            STATUS["last_error"] = str(e)
            db.log_event(f"worker tick error: {e}", "error")
        _wake.wait(timeout=max(30, settings.get_int("poll_seconds", 120)))
        _wake.clear()
    STATUS["running"] = False


def start() -> None:
    t = threading.Thread(target=_run, name="soulbridge-worker", daemon=True)
    t.start()


def wake() -> None:
    _wake.set()


def stop() -> None:
    _stop.set()
    _wake.set()
