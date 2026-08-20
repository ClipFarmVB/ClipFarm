"""CF-107: handle validation rules.

Pure logic — `app.services.handles` imports nothing but `re`, so these run
anywhere with no database, no settings and no network.
"""
import pytest

from app.services import handles


@pytest.mark.parametrize("raw", ["matt", "player_7", "a1b2c3", "x" * 30, "kunyuan1"])
def test_accepts_valid_handles(raw):
    assert handles.validate(raw) == raw


def test_normalizes_case_and_whitespace():
    """Case is presentation-only; storage and comparison use the lower form."""
    assert handles.validate("  MattZ  ") == "mattz"


@pytest.mark.parametrize(
    "raw,fragment",
    [
        ("", "required"),
        ("ab", "at least"),
        ("x" * 31, "at most"),
        ("has space", "lowercase letters"),
        ("has-hyphen", "lowercase letters"),
        ("has.dot", "lowercase letters"),
        ("emoji😀x", "lowercase letters"),
        ("_leading", "start or end"),
        ("trailing_", "start or end"),
        ("double__under", "consecutive"),
    ],
)
def test_rejects_malformed_handles(raw, fragment):
    with pytest.raises(handles.HandleError) as exc:
        handles.validate(raw)
    assert fragment in str(exc.value)


@pytest.mark.parametrize("raw", ["admin", "ADMIN", "support", "clipfarm", "api"])
def test_rejects_reserved_handles(raw):
    """Case-insensitively — normalization happens before the reserved check."""
    with pytest.raises(handles.HandleError, match="reserved"):
        handles.validate(raw)


@pytest.mark.parametrize("route", ["games", "upload", "collections", "login", "signup"])
def test_real_app_routes_are_reserved(route):
    """A handle equal to a top-level route would collide the moment vanity URLs
    move to the root. These names exist in web/src/app today."""
    assert route in handles.RESERVED_HANDLES


def test_suggestion_uses_the_email_local_part():
    assert handles.suggest_from_email("Matt.Zhu+vb@example.com") == "mattzhuvb"


def test_suggestion_falls_back_when_local_part_is_unusable():
    """A local part that sanitizes to fewer than 3 chars still has to yield a
    handle that passes validate() — the backfill depends on it."""
    for email in ("a@example.com", "!!@example.com", "@example.com", ""):
        assert handles.validate(handles.suggest_from_email(email))


def test_suggestion_disambiguates_with_a_suffix():
    assert handles.suggest_from_email("alice@a.com", suffix=2) == "alice2"


def test_suggestion_never_returns_a_reserved_handle():
    """`admin@example.com` must not be handed the `admin` handle."""
    assert handles.suggest_from_email("admin@example.com") != "admin"


def test_suggestion_respects_the_length_cap():
    long_email = ("z" * 60) + "@example.com"
    assert len(handles.suggest_from_email(long_email, suffix=12)) <= handles.MAX_LENGTH


# ── suggest_unique: what the backfill actually calls ────────────────────────
#
# The SQL backfill this replaced numbered rows *within* a stem partition, which
# is not the same as being unique — the generated names all draw from one
# namespace. These pin the cases that produced a duplicate and aborted
# migration 010 at CREATE UNIQUE INDEX.


def _assign(emails):
    """Run the backfill's loop over `emails`, as migration 010 does."""
    taken: set[str] = set()
    out = []
    for email in emails:
        handle = handles.suggest_unique(email, taken)
        taken.add(handle)
        out.append(handle)
    return out


def test_unique_suggestions_disambiguate_a_shared_stem():
    assert _assign(["alice@a.com", "alice@b.com"]) == ["alice", "alice2"]


def test_unique_suggestions_survive_a_stem_that_looks_like_a_suffix():
    """The regression: `alice2@c.com` collides with the name generated for the
    second `alice`, because per-stem numbering can't see across partitions."""
    assigned = _assign(["alice@a.com", "alice@b.com", "alice2@c.com"])
    assert len(set(assigned)) == 3, f"duplicate handles: {assigned}"


def test_unique_suggestions_survive_the_generic_fallback_colliding():
    """Same shape via the short-local-part fallback: `player`, `player2`, and a
    real `player2@z.com`."""
    assigned = _assign(["a@x.com", "!!!@y.com", "player2@z.com"])
    assert len(set(assigned)) == 3, f"duplicate handles: {assigned}"


def test_unique_suggestions_respect_handles_already_in_the_database():
    """`taken` is seeded from existing rows, not just this pass."""
    assert handles.suggest_unique("alice@a.com", {"alice"}) != "alice"


def test_every_generated_handle_passes_validation():
    """A handle the backfill assigns but validate() rejects leaves a live user
    holding a name they cannot re-save."""
    emails = [
        "alice@a.com", "alice@b.com", "alice2@c.com", "admin@x.com",
        "_matt_@x.com", "m@x.com", "!!!@y.com", "player2@z.com",
        ("z" * 60) + "@example.com", "UPPER.Case+tag@x.com", "a__b@x.com",
        ("q" * 29 + "_") + "@x.com",
    ]
    for handle in _assign(emails):
        assert handles.validate(handle) == handle, f"invalid generated handle: {handle!r}"


def test_generated_handles_are_never_reserved():
    """`admin@yourgym.com` must not end up owning /u/admin."""
    for handle in _assign([f"{name}@x.com" for name in sorted(handles.RESERVED_HANDLES)]):
        assert handle not in handles.RESERVED_HANDLES
