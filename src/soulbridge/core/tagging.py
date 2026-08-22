"""Write Audible metadata onto downloaded audio files.

The merge is conservative: always fill empty tags, and only replace an existing
tag when the new value is a strict superset of the old one (i.e. it just
reorganises / enriches the same information). If the existing tag has words the
new value lacks, or the two disagree, keep what's there. Every decision is
recorded so it can be shown on the Tags page.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from mutagen.flac import FLAC
from mutagen.id3 import (APIC, COMM, ID3, MVIN, MVNM, TALB, TCOM, TCON, TDRC,
                         TIT2, TPE1, TPE2, TXXX)
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis

AUDIO_EXTS = (".mp3", ".m4b", ".m4a", ".flac", ".ogg", ".opus")

# logical field -> human label shown on the Tags page
LABELS = {
    "title": "Title", "album": "Album", "artist": "Author", "albumartist": "Album artist",
    "composer": "Narrator", "year": "Year", "genre": "Genre", "comment": "Description",
    "series": "Series", "series_part": "Series #", "asin": "ASIN",
}
ORDER = list(LABELS.keys())


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def decide(old: str, new: str) -> tuple[str, str]:
    """Return (final_value, action). action in write|overwrite|keep|unchanged."""
    old, new = (old or "").strip(), (new or "").strip()
    if not new:
        return old, "unchanged"
    if not old:
        return new, "write"
    if _norm(old) == _norm(new):
        return old, "unchanged"
    ot, nt = set(_norm(old).split()), set(_norm(new).split())
    if ot and ot <= nt:               # new contains everything old had -> enrich
        return new, "overwrite"
    return old, "keep"                 # old has extra info or contradicts -> keep


def build_logical(meta: dict[str, Any]) -> dict[str, str]:
    def j(xs):
        return ", ".join([x for x in (xs or []) if x])
    return {
        "title": meta.get("title") or "",
        "album": meta.get("title") or "",
        "artist": j(meta.get("authors")),
        "albumartist": j(meta.get("authors")),
        "composer": j(meta.get("narrators")),
        "year": meta.get("year") or "",
        "genre": j((meta.get("genres") or [])[:3]),
        "comment": meta.get("description") or "",
        "series": meta.get("series") or "",
        "series_part": meta.get("series_position") or "",
        "asin": meta.get("asin") or "",
    }


# --------------------------------------------------------------------------
# Per-format adapters: get(field) -> current string, put(field, value), cover, save
# --------------------------------------------------------------------------
class _Mp3:
    def __init__(self, path: str):
        self.a = MP3(path)
        if self.a.tags is None:
            self.a.add_tags()
        self.t: ID3 = self.a.tags

    def _txxx(self, desc: str) -> str:
        fr = self.t.get(f"TXXX:{desc}")
        return str(fr.text[0]) if fr and fr.text else ""

    def get(self, f: str) -> str:
        simple = {"title": "TIT2", "album": "TALB", "artist": "TPE1",
                  "albumartist": "TPE2", "composer": "TCOM", "year": "TDRC", "genre": "TCON"}
        if f in simple:
            fr = self.t.get(simple[f])
            return str(fr.text[0]) if fr and fr.text else ""
        if f == "comment":
            for k, v in self.t.items():
                if k.startswith("COMM"):
                    return str(v.text[0]) if v.text else ""
            return ""
        if f == "series":
            return self._txxx("SERIES") or (str(self.t.get("MVNM").text[0]) if self.t.get("MVNM") else "")
        if f == "series_part":
            return self._txxx("SERIES-PART") or (str(self.t.get("MVIN").text[0]) if self.t.get("MVIN") else "")
        if f == "asin":
            return self._txxx("ASIN")
        return ""

    def put(self, f: str, v: str) -> None:
        frames = {"title": TIT2, "album": TALB, "artist": TPE1, "albumartist": TPE2,
                  "composer": TCOM, "year": TDRC, "genre": TCON}
        if f in frames:
            self.t.setall(frames[f].__name__, [frames[f](encoding=3, text=[v])])
        elif f == "comment":
            self.t.delall("COMM")
            self.t.add(COMM(encoding=3, lang="eng", desc="", text=[v]))
        elif f == "series":
            self.t.delall("TXXX:SERIES"); self.t.add(TXXX(encoding=3, desc="SERIES", text=[v]))
            self.t.setall("MVNM", [MVNM(encoding=3, text=[v])])
        elif f == "series_part":
            self.t.delall("TXXX:SERIES-PART"); self.t.add(TXXX(encoding=3, desc="SERIES-PART", text=[v]))
            self.t.setall("MVIN", [MVIN(encoding=3, text=[v])])
        elif f == "asin":
            self.t.delall("TXXX:ASIN"); self.t.add(TXXX(encoding=3, desc="ASIN", text=[v]))

    def has_cover(self) -> bool:
        return any(k.startswith("APIC") for k in self.t.keys())

    def set_cover(self, data: bytes, mime: str) -> None:
        self.t.delall("APIC")
        self.t.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))

    def save(self) -> None:
        self.a.save()


class _Mp4:
    FREE = "----:com.apple.iTunes:"
    MAP = {"title": "\xa9nam", "album": "\xa9alb", "artist": "\xa9ART", "albumartist": "aART",
           "composer": "\xa9wrt", "year": "\xa9day", "genre": "\xa9gen", "comment": "\xa9cmt",
           "series": "\xa9mvn"}

    def __init__(self, path: str):
        self.a = MP4(path)
        if self.a.tags is None:
            self.a.add_tags()
        self.t = self.a.tags

    def get(self, f: str) -> str:
        if f == "series_part":
            v = self.t.get("\xa9mvi")
            return str(v[0]) if v else ""
        if f == "asin":
            v = self.t.get(self.FREE + "ASIN")
            return v[0].decode("utf-8", "ignore") if v else ""
        key = self.MAP.get(f)
        if not key:
            return ""
        v = self.t.get(key)
        if not v:
            return ""
        x = v[0]
        return x.decode("utf-8", "ignore") if isinstance(x, bytes) else str(x)

    def put(self, f: str, v: str) -> None:
        if f == "series_part":
            try:
                self.t["\xa9mvi"] = [int(re.sub(r"[^0-9]", "", v) or 0)]
            except Exception:
                pass
            return
        if f == "asin":
            self.t[self.FREE + "ASIN"] = [v.encode("utf-8")]
            return
        key = self.MAP.get(f)
        if key:
            self.t[key] = [v]
        if f == "series":
            self.t[self.FREE + "SERIES"] = [v.encode("utf-8")]

    def has_cover(self) -> bool:
        return bool(self.t.get("covr"))

    def set_cover(self, data: bytes, mime: str) -> None:
        fmt = MP4Cover.FORMAT_PNG if "png" in mime else MP4Cover.FORMAT_JPEG
        self.t["covr"] = [MP4Cover(data, imageformat=fmt)]

    def save(self) -> None:
        self.t["stik"] = [2]   # media type: audiobook
        self.a.save()


class _Vorbis:
    KEY = {"title": "TITLE", "album": "ALBUM", "artist": "ARTIST", "albumartist": "ALBUMARTIST",
           "composer": "COMPOSER", "year": "DATE", "genre": "GENRE", "comment": "DESCRIPTION",
           "series": "SERIES", "series_part": "SERIESPART", "asin": "ASIN"}

    def __init__(self, path: str, flac: bool):
        self.flac = flac
        self.a = FLAC(path) if flac else OggVorbis(path)

    def get(self, f: str) -> str:
        v = self.a.get(self.KEY[f])
        return v[0] if v else ""

    def put(self, f: str, v: str) -> None:
        self.a[self.KEY[f]] = [v]

    def has_cover(self) -> bool:
        return bool(getattr(self.a, "pictures", None)) if self.flac else False

    def set_cover(self, data: bytes, mime: str) -> None:
        if self.flac:
            from mutagen.flac import Picture
            pic = Picture(); pic.type = 3; pic.mime = mime; pic.data = data
            self.a.clear_pictures(); self.a.add_picture(pic)

    def save(self) -> None:
        self.a.save()


def _adapter(path: str):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3":
        return _Mp3(path)
    if ext in (".m4b", ".m4a"):
        return _Mp4(path)
    if ext == ".flac":
        return _Vorbis(path, flac=True)
    if ext in (".ogg", ".opus"):
        return _Vorbis(path, flac=False)
    return None


def write_file(path: str, meta: dict[str, Any], cover: Optional[bytes], cover_mime: str,
               overwrite: bool, embed_cover: bool) -> list[dict[str, str]]:
    """Tag one file. Returns the per-field decision list for the Tags page."""
    ad = _adapter(path)
    if ad is None:
        return []
    logical = build_logical(meta)
    decisions: list[dict[str, str]] = []
    for f in ORDER:
        new = logical.get(f, "")
        if not new:
            continue
        old = ad.get(f)
        final, action = decide(old, new)
        if not overwrite and action == "overwrite":
            final, action = old, "keep"       # user chose gap-fill only
        if action in ("write", "overwrite"):
            ad.put(f, final)
        if action in ("write", "overwrite", "keep"):
            decisions.append({"name": LABELS[f], "old": old, "new": new, "action": action})
    if embed_cover and cover:
        if overwrite or not ad.has_cover():
            ad.set_cover(cover, cover_mime)
            decisions.append({"name": "Cover", "old": "", "new": "embedded", "action": "write"})
    ad.save()
    return decisions
