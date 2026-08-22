"""Audiobookshelf client — only used to trigger a library scan after import
(optional; ABS also has a filesystem watcher)."""
from __future__ import annotations

import httpx


class ABS:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self._c = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )

    def close(self) -> None:
        self._c.close()

    @property
    def configured(self) -> bool:
        return bool(self.base and self._c.headers.get("Authorization"))

    def ping(self) -> bool:
        if not self.configured:
            return False
        try:
            return self._c.get(self.base + "/api/libraries").status_code < 400
        except Exception:
            return False

    def scan(self, library_id: str) -> bool:
        if not (self.base and library_id):
            return False
        try:
            r = self._c.post(f"{self.base}/api/libraries/{library_id}/scan")
            return r.status_code < 300
        except Exception:
            return False
