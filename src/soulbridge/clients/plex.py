"""Plex client — targeted library scans. Plex can refresh just one folder within
a section via ?path=, so a new audiobook doesn't trigger a whole-library scan."""
from __future__ import annotations

import httpx


class Plex:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0):
        self.base = (base_url or "").rstrip("/")
        self.token = token or ""
        self._c = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._c.close()

    @property
    def configured(self) -> bool:
        return bool(self.base and self.token)

    def _params(self, extra: dict | None = None) -> dict:
        p = {"X-Plex-Token": self.token}
        if extra:
            p.update({k: v for k, v in extra.items() if v is not None})
        return p

    def ping(self) -> bool:
        if not self.configured:
            return False
        try:
            return self._c.get(self.base + "/identity", params=self._params()).status_code < 400
        except Exception:
            return False

    def scan(self, section_id: str, path: str | None = None) -> bool:
        """Refresh a section, optionally scoped to a single folder path."""
        if not (self.configured and section_id):
            return False
        try:
            r = self._c.get(f"{self.base}/library/sections/{section_id}/refresh",
                            params=self._params({"path": path} if path else None))
            return r.status_code < 400
        except Exception:
            return False
