"""Configuration schema. Settings live in the DB and are editable in the web UI;
environment variables of the form SOULBRIDGE_<KEY> seed them on first run."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from . import db


@dataclass
class Field:
    key: str
    label: str
    default: str
    group: str
    type: str = "text"          # text | password | bool | number | textarea
    help: str = ""


SPEC: list[Field] = [
    # Soulseek / slskd
    Field("slskd_url", "slskd URL", "http://slskd:5030", "Soulseek",
          help="Base URL of your slskd instance."),
    Field("slskd_api_key", "slskd API key", "", "Soulseek", "password",
          help="From slskd.yml (web.authentication.api_keys)."),
    Field("slskd_downloads_path", "slskd downloads path", "/data/soulseek/complete", "Soulseek",
          help="Where completed slskd downloads appear, as Soulbridge sees them."),
    # AudioBookRequest
    Field("abr_url", "AudioBookRequest URL", "http://audiobookrequest:8000", "AudioBookRequest",
          help="Base URL of your ABR instance. Leave blank to run search-only."),
    Field("abr_api_key", "AudioBookRequest API key", "", "AudioBookRequest", "password",
          help="ABR → Settings → API keys. Used with a Bearer header."),
    # Library / output
    Field("library_path", "Audiobook library path", "/data/media/audiobooks", "Library",
          help="Where organised audiobooks are placed (Audiobookshelf/Plex read this)."),
    Field("folder_template", "Folder template", "{author}/{title}", "Library",
          help="Placeholders: {author} {title} {narrator}."),
    # Audiobookshelf scan (optional)
    Field("abs_url", "Audiobookshelf URL", "", "Audiobookshelf",
          help="Optional. If set, Soulbridge triggers a library scan after import."),
    Field("abs_api_key", "Audiobookshelf API token", "", "Audiobookshelf", "password"),
    Field("abs_library_id", "Audiobookshelf library id", "", "Audiobookshelf"),
    # Plex scan (optional) — Plex can scan just the new folder
    Field("plex_url", "Plex URL", "", "Plex",
          help="Optional. e.g. http://plex:32400. Triggers a targeted folder scan after import."),
    Field("plex_token", "Plex token", "", "Plex", "password",
          help="X-Plex-Token. Find it in any item's 'Get Info > View XML' URL."),
    Field("plex_library_section_id", "Plex library section id", "", "Plex",
          help="The audiobook library's section id (a number, e.g. 5)."),
    # Jellyfin scan (optional)
    Field("jellyfin_url", "Jellyfin URL", "", "Jellyfin",
          help="Optional. e.g. http://jellyfin:8096. Triggers a library scan after import."),
    Field("jellyfin_api_key", "Jellyfin API key", "", "Jellyfin", "password",
          help="Dashboard > API Keys."),
    # Behaviour
    Field("auto_download", "Auto-download requests", "true", "Behaviour", "bool",
          help="When on, approved ABR requests are searched and grabbed automatically."),
    Field("poll_seconds", "Poll interval (seconds)", "120", "Behaviour", "number"),
    Field("require_free_slot", "Prefer sources with a free upload slot", "true", "Behaviour", "bool"),
    Field("preferred_formats", "Preferred formats (priority order)", "m4b,m4a,mp3,flac,ogg", "Behaviour",
          help="Comma-separated. Single-file m4b is ideal for audiobooks."),
    Field("min_size_mb", "Minimum size (MB)", "50", "Behaviour", "number",
          help="Reject tiny files (samples, single tracks, wrong matches). Full "
               "audiobooks are usually 50MB+."),
    Field("max_size_mb", "Maximum size (MB)", "4000", "Behaviour", "number"),
    Field("instance_name", "Instance name", "Soulbridge", "General"),
]

_BY_KEY = {f.key: f for f in SPEC}


def seed_from_env() -> None:
    """On first run, fill any unset settings from SOULBRIDGE_<KEY> env vars (or defaults)."""
    existing = db.all_settings()
    for f in SPEC:
        if f.key in existing:
            continue
        env = os.environ.get("SOULBRIDGE_" + f.key.upper())
        db.set_setting(f.key, env if env is not None else f.default)


def get(key: str) -> str:
    val = db.get_setting(key)
    if val is None:
        val = _BY_KEY[key].default if key in _BY_KEY else ""
    return val


def get_bool(key: str) -> bool:
    return get(key).strip().lower() in ("1", "true", "yes", "on")


def get_int(key: str, fallback: int = 0) -> int:
    try:
        return int(float(get(key)))
    except (TypeError, ValueError):
        return fallback


def get_list(key: str) -> list[str]:
    return [x.strip().lower() for x in get(key).split(",") if x.strip()]


def as_dict() -> dict[str, Any]:
    return {f.key: get(f.key) for f in SPEC}


def groups() -> list[str]:
    seen: list[str] = []
    for f in SPEC:
        if f.group not in seen:
            seen.append(f.group)
    return seen
