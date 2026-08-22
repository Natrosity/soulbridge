"""AudioBookRequest client. ABR authenticates API calls with a Bearer token
(Settings -> API keys)."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx


class ABR:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self._c = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._c.close()

    def health(self) -> bool:
        try:
            return self._c.get(self.base + "/api/health").status_code == 200
        except Exception:
            return False

    def list_requests(self, only_pending: bool = True) -> list[dict[str, Any]]:
        f = "not_downloaded" if only_pending else "all"
        r = self._c.get(f"{self.base}/api/requests?filter={f}")
        r.raise_for_status()
        return r.json() or []

    def mark_downloaded(self, asin: str) -> bool:
        r = self._c.patch(f"{self.base}/api/requests/{quote(asin)}/downloaded")
        return r.status_code < 300
