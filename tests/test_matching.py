"""Ranking/query regression tests. Runnable with pytest or `python tests/test_matching.py`."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from soulscribe.core import matching as m  # noqa: E402

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


def test_blocklisted_source_is_skipped():
    resp = [
        _resp("good", True, [_f(r"x\Audiobooks\Rachel Reid - The Long Game\The Long Game - Rachel Reid.m4b", 250_000_000)]),
        _resp("bad", True, [_f(r"y\Audiobooks\Rachel Reid - The Long Game\The Long Game - Rachel Reid.m4b", 250_000_000)]),
    ]
    blocked = {("bad", r"y\Audiobooks\Rachel Reid - The Long Game")}
    best = m.pick_best(resp, "The Long Game", "Rachel Reid", PREFS, blocked=blocked)
    assert best and best.username == "good"
    # if the only source is blocked, nothing is returned
    assert m.pick_best([resp[1]], "The Long Game", "Rachel Reid", PREFS, blocked=blocked) is None


MISTBORN_RESULTS = [
    {"asin": "B1", "title": "Mistborn", "subtitle": "Mistborn, Book 1", "series": "The Cosmere"},
    {"asin": "B2", "title": "The Well of Ascension", "subtitle": "Mistborn, Book 2", "series": "The Mistborn Saga"},
    {"asin": "B3", "title": "The Hero of Ages", "subtitle": "Mistborn, Book 3", "series": "The Mistborn Saga"},
    {"asin": "B4", "title": "The Alloy of Law", "subtitle": "Mistborn, Book 4", "series": "Wax and Wayne"},
]


def test_build_siblings_excludes_other_series_entries():
    sibs = m.build_siblings(MISTBORN_RESULTS, "B1", "Mistborn")
    assert frozenset({"alloy", "law"}) in sibs
    assert frozenset({"well", "ascension"}) in sibs
    assert frozenset({"hero", "ages"}) in sibs
    # nothing that would match the requested book itself
    assert all("mistborn" not in s for s in sibs)


def test_rejects_wrong_series_entry():
    # requested "Mistborn" (Book 1); the author folder carries "Sanderson" so the
    # generic-title guard passes — the sibling check must still reject Alloy of Law.
    sibs = m.build_siblings(MISTBORN_RESULTS, "B1", "Mistborn")
    ed = {"narrator": "Michael Kramer", "year": "2011"}
    alloy = [_resp("u", True, [_f(
        r"Brandon Sanderson\Alloy of Law, The - A Mistborn Novel read by Michael Kramer (2011).m4b", 296_000_000)])]
    assert m.pick_best(alloy, "Mistborn", "Brandon Sanderson", PREFS, edition=ed, siblings=sibs) is None
    # the right book (Book 1 / Final Empire) is still accepted
    fe = [_resp("u", True, [_f(
        r"Brandon Sanderson\Final Empire, The - Mistborn Book 1 read by Michael Kramer (2011).m4b", 673_000_000)])]
    best = m.pick_best(fe, "Mistborn", "Brandon Sanderson", PREFS, edition=ed, siblings=sibs)
    assert best and best.username == "u"


EDITION_RESULTS = [
    {"asin": "A", "title": "The Hobbit", "subtitle": None, "narrators": ["Rob Inglis"], "year": 2012},
    {"asin": "B", "title": "The Hobbit", "subtitle": None, "narrators": ["Andy Serkis"], "year": 2020},
    {"asin": "C", "title": "The Fellowship of the Ring", "subtitle": None, "narrators": ["Rob Inglis"], "year": 2012},
]


def test_build_editions_finds_same_book_alternates():
    alts = m.build_editions(EDITION_RESULTS, "A", "The Hobbit")
    assert len(alts) == 1 and alts[0]["asin"] == "B"     # B same book, C is a different book


def test_pick_edition_switches_on_narrator():
    alts = m.build_editions(EDITION_RESULTS, "A", "The Hobbit")
    target = {"narrators": ["Rob Inglis"], "year": "2012"}
    assert m.pick_edition("Andy Serkis", "2020", target, alts)["asin"] == "B"   # file is Serkis
    assert m.pick_edition("Rob Inglis", "2012", target, alts) is None           # file is the target
    assert m.pick_edition("", "", target, alts) is None                         # no file metadata


def test_book_number_lifts_correct_series_entry():
    # requested Book 1; a file that names "Book 1" beats a sibling that doesn't,
    # even when the sibling check can't reject it (no number on its own listing).
    ed = {"book_number": 1}
    fe = _resp("fe", True, [_f(r"Brandon Sanderson\The Final Empire - Mistborn Book 1.m4b", 673_000_000)])
    other = _resp("other", True, [_f(r"Brandon Sanderson\Shadows of Self - A Mistborn Novel.m4b", 345_000_000)])
    best = m.pick_best([other, fe], "Mistborn", "Brandon Sanderson", PREFS, edition=ed)
    assert best and best.username == "fe"


def test_bitrate_breaks_ties():
    # two otherwise-identical rips: the higher-bitrate one wins
    lo = _resp("lo", True, [{"filename": r"x\Audiobooks\Dark Matter - Blake Crouch.m4b", "size": 300_000_000, "bitRate": 64}])
    hi = _resp("hi", True, [{"filename": r"y\Audiobooks\Dark Matter - Blake Crouch.m4b", "size": 300_000_000, "bitRate": 256}])
    best = m.pick_best([lo, hi], "Dark Matter", "Blake Crouch", PREFS)
    assert best and best.username == "hi"


def test_bitrate_does_not_override_real_signal():
    # a lower-bitrate rip that names the requested narrator still beats a higher-bitrate one
    narr = _resp("narr", True, [{"filename": r"x\Audiobooks\Dark Matter - Blake Crouch read by Jon Doe.m4b", "size": 300_000_000, "bitRate": 64}])
    plain = _resp("plain", True, [{"filename": r"y\Audiobooks\Dark Matter - Blake Crouch.m4b", "size": 300_000_000, "bitRate": 320}])
    best = m.pick_best([narr, plain], "Dark Matter", "Blake Crouch", PREFS, edition={"narrator": "Jon Doe"})
    assert best and best.username == "narr"


# ------------------------------------------------------------ tunable weights
def test_default_prefs_without_weights_uses_hardcoded_defaults():
    # PREFS above never sets "weights"/"keywords" — score_group must fall back
    # to DEFAULT_WEIGHTS/DEFAULT_KEYWORDS, which is what every test above relies on.
    g = m.Group(username="u", directory=r"Audiobooks\X", free_slot=True,
               files=[{"filename": r"Audiobooks\X\Fire (Unabridged).m4b", "size": 300_000_000, "bitRate": 128}],
               total_size=300_000_000, exts={".m4b"})
    assert m.score_group(g, "Fire", "", PREFS) == m.score_group(g, "Fire", "", {**PREFS, "weights": {}})


def test_custom_weight_changes_ranking():
    # two m4b files, otherwise equal, differing only by format-order rank (m4b vs mp3
    # in the preferred list) — raising format_step should widen the gap.
    a = m.Group(username="a", directory=r"Audiobooks\Dark Matter", free_slot=True,
               files=[{"filename": r"Audiobooks\Dark Matter\Dark Matter - Blake Crouch.m4b",
                      "size": 300_000_000, "bitRate": 128}], total_size=300_000_000, exts={".m4b"})
    b = m.Group(username="b", directory=r"Audiobooks\Dark Matter", free_slot=True,
               files=[{"filename": r"Audiobooks\Dark Matter\Dark Matter - Blake Crouch.mp3",
                      "size": 300_000_000, "bitRate": 128}], total_size=300_000_000, exts={".mp3"})
    default_gap = (m.score_group(a, "Dark Matter", "Blake Crouch", PREFS)
                  - m.score_group(b, "Dark Matter", "Blake Crouch", PREFS))
    boosted = {**PREFS, "weights": {"format_step": 40}}
    boosted_gap = (m.score_group(a, "Dark Matter", "Blake Crouch", boosted)
                  - m.score_group(b, "Dark Matter", "Blake Crouch", boosted))
    assert boosted_gap > default_gap


def test_lowering_coverage_floor_admits_a_previously_rejected_candidate():
    # author is in the filename (has_author=True) so the separate generic-title
    # guard doesn't also reject this — isolates the coverage_floor knob.
    resp = [_resp("partial", True, [_f(r"x\Audiobooks\The Fellowship - Tolkien.m4b", 300_000_000)])]
    assert m.pick_best(resp, "The Fellowship of the Ring", "J.R.R. Tolkien", PREFS) is None
    lenient = {**PREFS, "weights": {"coverage_floor": 0.3}}
    best = m.pick_best(resp, "The Fellowship of the Ring", "J.R.R. Tolkien", lenient)
    assert best and best.username == "partial"


def test_custom_keyword_rejects_a_previously_accepted_candidate():
    resp = [_resp("g", True, [_f(
        r"x\Audiobooks\Rachel Reid - The Long Game\Rachel Reid - The Long Game.m4b", 250_000_000)])]
    assert m.pick_best(resp, "The Long Game", "Rachel Reid", PREFS) is not None
    # add a spam marker that happens to appear in this path
    custom = {**PREFS, "keywords": {"spam": ("rachel reid",)}}
    assert m.pick_best(resp, "The Long Game", "Rachel Reid", custom) is None


def test_partial_weight_override_keeps_other_defaults():
    # overriding one weight must not silently zero out the others
    g = m.Group(username="u", directory=r"Audiobooks\Dark Matter", free_slot=True,
               files=[{"filename": r"Audiobooks\Dark Matter\Dark Matter - Blake Crouch.m4b",
                      "size": 300_000_000, "bitRate": 128}], total_size=300_000_000, exts={".m4b"})
    only_author_changed = {**PREFS, "weights": {"author_bonus": 999}}
    score = m.score_group(g, "Dark Matter", "Blake Crouch", only_author_changed)
    # single-m4b bonus (default 20) must still have applied
    assert score >= 100 + 999 + 20 - 5   # loose bound; just proves other weights survived


def test_explain_breakdown_present_and_reject_reason_recorded():
    breakdown: list = []
    g = m.Group(username="u", directory=r"Audiobooks\Fire", free_slot=True,
               files=[{"filename": r"Audiobooks\Fire\Fire (Unabridged).m4b", "size": 300_000_000, "bitRate": 128}],
               total_size=300_000_000, exts={".m4b"})
    score = m.score_group(g, "Fire", "Kristin Cashore", PREFS, explain=breakdown)
    assert score == -1.0
    assert len(breakdown) == 1 and breakdown[0]["label"].startswith("Rejected:")

    breakdown2: list = []
    g2 = m.Group(username="u", directory=r"Audiobooks\Fire - Kristin Cashore", free_slot=True,
                files=[{"filename": r"Audiobooks\Fire - Kristin Cashore\Fire - Kristin Cashore.m4b",
                       "size": 300_000_000, "bitRate": 128}], total_size=300_000_000, exts={".m4b"})
    score2 = m.score_group(g2, "Fire", "Kristin Cashore", PREFS, explain=breakdown2)
    assert score2 > 0
    assert len(breakdown2) >= 3                        # title match + format + author, at least
    assert all("label" in e and "points" in e for e in breakdown2)


def test_weight_defaults_are_valid_for_their_own_html_number_constraints():
    # A default that isn't a multiple of its own step (from its own min) fails
    # native HTML5 <input type=number step=...> validation and SILENTLY blocks
    # the settings form from submitting at all (no error shown, no server request) —
    # this caught a real bug (bitrate_cap default 3.2 vs step 0.5) live in a browser.
    from decimal import Decimal
    for key, (_label, _help, lo, _hi, step) in m.WEIGHT_META.items():
        default = m.DEFAULT_WEIGHTS[key]
        remainder = (Decimal(str(default)) - Decimal(str(lo))) % Decimal(str(step))
        assert remainder == 0, f"{key}: default {default} is not a multiple of step {step} from min {lo}"
    # every preset's overrides must also land on a valid step, or applying a preset
    # then saving would hit the same silent-block bug.
    for name, overrides in m.PRESETS.items():
        for key, value in overrides.items():
            lo, _hi, step = m.WEIGHT_META[key][2], m.WEIGHT_META[key][3], m.WEIGHT_META[key][4]
            remainder = (Decimal(str(value)) - Decimal(str(lo))) % Decimal(str(step))
            assert remainder == 0, f"preset {name!r} {key}: {value} is not a multiple of step {step}"


def test_presets_apply_cleanly_over_defaults():
    for name, overrides in m.PRESETS.items():
        assert name in m.PRESET_LABELS
        merged = {**m.DEFAULT_WEIGHTS, **overrides}
        assert set(merged) == set(m.DEFAULT_WEIGHTS)   # no stray/unknown keys introduced
        for k in overrides:
            assert k in m.DEFAULT_WEIGHTS               # every override name is a real weight


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print(f"\n{len(fns)} passed")
