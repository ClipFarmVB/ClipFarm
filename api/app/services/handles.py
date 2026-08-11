"""Username (handle) validation for public profiles (CF-107).

Handles are the app's first piece of user-chosen public identity, so the rules
here are deliberately conservative: easy to widen later, painful to narrow once
people own handles that a stricter rule would reject.
"""
import re

# Lower-case ASCII, digits and underscore. No dots or hyphens: they invite
# look-alike impersonation (`john.smith` vs `john-smith`) and complicate using
# the handle in a URL path segment.
_HANDLE_RE = re.compile(r"^[a-z0-9_]{3,30}$")

MIN_LENGTH = 3
MAX_LENGTH = 30

# Reserved handles. Two groups, both real collision risks rather than a
# generic blocklist:
#
#   1. Route names — a profile served at /u/<handle> is safe today, but a
#      handle equal to a top-level route would collide the moment anyone wants
#      vanity URLs at the root (/<handle>), which is the normal end state. The
#      list mirrors web/src/app/* and the API router prefixes; keep it in sync.
#   2. Impersonation bait — official-sounding names that shouldn't be claimable
#      by a random signup.
_ROUTE_NAMES = {
    # web/src/app
    "auth", "collections", "debug", "games", "login", "signup", "upload",
    "feed", "explore", "search", "settings", "notifications", "u",
    # api router prefixes
    "corrections", "players", "clips", "health", "healthz", "monitoring",
    # conventional
    "api", "www", "static", "assets", "public", "favicon",
}

_IMPERSONATION = {
    "admin", "administrator", "root", "system", "support", "help", "billing",
    "security", "moderator", "mod", "staff", "team", "official", "clipfarm",
    "about", "terms", "privacy", "legal", "contact",
}

RESERVED_HANDLES = _ROUTE_NAMES | _IMPERSONATION


class HandleError(ValueError):
    """Raised with a user-facing reason when a handle is unacceptable."""


def normalize(raw: str) -> str:
    """Canonical storage form. Case is presentation-only; `Matt` and `matt`
    are the same handle, so everything downstream compares the lower form."""
    return (raw or "").strip().lower()


def validate(raw: str) -> str:
    """Return the normalized handle, or raise HandleError explaining why not."""
    handle = normalize(raw)

    if not handle:
        raise HandleError("Username is required")
    if len(handle) < MIN_LENGTH:
        raise HandleError(f"Username must be at least {MIN_LENGTH} characters")
    if len(handle) > MAX_LENGTH:
        raise HandleError(f"Username must be at most {MAX_LENGTH} characters")
    if not _HANDLE_RE.match(handle):
        raise HandleError(
            "Username may only contain lowercase letters, numbers and underscores"
        )
    if handle.startswith("_") or handle.endswith("_"):
        raise HandleError("Username may not start or end with an underscore")
    if "__" in handle:
        raise HandleError("Username may not contain consecutive underscores")
    if handle in RESERVED_HANDLES:
        raise HandleError("That username is reserved")

    return handle


def suggest_from_email(email: str, suffix: int | None = None) -> str:
    """Generate a starting handle for a user who hasn't chosen one.

    Used by the backfill in migration 010: existing accounts need *some* handle
    so the column can carry a uniqueness guarantee, and the user is free to
    change it afterwards. Falls back to a generic stem when the local part
    yields nothing usable.

    Every return value satisfies `validate()` — the backfill assigns these
    directly, so a handle this produces but the app would reject is a live
    account stuck with a name it cannot re-save.
    """
    stem = re.sub(r"[^a-z0-9_]", "", (email or "").split("@")[0].lower())
    stem = re.sub(r"_+", "_", stem).strip("_")
    if len(stem) < MIN_LENGTH:
        stem = f"player{stem}" if stem else "player"
    # Truncation can re-expose a trailing underscore that strip() already
    # removed once (`a_very_long_name_` cut mid-way), which validate() rejects.
    stem = stem[:MAX_LENGTH].rstrip("_")
    if len(stem) < MIN_LENGTH:
        stem = "player"

    if suffix is None and stem not in RESERVED_HANDLES:
        return stem

    tail = str(suffix if suffix is not None else 1)
    return f"{stem[: MAX_LENGTH - len(tail)].rstrip('_')}{tail}"


# The backfill runs against real user tables; a pathological dataset shouldn't
# turn into an unbounded loop inside a migration holding a transaction.
_MAX_SUGGESTION_ATTEMPTS = 10_000


def suggest_unique(email: str, taken: set[str]) -> str:
    """First handle derived from `email` that isn't already in `taken`.

    `taken` holds normalized (lower-case) handles and is the caller's running
    set — it must include handles assigned earlier in the same pass, not just
    those already in the database.

    Numbering per-stem is not enough on its own: `alice@a.com` and `alice@b.com`
    yield `alice` and `alice2`, which collides with a real `alice2@c.com`
    because both draw from one namespace. Checking each candidate against the
    whole set is what makes the result actually unique.
    """
    candidate = suggest_from_email(email)
    if candidate not in taken:
        return candidate

    for n in range(2, _MAX_SUGGESTION_ATTEMPTS):
        candidate = suggest_from_email(email, n)
        if candidate not in taken:
            return candidate

    raise HandleError(
        f"Could not derive a free handle for {email!r} after "
        f"{_MAX_SUGGESTION_ATTEMPTS} attempts"
    )
