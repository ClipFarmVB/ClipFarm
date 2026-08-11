"""CF-107: the profile routes' security-relevant behaviour.

`test_handles.py` covers handle validation. The properties here are the ones a
single innocuous edit can silently undo:

* `email` must not appear in the public profile response. `MeOut` subclasses
  `ProfileOut`, so promoting a field up the hierarchy publishes the login
  credential through an unauthenticated handle lookup.
* `/users/me` must not be shadowed by `/users/{handle}`. FastAPI matches in
  declaration order, so reordering the route definitions makes `me` resolve as
  a handle.

Both were curl-verified prose before this file existed.

Follows the house pattern from test_corrections.py — a stand-in session and
direct calls into the router, no TestClient and no database.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402

from app.routers import profiles  # noqa: E402
from app.schemas.profile import MeOut, ProfileOut, ProfileUpdate  # noqa: E402


class _FakeUser:
    """The columns the profile routes touch."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", uuid.uuid4())
        self.email = kwargs.get("email", "someone@example.com")
        self.username = kwargs.get("username")
        self.display_name = kwargs.get("display_name")
        self.bio = kwargs.get("bio")
        self.avatar_url = kwargs.get("avatar_url")
        self.is_private = kwargs.get("is_private", True)
        self.created_at = kwargs.get("created_at", datetime.now(timezone.utc))
        self.username_changed_at = kwargs.get("username_changed_at")
        self.username_is_generated = kwargs.get("username_is_generated", False)


class _FakeSession:
    """Returns a fixed user from get(); reports handles as free unless told."""

    def __init__(self, user, *, taken=False):
        self._user = user
        self._taken = taken
        self.committed = False

    async def get(self, _model, _pk):
        return self._user

    async def execute(self, _statement):
        taken = self._taken

        class _Result:
            def first(self):
                return (uuid.uuid4(),) if taken else None

            def scalar_one_or_none(self):
                return None

        return _Result()

    async def commit(self):
        self.committed = True

    async def refresh(self, _obj):
        return None


def _patch(user, **kwargs):
    session = _FakeSession(user, **kwargs)
    body = ProfileUpdate(username=kwargs.pop("username", "newhandle"))
    return session, asyncio.run(profiles.update_me(body, user.id, session))


# ── the public/private split ────────────────────────────────────────────────

def test_public_profile_schema_does_not_expose_email():
    """The security boundary: a handle lookup is unauthenticated."""
    assert "email" not in ProfileOut.model_fields


def test_email_lives_on_the_owner_only_schema():
    assert "email" in MeOut.model_fields


def test_public_profile_fields_are_an_explicit_allowlist():
    """Pins the whole set, so a field added to ProfileOut fails here rather than
    quietly appearing in every public lookup."""
    assert set(ProfileOut.model_fields) == {
        "id",
        "username",
        "display_name",
        "bio",
        "avatar_url",
        "is_private",
        "created_at",
    }


def test_serializing_a_user_through_the_public_schema_drops_email():
    user = _FakeUser(email="private@example.com", username="matt")
    dumped = ProfileOut.model_validate(user).model_dump()
    assert "email" not in dumped
    assert "private@example.com" not in str(dumped)


# ── route ordering ──────────────────────────────────────────────────────────

def _path_order():
    return [getattr(r, "path", None) for r in profiles.router.routes]


def test_me_is_declared_before_the_handle_catch_all():
    """`/users/{handle}` would otherwise swallow `/users/me`."""
    paths = _path_order()
    assert paths.index("/users/me") < paths.index("/users/{handle}")


def test_handle_available_is_declared_before_the_handle_catch_all():
    paths = _path_order()
    assert paths.index("/users/handle-available") < paths.index("/users/{handle}")


# ── rename cooldown ─────────────────────────────────────────────────────────

def test_rename_within_the_cooldown_is_rejected():
    user = _FakeUser(
        username="oldhandle",
        username_changed_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    with pytest.raises(HTTPException) as exc:
        _patch(user)
    assert exc.value.status_code == 429


def test_rename_after_the_cooldown_is_allowed():
    user = _FakeUser(
        username="oldhandle",
        username_changed_at=datetime.now(timezone.utc)
        - profiles.USERNAME_CHANGE_COOLDOWN
        - timedelta(days=1),
    )
    _, result = _patch(user)
    assert result.username == "newhandle"


def test_first_claim_is_free():
    """No username yet — nothing to rate limit, and changed_at stays null so the
    next change is free too (fixing a typo shouldn't cost 30 days)."""
    user = _FakeUser(username=None)
    _, result = _patch(user)
    assert result.username == "newhandle"
    assert user.username_changed_at is None


def test_replacing_a_generated_handle_counts_as_a_first_claim():
    """The backfilled cohort (migration 010) never chose their handle, so
    replacing it is a claim, not a rename — same grace as a new signup."""
    user = _FakeUser(username="generated1", username_is_generated=True)
    _, result = _patch(user)
    assert result.username == "newhandle"
    assert user.username_changed_at is None, "a claim must not start the cooldown"
    assert user.username_is_generated is False, "it's a chosen handle now"


def test_a_generated_handle_is_not_rate_limited_even_with_a_changed_at():
    user = _FakeUser(
        username="generated1",
        username_is_generated=True,
        username_changed_at=datetime.now(timezone.utc),
    )
    _, result = _patch(user)
    assert result.username == "newhandle"


def test_taken_handle_is_rejected_with_409():
    user = _FakeUser(username=None)
    with pytest.raises(HTTPException) as exc:
        _patch(user, taken=True)
    assert exc.value.status_code == 409


# ── avatar validation ───────────────────────────────────────────────────────

def test_avatar_rejects_an_unsupported_content_type():
    class _Upload:
        content_type = "image/gif"
        file = None

    user = _FakeUser()
    session = _FakeSession(user)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(profiles.upload_avatar(user.id, session, _Upload()))
    assert exc.value.status_code == 400


def test_avatar_accepts_every_declared_type():
    """The allowlist and the key must agree on what can be stored; a type here
    that upload_avatar rejects would be a dead entry."""
    assert profiles.ALLOWED_AVATAR_TYPES == {"image/jpeg", "image/png", "image/webp"}
