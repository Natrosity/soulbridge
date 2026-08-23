"""Apprise notifications — best-effort and fire-and-forget.

Sends on a daemon thread so neither the web request nor the worker tick ever
blocks on (or fails because of) a notifier. Apprise fans one call out to any
configured service URL (Discord, Telegram, ntfy, Pushover, email, …)."""
from __future__ import annotations

import threading

from .. import settings


def urls() -> list[str]:
    """Configured Apprise URLs — one per line or comma-separated."""
    raw = settings.get("apprise_urls") or ""
    return [u.strip() for u in raw.replace(",", "\n").splitlines() if u.strip()]


def _deliver(title: str, body: str, targets: list[str]) -> None:
    try:
        import apprise
        ap = apprise.Apprise()
        for u in targets:
            ap.add(u)
        ap.notify(title=title, body=body)
    except Exception:
        pass                                    # notifications must never break the pipeline


def send(title: str, body: str) -> None:
    targets = urls()
    if not targets:
        return
    threading.Thread(target=_deliver, args=(title, body, targets),
                     name="sb-notify", daemon=True).start()


def event(kind: str, title: str, body: str) -> None:
    """Send only when the matching `notify_on_<kind>` toggle is enabled."""
    if settings.get_bool(f"notify_on_{kind}"):
        send(title, body)


def test() -> tuple[bool, str]:
    """Synchronously send a test notification (for the Settings 'Test' button).
    Returns (ok, message)."""
    targets = urls()
    if not targets:
        return False, "No Apprise URLs configured."
    try:
        import apprise
    except Exception as e:                       # pragma: no cover - import guard
        return False, f"Apprise not available: {e}"
    ap = apprise.Apprise()
    added = sum(1 for u in targets if ap.add(u))
    if not added:
        return False, "No valid Apprise URLs (check the format)."
    ok = ap.notify(title="Soulbridge test",
                   body="If you can read this, notifications are working. 📚")
    return (bool(ok), "Test notification sent." if ok else
            "Apprise rejected the notification (check the URLs/credentials).")
