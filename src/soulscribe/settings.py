"""Configuration schema. Settings live in the DB and are editable in the web UI;
environment variables of the form SOULSCRIBE_<KEY> seed them on first run
(the legacy SOULBRIDGE_<KEY> names are still honoured for upgraded installs)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import db
from .env import env


@dataclass
class Field:
    key: str
    label: str
    default: str
    group: str
    type: str = "text"          # text | password | bool | number | textarea | select
    help: str = ""
    options: tuple = ()          # for type == "select"


SPEC: list[Field] = [
    # Soulseek / slskd
    Field("slskd_url", "slskd URL", "http://slskd:5030", "Soulseek",
          help="Base URL of your slskd instance."),
    Field("slskd_api_key", "slskd API key", "", "Soulseek", "password",
          help="From slskd.yml (web.authentication.api_keys)."),
    Field("slskd_downloads_path", "slskd downloads path", "/data/soulseek/complete", "Soulseek",
          help="Where completed slskd downloads appear, as Soulscribe sees them."),
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
          help="Optional. If set, Soulscribe triggers a library scan after import."),
    Field("abs_api_key", "Audiobookshelf API token", "", "Audiobookshelf", "password"),
    Field("abs_library_id", "Audiobookshelf library id", "", "Audiobookshelf"),
    # Plex scan (optional) — Plex can scan just the new folder
    Field("plex_url", "Plex URL", "", "Plex",
          help="Optional. e.g. http://plex:32400. Triggers a targeted folder scan after import."),
    Field("plex_token", "Plex token", "", "Plex", "password",
          help="X-Plex-Token. Find it in any item's 'Get Info > View XML' URL."),
    Field("plex_library_section_id", "Plex library section id", "", "Plex",
          help="The audiobook library's section id (a number, e.g. 5)."),
    Field("plex_login_enabled", "Enable 'Sign in with Plex'", "false", "Plex", "bool",
          help="Let users sign in with their Plex account. Only accounts with access to "
               "the Plex server above (URL + token) are admitted; new ones are auto-created "
               "at the default role. Requires the Plex URL and token to be set."),
    # Jellyfin scan (optional)
    Field("jellyfin_url", "Jellyfin URL", "", "Jellyfin",
          help="Optional. e.g. http://jellyfin:8096. Triggers a library scan after import."),
    Field("jellyfin_api_key", "Jellyfin API key", "", "Jellyfin", "password",
          help="Dashboard > API Keys."),
    # Behaviour
    Field("auto_download", "Auto-download requests", "true", "Behaviour", "bool",
          help="When on, approved ABR requests are searched and grabbed automatically."),
    Field("default_request_mode", "Default request mode", "auto", "Behaviour", "select",
          help="'auto' grabs the best Soulseek match automatically; 'interactive' lets the "
               "requester pick from ranked candidates. Sets the pre-selected choice per request.",
          options=("auto", "interactive")),
    Field("poll_seconds", "Poll interval (seconds)", "120", "Behaviour", "number"),
    Field("require_free_slot", "Prefer sources with a free upload slot", "true", "Behaviour", "bool"),
    Field("preferred_formats", "Preferred formats (priority order)", "m4b,m4a,mp3,flac,ogg", "Behaviour",
          help="Comma-separated. Single-file m4b is ideal for audiobooks."),
    Field("min_size_mb", "Minimum size (MB)", "50", "Behaviour", "number",
          help="Reject tiny files (samples, single tracks, wrong matches). Full "
               "audiobooks are usually 50MB+."),
    Field("max_size_mb", "Maximum size (MB)", "4000", "Behaviour", "number"),
    # Metadata tagging (Audible via Audnexus)
    Field("write_metadata", "Tag files from Audible", "true", "Metadata", "bool",
          help="After download, write tags + cover from the Audible listing (Audnexus)."),
    Field("embed_cover", "Embed cover art", "true", "Metadata", "bool"),
    Field("overwrite_tags", "Replace tags when the new value is better", "true", "Metadata", "bool",
          help="Always fills empty tags. When on, also replaces an existing tag if the new "
               "value contains everything the old one did; otherwise the existing tag is kept."),
    Field("audible_region", "Audible region", "us", "Metadata",
          help="us, uk, de, fr, au, ca, in, it, es, jp."),
    # Notifications (Apprise)
    Field("apprise_urls", "Apprise URLs", "", "Notifications", "textarea",
          help="One per line (or comma-separated). Apprise fans out to Discord, Telegram, "
               "ntfy, Pushover, email and many more — e.g. ntfy://ntfy.sh/mytopic or "
               "tgram://bottoken/chatid. See the Apprise wiki for the URL formats."),
    Field("notify_on_request", "Notify on new request", "true", "Notifications", "bool",
          help="A book was requested (and, for standard users, is awaiting approval)."),
    Field("notify_on_complete", "Notify on completed download", "true", "Notifications", "bool"),
    Field("notify_on_failure", "Notify on failure", "true", "Notifications", "bool",
          help="A request could not be found on Soulseek or a transfer failed."),
    # Access / users
    Field("default_role", "Default role for new users", "standard", "Access", "select",
          help="'standard' (requests need admin approval) or 'trusted' (auto-download).",
          options=("standard", "trusted")),
    Field("request_quota", "Request quota per user", "0", "Access", "number",
          help="Max open requests a non-admin may have. 0 = unlimited."),
    Field("instance_name", "Instance name", "Soulscribe", "General"),
    Field("public_url", "Public URL", "", "General",
          help="External base URL users reach this at, e.g. https://soulscribe.example.com. "
               "Used to build the Plex sign-in redirect. Leave blank to infer from the request."),
]

_BY_KEY = {f.key: f for f in SPEC}


def is_secret(key: str) -> bool:
    f = _BY_KEY.get(key)
    return bool(f and f.type == "password")


def seed_from_env() -> None:
    """On first run, fill any unset settings from SOULSCRIBE_<KEY> env vars (or defaults)."""
    existing = db.all_settings()
    for f in SPEC:
        if f.key in existing:
            continue
        seed = env(f.key.upper())
        db.set_setting(f.key, seed if seed is not None else f.default)


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
