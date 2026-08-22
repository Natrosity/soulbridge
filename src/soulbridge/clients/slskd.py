"""Minimal slskd (Soulseek daemon) API client. Covers search, download enqueue,
and transfer inspection — the slskd v0 REST API."""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx


class SlskdError(RuntimeError):
    pass


class Slskd:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/") + "/api/v0"
        self._c = httpx.Client(
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._c.close()

    # ---- health ----
    def application(self) -> dict[str, Any]:
        r = self._c.get(self.base + "/application")
        r.raise_for_status()
        return r.json()

    def is_connected(self) -> bool:
        try:
            state = (self.application().get("server") or {}).get("state", "")
            return "Connected" in state and "LoggedIn" in state
        except Exception:
            return False

    # ---- search ----
    def start_search(self, text: str) -> str:
        r = self._c.post(self.base + "/searches", json={"searchText": text})
        r.raise_for_status()
        return r.json()["id"]

    def search_state(self, search_id: str) -> dict[str, Any]:
        r = self._c.get(f"{self.base}/searches/{search_id}")
        r.raise_for_status()
        return r.json()

    def search_responses(self, search_id: str) -> list[dict[str, Any]]:
        r = self._c.get(f"{self.base}/searches/{search_id}/responses")
        r.raise_for_status()
        return r.json()

    def search(self, text: str, wait: float = 40.0, poll: float = 3.0) -> list[dict[str, Any]]:
        """Run a search and return its responses. Soulseek results trickle in with
        pauses, so we wait until slskd marks the search Completed (its network
        timeout, typically 15-30s) or `wait` elapses — never on a momentary stall."""
        sid = self.start_search(text)
        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(poll)
            if "Completed" in self.search_state(sid).get("state", ""):
                break
        return self.search_responses(sid)

    # ---- downloads ----
    def enqueue(self, username: str, files: list[dict[str, Any]]) -> None:
        """files: [{'filename': <remote path>, 'size': <bytes>}]."""
        payload = [{"filename": f["filename"], "size": int(f.get("size", 0))} for f in files]
        r = self._c.post(f"{self.base}/transfers/downloads/{username}", json=payload)
        if r.status_code >= 300:
            raise SlskdError(f"enqueue failed {r.status_code}: {r.text[:300]}")

    def downloads(self) -> list[dict[str, Any]]:
        r = self._c.get(self.base + "/transfers/downloads")
        r.raise_for_status()
        return r.json()

    def transfer_states(self, username: str, filenames: set[str]) -> dict[str, str]:
        """Return {remote_filename: state} for the given user's tracked downloads."""
        out: dict[str, str] = {}
        for user in self.downloads():
            if user.get("username") != username:
                continue
            for directory in user.get("directories", []):
                for f in directory.get("files", []):
                    fn = f.get("filename")
                    if fn in filenames:
                        out[fn] = f.get("state", "")
        return out
