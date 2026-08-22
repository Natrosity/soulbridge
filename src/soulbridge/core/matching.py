"""Query construction and result ranking — the part that turns a title+author
into a good Soulseek grab. Soulseek matches filename substrings and ANDs the
tokens, so shorter/cleaner queries beat 'title + full author'."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

AUDIO_EXTS = (".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus")
SPAM = ("sample", "summary", "workbook", "analysis of", "study guide", "abridged")
STOP = {"the", "a", "an", "of", "and", "to", "in", "my", "is", "for"}


def norm(s: str) -> str:
    s = (s or "").lower().replace("'", "").replace("’", "")
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


def score_group(g: Group, title: str, author: str, prefs: dict[str, Any]) -> float:
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
    # Generic one-word titles ("Fire", "It", "1984") match far too much — a bare
    # "fire" hits music, games, anything. When the title carries <=1 distinctive
    # word, insist the author's surname appears so we don't grab the wrong thing.
    surname = norm(author).split()[-1] if author else ""
    distinctive = [t for t in want if len(t) >= 3]
    if len(distinctive) <= 1 and surname and surname not in text:
        return -1.0
    size_mb = g.total_size / 1_048_576
    if size_mb < prefs["min_mb"] or size_mb > prefs["max_mb"]:
        return -1.0

    score = 100 * coverage
    # format preference (priority order -> descending bonus)
    order = prefs["formats"]
    best_ext = min((order.index(e[1:]) for e in g.exts if e[1:] in order), default=len(order))
    score += (len(order) - best_ext) * 8
    # a single m4b is the ideal audiobook artifact
    if g.exts == {".m4b"} and len(g.files) == 1:
        score += 20
    if g.free_slot:
        score += 15
    elif prefs["require_free_slot"]:
        score -= 25
    surname = norm(author).split()[-1] if author else ""
    if surname and surname in text:
        score += 10
    if "unabridged" in text:
        score += 6
    if "abridged" in text and "unabridged" not in text:
        score -= 15
    # mild preference for fewer files (cleaner) among multi-file sets
    score -= min(len(g.files), 40) * 0.1
    return score


def pick_best(responses: list[dict[str, Any]], title: str, author: str,
              prefs: dict[str, Any]) -> Optional[Group]:
    groups = group_responses(responses)
    for g in groups:
        g.score = score_group(g, title, author, prefs)
    ranked = sorted((g for g in groups if g.score > 0), key=lambda g: g.score, reverse=True)
    return ranked[0] if ranked else None


def default_prefs(settings_module) -> dict[str, Any]:
    return {
        "formats": settings_module.get_list("preferred_formats") or ["m4b", "mp3"],
        "min_mb": settings_module.get_int("min_size_mb", 20),
        "max_mb": settings_module.get_int("max_size_mb", 4000),
        "require_free_slot": settings_module.get_bool("require_free_slot"),
    }
