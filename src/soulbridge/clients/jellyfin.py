"""Jellyfin client — library scan after import. Tries a targeted path update
first (only works if Jellyfin sees the same path), then falls back to a refresh."""
from __future__ import annotations

import httpx


class Jellyfin:
    def __init__(self, base_url: str, api_key: str, timeout: float = 15.0):
        self.base = (base_url or "").rstrip("/")
        self.key = api_key or ""
        self._c = httpx.Client(headers={"X-Emby-Token": self.key}, timeout=timeout)

    def close(self) -> None:
        self._c.close()

    @property
    def configured(self) -> bool:
        return bool(self.base and self.key)

    def ping(self) -> bool:
        if not self.configured:
            return False
        try:
            return self._c.get(self.base + "/System/Info").status_code < 400
        except Exception:
            return False

    def scan(self, path: str | None = None) -> bool:
        if not self.configured:
            return False
        try:
            if path:
                r = self._c.post(self.base + "/Library/Media/Updated",
                                 json={"Updates": [{"Path": path, "UpdateType": "Created"}]})
                if r.status_code < 400:
                    return True
            return self._c.post(self.base + "/Library/Refresh").status_code < 400
        except Exception:
            return False
