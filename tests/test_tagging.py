"""Tag-merge decision tests. Run with pytest or `python tests/test_tagging.py`."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from soulscribe.core.tagging import decide, decide_field  # noqa: E402


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


def test_liberal_overwrites_authoritative_fields():
    # a messy/inaccurate narrator is replaced outright once we've matched an edition
    assert decide_field("random ripper v2", "Rob Inglis", "composer", True) == ("Rob Inglis", "overwrite")
    assert decide_field("the hobbit (unabridged) [2003]", "The Hobbit", "title", True) == ("The Hobbit", "overwrite")


def test_liberal_leaves_non_authoritative_conservative():
    # author (artist) is not in the authoritative set -> keep the richer existing value
    assert decide_field("Kristin Cashore 2012", "Kristin Cashore", "artist", True) == ("Kristin Cashore 2012", "keep")


def test_gap_fill_only_when_overwrite_off():
    assert decide_field("Old Title", "New Title", "title", False) == ("Old Title", "keep")
    assert decide_field("", "New Title", "title", False) == ("New Title", "write")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("ok:", fn.__name__)
    print(f"\n{len(fns)} passed")
