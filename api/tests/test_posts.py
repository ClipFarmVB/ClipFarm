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
        self.clip_id = None


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
        (AUTHOR, Visibility.private, True),  # your own post is always yours
    ],
)
def test_post_readability(viewer, post_level, expected):
    game = _Game(Visibility.public)
    clip = _Clip(game)
    post = _Post(post_level)
    post.clip_id = clip.id
    assert access.can_view_post(viewer, post, clip, game) is expected


def test_the_ladder_is_not_duplicated_in_the_router():
    """Review finding: a router-local copy of the private/followers/public
    ladder is a second place for it to drift, on the one table whose whole
    purpose is serving other people's footage. CF-110 changes both the
    object-level answer and the SQL one; a router copy would have picked up the
    first and silently missed the second."""
    assert not hasattr(posts_router, "_post_readable")
    assert hasattr(access, "can_view_post")
    assert hasattr(access, "apply_post_visibility")


def test_followers_tier_is_closed_until_cf110():
    """Mirrors the CF-108 seam: `followers` posts stay author-only until the
    follow graph exists, rather than defaulting to visible."""
    assert access.is_follower(STRANGER, AUTHOR) is False
    game = _Game(Visibility.public)
    clip = _Clip(game)
    post = _Post(Visibility.followers)
    post.clip_id = clip.id
    assert access.can_view_post(STRANGER, post, clip, game) is False


# ── the page must be filtered in SQL, not after the LIMIT ───────────────────


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def test_post_visibility_is_applied_in_sql():
    """Review finding: applying `limit` in SQL and visibility in Python means
    the limit counts rows the viewer can't see. An author with 60 private posts
    then 20 public ones returns an *empty* page to a stranger, and no amount of
    paging reaches the readable rows."""
    from sqlalchemy import select

    from app.models.post import Post as PostModel

    sql = _sql(access.apply_post_visibility(select(PostModel), STRANGER)).upper()
    assert "WHERE" in sql
    assert "POSTS.VISIBILITY" in sql, "the post gate must be in the WHERE clause"
    assert "CLIPS.VISIBILITY" in sql, "the clip gate must be too"


def test_the_post_query_owns_its_joins():
    """Both gates read `clips` and `games`, so an unjoined query is a cartesian
    product that fails *open* — the same trap CF-108 fixed for clips, which is
    why this takes the statement rather than handing back a predicate."""
    from sqlalchemy import select

    from app.models.post import Post as PostModel

    sql = _sql(access.apply_post_visibility(select(PostModel), STRANGER)).upper()
    assert "JOIN CLIPS" in sql
    assert "JOIN GAMES" in sql
    assert "FROM POSTS, CLIPS" not in sql, "cartesian product — join missing"


def test_the_list_endpoint_does_not_filter_after_the_limit():
    """Pins the fix at the call site, not just in the helper."""
    import inspect

    src = inspect.getsource(posts_router.list_user_posts)
    assert "apply_post_visibility" in src
    assert "continue" not in src, "a skip in the loop is post-LIMIT filtering"


def test_a_public_post_over_a_private_clip_is_still_unreadable():
    """The regression this design exists to prevent.

    Even if a public post row somehow exists over a private clip — a bad
    migration, a direct DB edit, a future code path that skips create_post's
    check — the read path gates on the clip too, so the footage stays private.
    """
    game = _Game(Visibility.private)
    clip = _Clip(game)
    post = _Post(Visibility.public)
    post.clip_id = clip.id

    # The post's own tier says yes...
    assert access.may_read(STRANGER, post.author_id, post.visibility) is True
    # ...and the clip gate is what actually decides:
    assert access.can_view_clip(STRANGER, clip, game) is False
    assert access.can_view_post(STRANGER, post, clip, game) is False


def test_author_reads_their_own_private_post():
    game = _Game(Visibility.private)
    clip = _Clip(game)
    assert access.can_view_clip(AUTHOR, clip, game) is True
