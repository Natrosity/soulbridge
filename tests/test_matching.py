"""Ranking/query regression tests. Runnable with pytest or `python tests/test_matching.py`."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from soulbridge.core import matching as m  # noqa: E402

PREFS = {"formats": ["m4b", "m4a", "mp3", "flac", "ogg"],
         "min_mb": 50, "max_mb": 4000, "require_free_slot": True}


def _f(path, size):
    return {"filename": path, "size": size, "bitRate": 128}


def _resp(username, free, files):
    return {"username": username, "hasFreeUploadSlot": free, "files": files}


def test_query_drops_short_contraction_tokens():
    # "I'm" -> "im" would not match "I'm ..." on Soulseek; the clean query must not include it.
    qs = m.build_queries("I'm Glad My Mom Died", "Jennette McCurdy")
    assert qs[0] == "glad mom died"


def test_accents_folded_to_ascii():
    # accented letters must fold, not split the word ("recursión" -> "recursion")
    assert m.norm("Recursión") == "recursion"
    assert m.norm("Les Misérables") == "les miserables"
    assert m.norm("Æther") == "aether"
    assert m.build_queries("Recursión", "Blake Crouch")[0] == "recursion"


def test_prefers_single_m4b_with_free_slot():
    resp = [
        _resp("a", True, [_f(r"x\I'm Glad My Mom Died - Jennette McCurdy.m4b", 391_000_000)]),
        _resp("b", False, [_f(r"y\I'm Glad My Mom Died.mp3", 391_000_000)]),
    ]
    best = m.pick_best(resp, "I'm Glad My Mom Died", "Jennette McCurdy", PREFS)
    assert best and best.username == "a"


def test_rejects_summary_and_sample():
    resp = [
        _resp("s", True, [_f(r"x\Summary of Cage of Souls.mp3", 8_000_000)]),
        _resp("t", True, [_f(r"y\Cage of Souls sample.m4b", 3_000_000)]),
    ]
    assert m.pick_best(resp, "Cage of Souls", "Adrian Tchaikovsky", PREFS) is None


def test_generic_one_word_title_requires_author():
    # "Fire" must not match a music track, nor an author-less file.
    resp = [
        _resp("music", True, [_f(r"NYE DJ Set\The Roof Is on Fire (Mixed).m4a", 21_000_000)]),
        _resp("noauthor", True, [_f(r"audiobooks\Fire (Unabridged).m4b", 300_000_000)]),
        _resp("book", True, [_f(r"Kristin Cashore - Fire\Fire - Kristin Cashore.m4b", 320_000_000)]),
    ]
    best = m.pick_best(resp, "Fire", "Kristin Cashore", PREFS)
    assert best and best.username == "book"


def test_edition_prefers_requested_narrator():
    resp = [
        _resp("fry", True, [_f(r"x\Harry Potter and the Chamber of Secrets - Stephen Fry.m4b", 380_000_000)]),
        _resp("dale", True, [_f(r"y\Harry Potter and the Chamber of Secrets - Jim Dale.m4b", 380_000_000)]),
    ]
    best = m.pick_best(resp, "Harry Potter and the Chamber of Secrets", "J.K. Rowling", PREFS,
                       edition={"narrator": "Stephen Fry", "year": "2015"})
    assert best and best.username == "fry"


def test_edition_avoids_full_cast_when_standard_requested():
    resp = [
        _resp("standard", True, [_f(r"a\Harry Potter and the Chamber of Secrets - Stephen Fry.m4b", 380_000_000)]),
        _resp("cast", True, [_f(r"b\Harry Potter and the Chamber of Secrets (Full Cast Edition).m4b", 400_000_000)]),
    ]
    best = m.pick_best(resp, "Harry Potter and the Chamber of Secrets", "J.K. Rowling", PREFS)
    assert best and best.username == "standard"


def test_rejects_music_remix_for_short_title():
    # "Game Changer" pulled two trance remixes from a Music folder — must not match.
    resp = [_resp("dj", True, [
        _f(r"@@civju\Music\Apple Lossless\Bryan Kearney\Bryan Kearney - The Game Changer (Standerwick Remix).m4a", 57_000_000),
        _f(r"@@civju\Music\Apple Lossless\Bryan Kearney\Bryan Kearney - The Game Changer.m4a", 58_000_000),
    ])]
    assert m.pick_best(resp, "Game Changer", "Rachel Reid", PREFS) is None


def test_rejects_music_album_for_short_title():
    # "Role Model" pulled a 10-track Bodyjar album — must not match.
    files = [_f(rf"@@fgkzr\Music\Bodyjar\Role Model\{i:02d} track.mp3", 7_600_000) for i in range(1, 11)]
    assert m.pick_best([_resp("wally", True, files)], "Role Model", "Rachel Reid", PREFS) is None


def test_rejects_wrong_author_for_generic_title():
    # "The Long Game" grabbed a single m4b by the WRONG author (Eric Becker).
    resp = [_resp("k", True, [
        _f(r"@@zhezk\Audiobooks\The Long Game by Eric Becker\The Long Game by Eric Becker.m4b", 258_000_000)])]
    assert m.pick_best(resp, "The Long Game", "Rachel Reid", PREFS) is None


def test_accepts_correct_author_for_generic_title():
    # the right book (author named) should still be grabbed.
    resp = [_resp("g", True, [
        _f(r"x\Audiobooks\Rachel Reid - The Long Game\Rachel Reid - The Long Game.m4b", 250_000_000)])]
    best = m.pick_best(resp, "The Long Game", "Rachel Reid", PREFS)
    assert best and best.username == "g"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print(f"\n{len(fns)} passed")
