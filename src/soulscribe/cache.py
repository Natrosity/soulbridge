"""A tiny thread-safe in-process TTL cache.

Single-process only: entries live in this process's memory and are shared
between the web layer and the background worker thread (hence the lock), but
NOT across processes or restarts. Soulscribe is designed to run as one Uvicorn
worker plus one worker thread; running multiple worker processes would give each
its own copy of every cache (and its own worker thread), so don't.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

_MISSING = object()


class TTLCache:
    """Map keys to values that expire after ``ttl`` seconds.

    ``get`` returns only fresh values (and drops an expired one it finds);
    ``peek`` returns the last stored value regardless of age, for callers that
    want to fall back to stale data when a refresh fails.
    """

    def __init__(self, ttl: float, max_entries: int = 512):
        self.ttl = ttl
        self.max_entries = max_entries
        self._d: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            entry = self._d.get(key)
            if entry is not None and time.time() - entry[0] < self.ttl:
                return entry[1]
            # Expired entries are left in place (not evicted here) so peek() can
            # still return the last-good value; the size-based sweep reclaims them.
            return default

    def peek(self, key: Any, default: Any = None) -> Any:
        """Return the stored value ignoring its age (None/`default` if absent)."""
        with self._lock:
            entry = self._d.get(key)
            return entry[1] if entry is not None else default

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            if len(self._d) >= self.max_entries:
                self._sweep_locked()
            self._d[key] = (time.time(), value)

    def get_or_set(self, key: Any, factory: Callable[[], Any]) -> Any:
        """Return the fresh cached value, or compute it with ``factory``, store, return."""
        found = self.get(key, _MISSING)
        if found is not _MISSING:
            return found
        value = factory()
        self.set(key, value)
        return value

    def pop(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            entry = self._d.pop(key, None)
            return entry[1] if entry is not None else default

    def __contains__(self, key: Any) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def _sweep_locked(self) -> None:
        cutoff = time.time() - self.ttl
        for k in [k for k, (ts, _) in self._d.items() if ts < cutoff]:
            self._d.pop(k, None)
