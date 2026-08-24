"""Query construction and result ranking — the part that turns a title+author
into a good Soulseek grab. Soulseek matches filename substrings and ANDs the
tokens, so shorter/cleaner queries beat 'title + full author'."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

AUDIO_EXTS = (".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus")
SPAM = ("sample", "summary", "workbook", "analysis of", "study guide", "abridged")
STOP = {"the", "a", "an", "of", "and", "to", "in", "my", "is", "for"}
# markers of a dramatised / full-cast edition (normalised form, no punctuation;
# includes German/Spanish/French terms since those editions are common)
DRAMATIZED = ("full cast", "fullcast", "dramatized", "dramatised", "dramatization",
              "dramatisation", "multicast", "multi cast", "graphic audio", "graphicaudio",
              "radio drama", "audio drama", "horspiel", "dramatizada", "dramatizado",
              "dramatisee", "vollvertont")
# path/context signals to tell an audiobook from music or other audio
AUDIOBOOK_DIRS = {"audiobook", "audiobooks", "audible", "hoerbuch", "horbuch", "hoerbucher"}
AUDIOBOOK_MARKERS = ("unabridged", "audiobook", "narrated", "read by", "narrator")
MUSIC_DIRS = {"music", "album", "albums", "discography", "discographies", "songs",
              "singles", "soundtrack", "soundtracks", "remixes", "mp3s", "flacs"}

# ---------------------------------------------------------------------------
# Tunable ranking weights. These are the only numbers score_group() reads for
# its bonuses/penalties/thresholds — everything else (grouping, hard content-
# type structure) is fixed logic. Exposed in Server Settings > Matching so an
# operator can retune without editing code; DEFAULT_WEIGHTS are today's values,
# i.e. behaviour is unchanged until someone edits a setting.
# ---------------------------------------------------------------------------
DEFAULT_WEIGHTS: dict[str, float] = {
    "coverage_floor": 0.6,              # min fraction of title words that must appear
    "format_step": 8,                   # points per rank in the preferred-format order
    "single_m4b_bonus": 20,             # a lone .m4b file
    "free_slot_bonus": 15,
    "free_slot_penalty": 25,            # when require_free_slot is on and there's no slot
    "author_bonus": 10,
    "audiobook_ctx_bonus": 8,
    "music_penalty": 40,
    "album_penalty": 20,
    "unabridged_bonus": 6,
    "abridged_penalty": 15,
    "narrator_bonus": 14,
    "year_bonus": 8,
    "dramatised_mismatch_penalty": 25,  # candidate is dramatised, request wasn't
    "dramatised_missing_penalty": 12,   # request is dramatised, candidate looks standard
    "book_number_bonus": 12,
    "book_number_penalty": 15,
    "bitrate_divisor": 100,             # avg kbps / this, capped at bitrate_cap — a tiebreaker
    "bitrate_cap": 3.2,
    "generic_title_max_words": 2,       # <= this many distinctive words requires the author
}

# Weight metadata for the settings UI: (label, help, min, max, step). Dict order
# is the display order.
WEIGHT_META: dict[str, tuple[str, str, float, float, float]] = {
    "coverage_floor": ("Minimum title match", "Fraction of the title's distinctive words "
                       "that must appear in a candidate (0-1). Lower finds more matches but "
                       "raises the risk of a wrong one.", 0.2, 0.9, 0.05),
    "format_step": ("Format preference strength", "Points per rank in your preferred-format "
                    "order (e.g. m4b before mp3).", 0, 20, 1),
    "single_m4b_bonus": ("Single-file M4B bonus", "Extra points for a lone .m4b file — the "
                        "cleanest audiobook artifact.", 0, 40, 1),
    "free_slot_bonus": ("Free upload slot bonus", "Points for a source with a free Soulseek "
                        "upload slot.", 0, 30, 1),
    "free_slot_penalty": ("No free slot penalty", "Points lost when 'require a free slot' is "
                          "on and this source has none.", 0, 50, 1),
    "author_bonus": ("Author match bonus", "Points when the requested author's surname "
                     "appears in the filenames.", 0, 25, 1),
    "audiobook_ctx_bonus": ("Audiobook context bonus", "Points when the path/filenames look "
                            "like an audiobook (folder name, 'unabridged', etc).", 0, 20, 1),
    "music_penalty": ("Music mismatch penalty", "Points lost when a source looks like a "
                      "music album with no audiobook markers.", 0, 80, 1),
    "album_penalty": ("Album-shape penalty", "Extra points lost for many small tracks (an "
                      "album shape) with no audiobook markers.", 0, 40, 1),
    "unabridged_bonus": ("Unabridged bonus", "Points for a source explicitly marked "
                         "unabridged.", 0, 15, 1),
    "abridged_penalty": ("Abridged penalty", "Points lost when a source is marked abridged "
                         "(and not also unabridged).", 0, 30, 1),
    "narrator_bonus": ("Narrator match bonus", "Points when the requested edition's narrator "
                       "appears in the filenames.", 0, 30, 1),
    "year_bonus": ("Release year match bonus", "Points when the requested edition's year "
                   "appears in the filenames.", 0, 20, 1),
    "dramatised_mismatch_penalty": ("Unwanted dramatised penalty", "Points lost when a "
                                    "source is a full-cast/dramatised edition but a standard "
                                    "one was requested.", 0, 50, 1),
    "dramatised_missing_penalty": ("Missing dramatised penalty", "Points lost when a "
                                   "dramatised edition was requested but a source looks "
                                   "standard.", 0, 30, 1),
    "book_number_bonus": ("Series position match bonus", "Points when a source names the "
                          "requested book number in a series.", 0, 25, 1),
    "book_number_penalty": ("Wrong series position penalty", "Points lost when a source "
                            "clearly names a different book number.", 0, 30, 1),
    "bitrate_divisor": ("Bitrate tiebreaker divisor", "Average kbps divided by this becomes "
                        "a small tiebreaker bonus (capped below). Lower means bitrate "
                        "matters more.", 20, 400, 5),
    "bitrate_cap": ("Bitrate tiebreaker cap", "Maximum points the bitrate tiebreaker can "
                    "contribute — kept small so it only settles near-ties.", 0, 15, 0.1),
    "generic_title_max_words": ("Generic-title guard", "A title with this many or fewer "
                                "distinctive words requires the author to match (stops "
                                "'Fire' grabbing a remix).", 0, 5, 1),
}

# Tunable keyword lists, keyed the same as their settings field suffix
# (keyword_<key>). Defaults are today's fixed tuples above.
DEFAULT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "spam": SPAM,
    "dramatized": DRAMATIZED,
    "music_dirs": tuple(sorted(MUSIC_DIRS)),
    "audiobook_markers": AUDIOBOOK_MARKERS,
}

# Curated weight presets (partial overrides merged onto DEFAULT_WEIGHTS) for the
# settings UI, so tuning doesn't require understanding individual point values.
PRESETS: dict[str, dict[str, float]] = {
    "balanced": {},                     # today's defaults — also the reset-to-defaults choice
    "single_m4b": {
        "format_step": 14, "single_m4b_bonus": 40, "album_penalty": 30,
    },
    "bitrate": {
        "bitrate_divisor": 40, "bitrate_cap": 10,
    },
    "lenient": {
        "coverage_floor": 0.45, "generic_title_max_words": 1,
        "music_penalty": 25, "album_penalty": 10,
    },
}
PRESET_LABELS: dict[str, str] = {
    "balanced": "Balanced (defaults)",
    "single_m4b": "Prefer single M4B",
    "bitrate": "Prefer highest bitrate",
    "lenient": "Lenient (rare titles)",
}


def _weights(prefs: dict[str, Any]) -> dict[str, float]:
    w = prefs.get("weights")
    return {**DEFAULT_WEIGHTS, **w} if w else DEFAULT_WEIGHTS


def _keywords(prefs: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    k = prefs.get("keywords")
    return {**DEFAULT_KEYWORDS, **k} if k else DEFAULT_KEYWORDS


# ligatures / special letters that NFKD does not decompose to ASCII
_LIGATURES = str.maketrans({"æ": "ae", "œ": "oe", "ø": "o", "ð": "d", "þ": "th",
                            "ß": "ss", "ł": "l", "đ": "d", "ĳ": "ij"})


def norm(s: str) -> str:
    s = (s or "").lower().replace("'", "").replace("’", "").translate(_LIGATURES)
    # fold accents to ASCII so "recursión" -> "recursion" (how Soulseek indexes it)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def tokens(s: str) -> list[str]:
    return [t for t in norm(s).split() if t and t not in STOP]


def build_queries(title: str, author: str) -> list[str]:
    """Ordered candidate search strings, most-likely-to-match first.

    Soulseek ANDs search tokens as substrings against raw filenames, so short
    contraction remnants hurt: "I'm" normalises to "im", which does NOT match a
    file literally named "I'm ...". Dropping tokens shorter than 3 chars ("im",
    "my", "a") yields the clean, distinctive query that actually hits."""
    full = norm(title)
    sig = " ".join(w for w in full.split() if len(w) >= 3)
    surname = norm(author).split()[-1] if author else ""
    q: list[str] = []
    if sig:
        q.append(sig)                                 # distinctive words only — best recall
    if full and full != sig:
        q.append(full)                                # precise fallback
    if sig and surname and surname not in sig:
        q.append(f"{sig} {surname}")                  # disambiguate with surname
    if not sig and surname:
        q.append(f"{full} {surname}".strip())         # very short title -> lean on author
    # de-dupe, preserve order
    seen: set[str] = set()
    out = []
    for s in q:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def book_number(subtitle: str) -> Optional[int]:
    """Extract a series position from a subtitle like 'Mistborn, Book 1' -> 1."""
    if not subtitle:
        return None
    m = re.search(r"\bbook\s+(\d{1,3})\b", norm(subtitle))
    return int(m.group(1)) if m else None


def build_siblings(results: list[dict[str, Any]], asin: str, title: str) -> list[frozenset]:
    """Given Audible results for a series, return the distinctive token-sets of the
    OTHER books (a different series position) so the matcher can reject them. The
    requested book's own words and the series name are stripped out."""
    me = next((r for r in results if r.get("asin") == asin), None)
    if not me:
        return []
    my_num = book_number(me.get("subtitle"))
    if my_num is None:
        return []                                    # can't place the requested book — don't guess
    common = set(tokens(title)) | set(tokens(me.get("subtitle") or "")) | set(tokens(me.get("series") or ""))
    sibs: list[frozenset] = []
    for r in results:
        if r.get("asin") == asin:
            continue
        num = book_number(r.get("subtitle"))
        if num is None or num == my_num:
            continue                                 # unknown or same position — not a distinct sibling
        toks = frozenset(t for t in tokens(r.get("title") or "")
                         if t not in common and len(t) >= 3)
        if toks:
            sibs.append(toks)
    return sibs


def avg_bitrate(files: list[dict[str, Any]]) -> float:
    """Mean bitrate (kbps) across a group's files, 0 when unknown."""
    brs = [f.get("bitRate") for f in files if f.get("bitRate")]
    return sum(brs) / len(brs) if brs else 0.0


def build_editions(results: list[dict[str, Any]], asin: str, title: str) -> list[dict[str, Any]]:
    """Other Audible listings that are the SAME book as `asin` (same title + series
    position) but a different edition — i.e. a different narrator/year/ASIN. Used to
    recognise which edition a download actually is."""
    me = next((r for r in results if r.get("asin") == asin), None)
    if not me:
        return []
    my_title = norm(me.get("title") or title)
    my_num = book_number(me.get("subtitle"))
    alts = []
    for r in results:
        if r.get("asin") == asin or not r.get("asin"):
            continue
        if norm(r.get("title") or "") == my_title and book_number(r.get("subtitle")) == my_num:
            alts.append({"asin": r["asin"], "narrators": r.get("narrators") or [],
                         "year": str(r.get("year") or "") or None, "title": r.get("title")})
    return alts


def surname(name: str) -> str:
    parts = norm(name or "").split()
    return parts[-1] if parts else ""


def year4(s: str) -> str:
    m = re.search(r"\d{4}", str(s or ""))
    return m.group(0) if m else ""


def pick_edition(file_narr: str, file_year: str, target: dict[str, Any],
                 alternates: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Given the downloaded file's own narrator/year tags, return the alternate
    edition it actually is — or None to keep the requested (target) edition.
    Conservative: only switches on a confident narrator match (or a unique year)."""
    fn, fy = surname(file_narr), year4(file_year)
    if not fn and not fy:
        return None

    def nmatch(ed):
        return bool(fn) and any(fn == surname(n) for n in ed.get("narrators") or [])

    def ymatch(ed):
        return bool(fy) and year4(ed.get("year") or "") == fy

    if (fn and nmatch(target)) or (not fn and ymatch(target)):
        return None                                  # the file matches what we asked for
    if fn:                                            # narrator differs from target — find its edition
        for ed in alternates:
            if nmatch(ed):
                return ed
    if fy and not ymatch(target):                     # no narrator signal: fall back to a unique year
        yalts = [ed for ed in alternates if ymatch(ed)]
        if len(yalts) == 1:
            return yalts[0]
    return None


def ext_of(path: str) -> str:
    m = re.search(r"(\.[a-z0-9]+)$", path.lower())
    return m.group(1) if m else ""


def _dir_of(remote_path: str) -> str:
    # slskd remote paths are Windows-style (backslash separated)
    return remote_path.rsplit("\\", 1)[0] if "\\" in remote_path else ""


@dataclass
class Group:
    username: str
    directory: str
    free_slot: bool
    files: list[dict[str, Any]] = field(default_factory=list)
    total_size: int = 0
    exts: set[str] = field(default_factory=set)
    score: float = 0.0

    @property
    def label(self) -> str:
        base = self.directory.rsplit("\\", 1)[-1] if self.directory else ""
        if not base and self.files:
            base = self.files[0]["filename"].rsplit("\\", 1)[-1]
        return base


def group_responses(responses: list[dict[str, Any]]) -> list[Group]:
    """Group audio files into (user, directory) bundles — one bundle == one book."""
    groups: dict[tuple[str, str], Group] = {}
    for r in responses:
        user = r.get("username", "")
        free = bool(r.get("hasFreeUploadSlot"))
        for f in r.get("files", []):
            fn = f.get("filename", "")
            if ext_of(fn) not in AUDIO_EXTS:
                continue
            key = (user, _dir_of(fn))
            g = groups.get(key)
            if g is None:
                g = Group(username=user, directory=_dir_of(fn), free_slot=free)
                groups[key] = g
            g.files.append({"filename": fn, "size": int(f.get("size", 0)),
                            "bitRate": f.get("bitRate")})
            g.total_size += int(f.get("size", 0))
            g.exts.add(ext_of(fn))
    return list(groups.values())


def score_group(g: Group, title: str, author: str, prefs: dict[str, Any],
                edition: Optional[dict[str, Any]] = None,
                siblings: Optional[list[frozenset]] = None,
                explain: Optional[list[dict[str, Any]]] = None) -> float:
    """Score one candidate group. When `explain` is a list, every scoring step
    (and the reason for a reject) is appended to it as {"label", "points"} —
    used by the interactive picker to show why a source scored what it did."""
    w = _weights(prefs)
    kw = _keywords(prefs)
    spam, dramatized = kw["spam"], kw["dramatized"]
    music_dirs, audiobook_markers = set(kw["music_dirs"]), kw["audiobook_markers"]

    def reject(reason: str) -> float:
        if explain is not None:
            explain.append({"label": f"Rejected: {reason}", "points": None})
        return -1.0

    def note(label: str, points: float) -> None:
        if explain is not None and points:
            explain.append({"label": label, "points": round(points, 1)})

    dirnorm = norm(g.directory)
    text = norm(g.directory + " " + " ".join(f["filename"] for f in g.files))
    want = tokens(title)
    if want:
        coverage = sum(1 for t in want if t in text) / len(want)
    else:
        coverage = 0.0
    if coverage < w["coverage_floor"]:
        return reject(f"title match too low ({round(coverage * 100)}%)")
    if any(s in text for s in spam if s != "abridged"):
        return reject("looks like a sample/summary/workbook")
    # a DIFFERENT book in the same series (e.g. requested Mistborn bk1, this is
    # 'The Alloy of Law' bk4) — the title collides on the series name, so reject
    # when the files clearly name another entry.
    if siblings:
        toks = set(text.split())
        if any(sib and sib <= toks for sib in siblings):
            return reject("names a different book in the series")

    surname = norm(author).split()[-1] if author else ""
    has_author = bool(surname and len(surname) >= 3 and surname in text)

    # --- content type: is this actually an audiobook, or music / something else? ---
    dirtokens = set(dirnorm.split())
    audiobook_ctx = bool(dirtokens & AUDIOBOOK_DIRS) or any(m in text for m in audiobook_markers)
    music_ctx = bool(dirtokens & music_dirs) and not audiobook_ctx
    size_mb = g.total_size / 1_048_576
    n = len(g.files)
    avg_mb = size_mb / max(n, 1)
    # a pile of small tracks is an album, not an audiobook (chapters run long)
    looks_like_album = n >= 6 and avg_mb < 12
    if music_ctx and (looks_like_album or n <= 3):
        return reject("looks like music, not an audiobook")

    # Generic short titles ("Fire", "Game Changer", "Role Model", "The Long Game")
    # collide with songs and with same-named books by other authors. When the title
    # carries few distinctive words, insist the requested author appears so we don't
    # grab a remix or the wrong writer's book.
    distinctive = [t for t in want if len(t) >= 3]
    if len(distinctive) <= w["generic_title_max_words"] and surname and not has_author:
        return reject("generic title without the requested author")

    if size_mb < prefs["min_mb"] or size_mb > prefs["max_mb"]:
        return reject(f"size {round(size_mb)}MB outside the allowed range")

    score = 100 * coverage
    note(f"Title match ({round(coverage * 100)}%)", score)
    # format preference (priority order -> descending bonus)
    order = prefs["formats"]
    best_ext = min((order.index(e[1:]) for e in g.exts if e[1:] in order), default=len(order))
    fmt_pts = (len(order) - best_ext) * w["format_step"]
    score += fmt_pts
    note("Preferred format", fmt_pts)
    # a single m4b is the ideal audiobook artifact
    if g.exts == {".m4b"} and n == 1:
        score += w["single_m4b_bonus"]
        note("Single M4B file", w["single_m4b_bonus"])
    if g.free_slot:
        score += w["free_slot_bonus"]
        note("Free upload slot", w["free_slot_bonus"])
    elif prefs["require_free_slot"]:
        score -= w["free_slot_penalty"]
        note("No free upload slot", -w["free_slot_penalty"])
    if has_author:
        score += w["author_bonus"]
        note("Author match", w["author_bonus"])
    if audiobook_ctx:
        score += w["audiobook_ctx_bonus"]             # filed as / labelled an audiobook
        note("Looks like an audiobook", w["audiobook_ctx_bonus"])
    if music_ctx:
        score -= w["music_penalty"]                   # under a music path with no book markers
        note("Looks like music", -w["music_penalty"])
    if looks_like_album:
        score -= w["album_penalty"]
        note("Looks like an album", -w["album_penalty"])
    if "unabridged" in text:
        score += w["unabridged_bonus"]
        note("Unabridged", w["unabridged_bonus"])
    if "abridged" in text and "unabridged" not in text:
        score -= w["abridged_penalty"]
        note("Abridged", -w["abridged_penalty"])
    # mild preference for fewer files (cleaner) among multi-file sets
    score -= min(len(g.files), 40) * 0.1
    # tiebreaker: when everything else is close, prefer the higher-bitrate rip.
    # Capped small so it only settles near-ties, never overriding a real signal
    # (a format/narrator/edition/free-slot difference is worth far more).
    br_pts = min(avg_bitrate(g.files) / w["bitrate_divisor"], w["bitrate_cap"])
    score += br_pts
    note("Higher bitrate", br_pts)

    # --- edition affinity: nudge toward the specific edition that was requested ---
    ed = edition or {}
    narrator = norm(ed.get("narrator") or "")
    nsurname = narrator.split()[-1] if narrator else ""
    if nsurname and len(nsurname) >= 3 and nsurname in text:
        score += w["narrator_bonus"]                  # this upload names the requested narrator
        note("Requested narrator", w["narrator_bonus"])
    yr = str(ed.get("year") or "")
    if re.fullmatch(r"\d{4}", yr) and yr in text:
        score += w["year_bonus"]
        note("Requested year", w["year_bonus"])
    # dramatised/full-cast vs standard: don't grab the wrong kind of edition
    req_drama = any(m in norm(title) for m in dramatized)
    cand_drama = any(m in text for m in dramatized)
    if cand_drama and not req_drama:
        score -= w["dramatised_mismatch_penalty"]     # full-cast upload but a standard was requested
        note("Unwanted dramatised edition", -w["dramatised_mismatch_penalty"])
    elif req_drama and not cand_drama:
        score -= w["dramatised_missing_penalty"]      # requested full-cast; this looks standard
        note("Missing dramatised edition", -w["dramatised_missing_penalty"])
    # series position: prefer a file that names the requested book number, and
    # push down one that names a different number (e.g. "Book 5").
    bn = ed.get("book_number")
    if bn:
        if re.search(rf"\bbook\s+0*{bn}\b", text):
            score += w["book_number_bonus"]
            note("Requested series position", w["book_number_bonus"])
        else:
            other = re.search(r"\bbook\s+0*(\d{1,3})\b", text)
            if other and int(other.group(1)) != bn:
                score -= w["book_number_penalty"]
                note("Different series position", -w["book_number_penalty"])
    return score


def pick_best(responses: list[dict[str, Any]], title: str, author: str,
              prefs: dict[str, Any], edition: Optional[dict[str, Any]] = None,
              blocked: Optional[set] = None,
              siblings: Optional[list[frozenset]] = None) -> Optional[Group]:
    blocked = blocked or set()
    groups = [g for g in group_responses(responses)
              if (g.username, g.directory) not in blocked]
    for g in groups:
        g.score = score_group(g, title, author, prefs, edition, siblings)
    ranked = sorted((g for g in groups if g.score > 0), key=lambda g: g.score, reverse=True)
    return ranked[0] if ranked else None


def default_prefs(settings_module) -> dict[str, Any]:
    weights = {k: settings_module.get_float(f"weight_{k}", DEFAULT_WEIGHTS[k]) for k in DEFAULT_WEIGHTS}
    keywords = {k: tuple(settings_module.get_list(f"keyword_{k}")) or default
               for k, default in DEFAULT_KEYWORDS.items()}
    return {
        "formats": settings_module.get_list("preferred_formats") or ["m4b", "mp3"],
        "min_mb": settings_module.get_int("min_size_mb", 20),
        "max_mb": settings_module.get_int("max_size_mb", 4000),
        "require_free_slot": settings_module.get_bool("require_free_slot"),
        "weights": weights,
        "keywords": keywords,
    }
