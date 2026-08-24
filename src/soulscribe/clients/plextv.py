"""plex.tv OAuth (PIN flow) + account/resource lookup — the 'Sign in with Plex'
side, separate from the local-server scan client in plex.py.

Overseerr-style flow: create a PIN, send the user to app.plex.tv to authorise,
then poll the PIN for an auth token, fetch their account, and confirm the account
can reach the configured Plex server (membership) before letting them in."""
from __future__ import annotations

import urllib.parse
from typing import Any, Optional

import httpx

PLEXTV = "https://plex.tv"
AUTH_APP = "https://app.plex.tv/auth"


def auth_url(client_id: str, code: str, forward_url: str, product: str = "Soulscribe") -> str:
    """The app.plex.tv page the user visits to authorise the PIN. Params live in the
    URL fragment (#?...), which is how Plex's hosted auth expects them."""
    q = urllib.parse.urlencode({
        "clientID": client_id,
        "code": code,
        "forwardUrl": forward_url,
        "context[device][product]": product,
    })
    return f"{AUTH_APP}#?{q}"


def server_in_resources(resources: list[dict[str, Any]], machine_id: str) -> bool:
    """True if the signed-in account can reach the given Plex server (owned or shared).
    A resource is the configured server when its clientIdentifier matches and it
    actually provides server capabilities."""
    if not machine_id:
        return False
    for r in resources:
        if r.get("clientIdentifier") == machine_id and "server" in (r.get("provides") or ""):
            return True
    return False


class PlexTV:
    def __init__(self, client_id: str, product: str = "Soulscribe",
                 version: str = "", timeout: float = 15.0):
        self.client_id = client_id
        self.product = product
        self.version = version or "0"
        self._c = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._c.close()

    def _headers(self, token: Optional[str] = None) -> dict[str, str]:
        h = {
            "Accept": "application/json",
            "X-Plex-Product": self.product,
            "X-Plex-Version": self.version,
            "X-Plex-Client-Identifier": self.client_id,
        }
        if token:
            h["X-Plex-Token"] = token
        return h

    def create_pin(self) -> dict[str, Any]:
        """Returns {'id':..., 'code':...} for a fresh strong PIN."""
        r = self._c.post(f"{PLEXTV}/api/v2/pins", headers=self._headers(),
                         data={"strong": "true"})
        r.raise_for_status()
        d = r.json()
        return {"id": d["id"], "code": d["code"]}

    def check_pin(self, pin_id: int) -> Optional[str]:
        """Poll a PIN; returns the auth token once the user has authorised, else None."""
        r = self._c.get(f"{PLEXTV}/api/v2/pins/{pin_id}", headers=self._headers())
        if r.status_code >= 400:
            return None
        return r.json().get("authToken") or None

    def account(self, token: str) -> Optional[dict[str, Any]]:
        """The Plex account behind an auth token: {id, username, email, thumb}."""
        r = self._c.get(f"{PLEXTV}/api/v2/user", headers=self._headers(token))
        if r.status_code >= 400:
            return None
        d = r.json()
        return {"id": str(d.get("id") or ""), "username": d.get("username") or d.get("title") or "",
                "email": d.get("email"), "thumb": d.get("thumb")}

    def resources(self, token: str) -> list[dict[str, Any]]:
        """Servers/devices the account can reach (used for the membership check)."""
        r = self._c.get(f"{PLEXTV}/api/v2/resources", headers=self._headers(token),
                        params={"includeHttps": 1, "includeRelay": 1})
        if r.status_code >= 400:
            return []
        d = r.json()
        return d if isinstance(d, list) else []
