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
# markers of a dramatised / full-cast edition (normalised form, no punctuation)
DRAMATIZED = ("full cast", "dramatized", "dramatised", "multicast", "multi cast",
              "graphic audio", "graphicaudio", "radio drama", "audio drama")
# path/context signals to tell an audiobook from music or other audio
AUDIOBOOK_DIRS = {"audiobook", "audiobooks", "audible", "hoerbuch", "horbuch", "hoerbucher"}
AUDIOBOOK_MARKERS = ("unabridged", "audiobook", "narrated", "read by", "narrator")
MUSIC_DIRS = {"music", "album", "albums", "discography", "discographies", "songs",
              "singles", "soundtrack", "soundtracks", "remixes", "mp3s", "flacs"}


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
                siblings: Optional[list[frozenset]] = None) -> float:
    dirnorm = norm(g.directory)
    text = norm(g.directory + " " + " ".join(f["filename"] for f in g.files))
    want = tokens(title)
    if want:
        coverage = sum(1 for t in want if t in text) / len(want)
    else:
        coverage = 0.0
    if coverage < 0.6:
        return -1.0                                   # not confidently this book
    if any(s in text for s in SPAM if s != "abridged"):
        return -1.0
    # a DIFFERENT book in the same series (e.g. requested Mistborn bk1, this is
    # 'The Alloy of Law' bk4) — the title collides on the series name, so reject
    # when the files clearly name another entry.
    if siblings:
        toks = set(text.split())
        if any(sib and sib <= toks for sib in siblings):
            return -1.0

    surname = norm(author).split()[-1] if author else ""
    has_author = bool(surname and len(surname) >= 3 and surname in text)

    # --- content type: is this actually an audiobook, or music / something else? ---
    dirtokens = set(dirnorm.split())
    audiobook_ctx = bool(dirtokens & AUDIOBOOK_DIRS) or any(m in text for m in AUDIOBOOK_MARKERS)
    music_ctx = bool(dirtokens & MUSIC_DIRS) and not audiobook_ctx
    size_mb = g.total_size / 1_048_576
    n = len(g.files)
    avg_mb = size_mb / max(n, 1)
    # a pile of small tracks is an album, not an audiobook (chapters run long)
    looks_like_album = n >= 6 and avg_mb < 12
    if music_ctx and (looks_like_album or n <= 3):
        return -1.0                                   # clearly a song/album, not a book

    # Generic short titles ("Fire", "Game Changer", "Role Model", "The Long Game")
    # collide with songs and with same-named books by other authors. When the title
    # carries <=2 distinctive words, insist the requested author appears so we don't
    # grab a remix or the wrong writer's book.
    distinctive = [t for t in want if len(t) >= 3]
    if len(distinctive) <= 2 and surname and not has_author:
        return -1.0

    if size_mb < prefs["min_mb"] or size_mb > prefs["max_mb"]:
        return -1.0

    score = 100 * coverage
    # format preference (priority order -> descending bonus)
    order = prefs["formats"]
    best_ext = min((order.index(e[1:]) for e in g.exts if e[1:] in order), default=len(order))
    score += (len(order) - best_ext) * 8
    # a single m4b is the ideal audiobook artifact
    if g.exts == {".m4b"} and n == 1:
        score += 20
    if g.free_slot:
        score += 15
    elif prefs["require_free_slot"]:
        score -= 25
    if has_author:
        score += 10
    if audiobook_ctx:
        score += 8                                    # filed as / labelled an audiobook
    if music_ctx:
        score -= 40                                   # under a music path with no book markers
    if looks_like_album:
        score -= 20
    if "unabridged" in text:
        score += 6
    if "abridged" in text and "unabridged" not in text:
        score -= 15
    # mild preference for fewer files (cleaner) among multi-file sets
    score -= min(len(g.files), 40) * 0.1
    # tiebreaker: when everything else is close, prefer the higher-bitrate rip.
    # Capped at +3.2 so it only settles near-ties, never overriding a real signal
    # (a format/narrator/edition/free-slot difference is worth far more).
    score += min(avg_bitrate(g.files), 320) / 100.0

    # --- edition affinity: nudge toward the specific edition that was requested ---
    ed = edition or {}
    narrator = norm(ed.get("narrator") or "")
    nsurname = narrator.split()[-1] if narrator else ""
    if nsurname and len(nsurname) >= 3 and nsurname in text:
        score += 14                                  # this upload names the requested narrator
    yr = str(ed.get("year") or "")
    if re.fullmatch(r"\d{4}", yr) and yr in text:
        score += 8
    # dramatised/full-cast vs standard: don't grab the wrong kind of edition
    req_drama = any(m in norm(title) for m in DRAMATIZED)
    cand_drama = any(m in text for m in DRAMATIZED)
    if cand_drama and not req_drama:
        score -= 25                                  # a full-cast upload but a standard was requested
    elif req_drama and not cand_drama:
        score -= 12                                  # requested a full-cast edition; this looks standard
    # series position: prefer a file that names the requested book number, and
    # push down one that names a different number (e.g. "Book 5").
    bn = ed.get("book_number")
    if bn:
        if re.search(rf"\bbook\s+0*{bn}\b", text):
            score += 12
        else:
            other = re.search(r"\bbook\s+0*(\d{1,3})\b", text)
            if other and int(other.group(1)) != bn:
                score -= 15
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
    return {
        "formats": settings_module.get_list("preferred_formats") or ["m4b", "mp3"],
        "min_mb": settings_module.get_int("min_size_mb", 20),
        "max_mb": settings_module.get_int("max_size_mb", 4000),
        "require_free_slot": settings_module.get_bool("require_free_slot"),
    }
