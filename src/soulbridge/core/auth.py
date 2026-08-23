"""Authentication primitives: argon2 password hashing, session-user lookup,
login brute-force lockout, CSRF tokens, and the signing secret."""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

from .. import db

_ph = PasswordHasher()

LOCK_THRESHOLD = 6          # failed attempts before lockout
LOCK_MINUTES = 15
ROLES = ("admin", "trusted", "standard")


# ---- secret (session signing) ----
def get_secret() -> str:
    env = os.environ.get("SOULBRIDGE_SECRET")
    if env:
        return env
    path = os.path.join(db.CONFIG_DIR, "secret.key")
    try:
        with open(path) as f:
            s = f.read().strip()
            if s:
                return s
    except FileNotFoundError:
        pass
    s = secrets.token_hex(32)
    os.makedirs(db.CONFIG_DIR, exist_ok=True)
    with open(path, "w") as f:
        f.write(s)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return s


# ---- passwords ----
# A fixed hash to verify against when a login names a non-existent user, so the
# response takes the same time as a real (wrong-password) attempt — no timing oracle
# for username enumeration.
_DUMMY_HASH = _ph.hash("soulbridge-nonexistent-account")


def hash_pw(password: str) -> str:
    return _ph.hash(password)


def waste_time(password: str) -> None:
    """Spend the same effort as a real verify (for unknown-username logins)."""
    try:
        _ph.verify(_DUMMY_HASH, password or "")
    except Exception:
        pass


def verify_pw(password_hash: Optional[str], password: str) -> bool:
    if not password_hash:
        return False
    try:
        _ph.verify(password_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, Exception):
        return False


# ---- lockout ----
def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_locked(user: dict[str, Any]) -> bool:
    lu = user.get("locked_until")
    if not lu:
        return False
    try:
        return _now() < datetime.fromisoformat(lu)
    except ValueError:
        return False


def register_failure(user: dict[str, Any]) -> None:
    attempts = int(user.get("failed_attempts") or 0) + 1
    fields: dict[str, Any] = {"failed_attempts": attempts}
    if attempts >= LOCK_THRESHOLD:
        fields["locked_until"] = (_now() + timedelta(minutes=LOCK_MINUTES)).isoformat()
    db.update_user(user["id"], **fields)


def register_success(user: dict[str, Any]) -> None:
    db.update_user(user["id"], failed_attempts=0, locked_until=None,
                   last_login=_now().isoformat(timespec="seconds"))


# ---- session user ----
def current_user(request) -> Optional[dict[str, Any]]:
    uid = request.session.get("uid")
    if not uid:
        return None
    return db.get_user(uid)


def login_session(request, user: dict[str, Any]) -> None:
    request.session.clear()                       # regenerate on login (no fixation)
    request.session["uid"] = user["id"]
    request.session["csrf"] = secrets.token_urlsafe(32)


def logout_session(request) -> None:
    request.session.clear()


# ---- csrf ----
def csrf_token(request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session["csrf"] = tok
    return tok


def csrf_ok(request, submitted: Optional[str]) -> bool:
    expected = request.session.get("csrf")
    return bool(expected and submitted and secrets.compare_digest(expected, submitted))


# ---- roles ----
def is_admin(user: Optional[dict[str, Any]]) -> bool:
    return bool(user and user.get("role") == "admin")


def is_trusted(user: Optional[dict[str, Any]]) -> bool:
    return bool(user and user.get("role") in ("admin", "trusted"))
