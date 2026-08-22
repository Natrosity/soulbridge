"""Tag-merge decision tests. Run with pytest or `python tests/test_tagging.py`."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from soulbridge.core.tagging import decide  # noqa: E402


def test_fills_empty():
    assert decide("", "Kristin Cashore") == ("Kristin Cashore", "write")


def test_overwrites_when_new_is_superset():
    # old is a subset of new -> just reorganised/enriched -> replace
    assert decide("Cashore", "Kristin Cashore") == ("Kristin Cashore", "overwrite")


def test_keeps_when_old_has_extra_info():
    assert decide("Kristin Cashore 2012", "Kristin Cashore") == ("Kristin Cashore 2012", "keep")


def test_keeps_on_conflict():
    # different narrators -> keep what's there
    assert decide("Xanthe Elbrick", "Emma Powell") == ("Xanthe Elbrick", "keep")


def test_unchanged_when_equal():
    assert decide("Fire", "fire") == ("Fire", "unchanged")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("ok:", fn.__name__)
    print(f"\n{len(fns)} passed")
