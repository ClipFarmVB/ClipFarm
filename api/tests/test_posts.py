"""CF-109: post visibility rules.

The security-relevant part of posting is that a post can never widen access to
the footage behind it, and that withdrawing the clip withdraws the post. Both
are pure predicate logic, so they're testable without a database.
"""
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")
# The router pulls in services.storage for presigned playback URLs, which needs
# boto3. Present in CI (api/requirements-dev.txt); guarded so a bare
# environment skips instead of erroring at collection.
pytest.importorskip("boto3")

from app.models.visibility import Visibility  # noqa: E402
from app.routers import posts as posts_router  # noqa: E402
from app.services import access  # noqa: E402

AUTHOR = uuid.uuid4()
STRANGER = uuid.uuid4()
ANONYMOUS = None
RANK = posts_router._RANK


class _Game:
    def __init__(self, visibility, owner_id=AUTHOR):
        self.id = uuid.uuid4()
        self.owner_id = owner_id
        self.visibility = visibility


class _Clip:
    def __init__(self, game, visibility=None):
        self.id = uuid.uuid4()
        self.game_id = game.id
        self.visibility = visibility


class _Post:
    def __init__(self, visibility, author_id=AUTHOR):
        self.id = uuid.uuid4()
        self.author_id = author_id
        self.visibility = visibility


# ── a post may never be wider than its clip ─────────────────────────────────


@pytest.mark.parametrize(
    "clip_level,post_level,allowed",
    [
        (Visibility.private, Visibility.private, True),
        (Visibility.private, Visibility.followers, False),
        (Visibility.private, Visibility.public, False),
        (Visibility.followers, Visibility.private, True),
        (Visibility.followers, Visibility.followers, True),
        (Visibility.followers, Visibility.public, False),
        (Visibility.public, Visibility.private, True),
        (Visibility.public, Visibility.followers, True),
        (Visibility.public, Visibility.public, True),
    ],
)
def test_post_cannot_exceed_its_clips_visibility(clip_level, post_level, allowed):
    """The rule create_post enforces: publishing must not be a back door to
    exposing footage the clip itself keeps private."""
    assert (RANK[post_level] <= RANK[clip_level]) is allowed


def test_rank_covers_every_visibility_value():
    """A new tier added to the enum without a rank would raise KeyError inside
    create_post — at request time, not at import."""
    assert set(RANK) == set(Visibility)


# ── reading a post ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "viewer,post_level,expected",
    [
        (STRANGER, Visibility.public, True),
        (STRANGER, Visibility.followers, False),  # False until CF-110
        (STRANGER, Visibility.private, False),
        (ANONYMOUS, Visibility.public, True),
        (ANONYMOUS, Visibility.followers, False),
        (ANONYMOUS, Visibility.private, False),
    ],
)
def test_post_readability(viewer, post_level, expected):
    game = _Game(Visibility.public)
    assert posts_router._post_readable(viewer, _Post(post_level), game) is expected


def test_followers_tier_is_closed_until_cf110():
    """Mirrors the CF-108 seam: `followers` posts stay author-only until the
    follow graph exists, rather than defaulting to visible."""
    assert access.is_follower(STRANGER, AUTHOR) is False
    assert posts_router._post_readable(
        STRANGER, _Post(Visibility.followers), _Game(Visibility.public)
    ) is False


def test_a_public_post_over_a_private_clip_is_still_unreadable():
    """The regression this design exists to prevent.

    Even if a public post row somehow exists over a private clip — a bad
    migration, a direct DB edit, a future code path that skips create_post's
    check — the read path gates on the clip too, so the footage stays private.
    """
    game = _Game(Visibility.private)
    clip = _Clip(game)
    post = _Post(Visibility.public)

    assert posts_router._post_readable(STRANGER, post, game) is True
    # ...but the clip gate is what actually decides:
    assert access.can_view_clip(STRANGER, clip, game) is False


def test_author_reads_their_own_private_post():
    game = _Game(Visibility.private)
    clip = _Clip(game)
    assert access.can_view_clip(AUTHOR, clip, game) is True
