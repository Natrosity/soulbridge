"""TTLCache behaviour. Runnable with pytest or `python tests/test_cache.py`."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from soulscribe.cache import TTLCache  # noqa: E402


def test_get_returns_fresh_value():
    c = TTLCache(10)
    c.set("k", 123)
    assert c.get("k") == 123
    assert "k" in c


def test_get_expires_and_peek_survives():
    c = TTLCache(0.02)
    c.set("k", "v")
    time.sleep(0.05)
    assert c.get("k") is None            # expired -> gone from get
    assert c.peek("k") == "v"            # ...but last-good still available via peek


def test_get_or_set_computes_once():
    c = TTLCache(10)
    calls = []

    def factory():
        calls.append(1)
        return "made"

    assert c.get_or_set("k", factory) == "made"
    assert c.get_or_set("k", factory) == "made"
    assert len(calls) == 1               # factory ran only on the miss


def test_pop_removes():
    c = TTLCache(10)
    c.set("k", 1)
    assert c.pop("k") == 1
    assert c.get("k") is None
    assert c.pop("missing", "d") == "d"


def test_sweep_bounds_size():
    c = TTLCache(0.02, max_entries=4)
    for i in range(4):
        c.set(i, i)
    time.sleep(0.05)                     # let them all expire
    c.set("fresh", 1)                    # triggers a sweep at capacity
    assert len(c._d) == 1                # expired entries were reclaimed


def test_missing_returns_default():
    c = TTLCache(10)
    assert c.get("nope", "fallback") == "fallback"
    assert "nope" not in c


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("\nall cache tests passed")
