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
    """`admin@clipfarm.app` must not be handed the `admin` handle."""
    assert handles.suggest_from_email("admin@example.com") != "admin"


def test_suggestion_respects_the_length_cap():
    long_email = ("z" * 60) + "@example.com"
    assert len(handles.suggest_from_email(long_email, suffix=12)) <= handles.MAX_LENGTH
