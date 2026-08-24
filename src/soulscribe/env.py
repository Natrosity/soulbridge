"""Environment lookups with a backward-compatible fallback to the legacy
``SOULBRIDGE_*`` variable names used before the project was renamed to Soulscribe.

New deployments should use ``SOULSCRIBE_*``; existing ones keep working because
every read prefers the new name and falls back to the old one.
"""
from __future__ import annotations

import os
from typing import Optional


def env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read ``SOULSCRIBE_<key>``, falling back to legacy ``SOULBRIDGE_<key>``.

    ``key`` is the suffix only, e.g. ``env("CONFIG_DIR")`` reads
    ``SOULSCRIBE_CONFIG_DIR`` then ``SOULBRIDGE_CONFIG_DIR``.
    """
    val = os.environ.get(f"SOULSCRIBE_{key}")
    if val is not None:
        return val
    return os.environ.get(f"SOULBRIDGE_{key}", default)
