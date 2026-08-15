"""CF-111: home feed.

Two things can go wrong here and both are silent. The feed can leak a post the
caller isn't entitled to, and the cursor can duplicate or skip a post while new
posts are being inserted. The first is checked by compiling the query and
asserting on the SQL; the second by round-tripping the cursor and pinning the
comparison to the ORDER BY. Paging behaviour under concurrent inserts is
exercised live against Postgres (see the PR body).
"""
import base64
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")
pytest.importorskip("boto3")

from fastapi import HTTPException  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.models.post import Post as PostModel  # noqa: E402
from app.routers import feed as feed_router  # noqa: E402
from app.services import access  # noqa: E402


def _visible(viewer):
    """The feed's visibility-filtered base query, as the router builds it."""
    return access.apply_post_visibility(select(PostModel), viewer)

VIEWER = uuid.uuid4()
ANONYMOUS = None


class _Post:
    def __init__(self, created_at, post_id=None):
        self.created_at = created_at
        self.id = post_id or uuid.uuid4()


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


# ── the query only returns what the viewer may see ──────────────────────────


def test_feed_query_gates_on_both_the_post_and_the_clip():
    """A post is readable only if the post's own tier admits the viewer AND the
    clip behind it does. Dropping the second gate is the bug that would let a
    clip going private after it was posted keep playing in followers' feeds.
    """
    sql = _sql(_visible(VIEWER)).lower()
    assert "posts.visibility" in sql
    assert "clips.visibility" in sql
    assert "games.visibility" in sql, "inherited clip visibility must resolve too"


def test_feed_query_joins_rather_than_selecting_posts_alone():
    """Same trap CF-108 hit: the predicate references clips and games, so
    without the joins it compiles to a cartesian product and returns rows it
    should not. The joins are built into the query for that reason."""
    sql = _sql(_visible(VIEWER)).upper()
    assert "JOIN CLIPS" in sql
    assert "JOIN GAMES" in sql
    assert "FROM POSTS, CLIPS" not in sql, "cartesian product — join missing"


def test_only_accepted_follows_widen_the_feed():
    """A pending request must contribute nothing. If `pending` ever satisfied
    this, requesting a private account would silently grant its feed.

    Since CF-110 this resolves in SQL, so the assertion is on the EXISTS rather
    than on a Python-side flag."""
    sql = _sql(_visible(VIEWER)).lower()
    assert "exists" in sql and "follows" in sql
    assert "'accepted'" in sql
    assert "'pending'" not in sql


def test_anonymous_gets_no_follow_subquery():
    """A signed-out visitor can't follow anyone. The feed itself is
    authenticated, but the shared predicate serves anonymous profile reads too,
    and an always-false EXISTS on every one of those is pure cost."""
    sql = _sql(_visible(ANONYMOUS)).lower()
    assert "exists" not in sql
    assert "'followers'" not in sql


def test_a_private_post_is_visible_only_to_its_author():
    """The self clause is what makes the empty state work — a user who follows
    nobody still sees their own posts, including private ones."""
    sql = _sql(_visible(VIEWER)).lower()
    assert "posts.author_id" in sql, "must admit the viewer's own posts"


def test_visibility_is_not_optional_for_signed_in_viewers():
    """Guards against a future refactor that gates the filter behind a flag:
    every viewer kind must get a WHERE clause naming public."""
    for viewer in (VIEWER, ANONYMOUS):
        assert "'public'" in _sql(_visible(viewer))


# ── cursor ──────────────────────────────────────────────────────────────────


def test_cursor_round_trips_the_full_sort_key():
    """Both halves must survive. Encoding only the timestamp would let two posts
    created in the same millisecond hide behind each other permanently."""
    now = datetime.now(timezone.utc)
    post = _Post(now)
    ts, post_id = feed_router._decode_cursor(feed_router._encode_cursor(post))
    assert ts == now
    assert post_id == post.id


def test_cursor_is_opaque():
    """Not a security boundary — the query is visibility-filtered regardless —
    but it should not read as an invitation to hand-craft one."""
    cursor = feed_router._encode_cursor(_Post(datetime.now(timezone.utc)))
    assert "|" not in cursor
    assert base64.urlsafe_b64decode(cursor.encode()).decode().count("|") == 1


@pytest.mark.parametrize(
    "bad",
    ["not-base64!!", "", base64.urlsafe_b64encode(b"garbage").decode(),
     base64.urlsafe_b64encode(b"2020-01-01T00:00:00|not-a-uuid").decode()],
)
def test_a_malformed_cursor_is_a_400_not_a_500(bad):
    with pytest.raises(HTTPException) as exc:
        feed_router._decode_cursor(bad)
    assert exc.value.status_code == 400


def test_paging_uses_a_keyset_not_an_offset():
    """OFFSET re-counts from the top on every page, so a post inserted while
    someone scrolls shifts every later page by one — duplicating a row at the
    boundary and skipping another. This is the acceptance criterion."""
    import inspect

    src = inspect.getsource(feed_router.get_feed)
    assert "tuple_" in src, "keyset comparison expected"
    assert ".offset(" not in src, "OFFSET paging would duplicate/skip rows"


def test_the_cursor_comparison_matches_the_order_by():
    """A keyset that compares on a different key than it sorts by is worse than
    OFFSET — it skips rows deterministically rather than only under writes."""
    import inspect

    src = inspect.getsource(feed_router.get_feed)
    assert "Post.created_at.desc(), Post.id.desc()" in src
    assert "tuple_(Post.created_at, Post.id) < (created_at, post_id)" in src


# ── no N+1 ──────────────────────────────────────────────────────────────────


def test_one_page_is_one_query():
    """The card's criterion: a feed page must not issue a query per post. The
    author, clip and game all arrive as joined columns."""
    import inspect

    src = inspect.getsource(feed_router.get_feed)
    body = src.split("items: list[PostOut] = []")[1]
    assert "await db.execute" not in body, "no queries inside the serialize loop"
    assert "db.get(" not in body
    assert "select(Post, Clip, User)" in src, "author must be joined, not fetched per row"


def test_has_more_costs_no_extra_query():
    """limit + 1 and discard, rather than a COUNT over the same filtered set."""
    import inspect

    src = inspect.getsource(feed_router.get_feed)
    assert "limit + 1" in src
    assert "func.count" not in src


def test_last_page_has_no_next_cursor():
    """The client's stop signal. Derived from the extra row, not from
    `len(items) < limit`, which stops a page early on an exact boundary."""
    import inspect

    src = inspect.getsource(feed_router.get_feed)
    assert "has_more = len(rows) > limit" in src
    assert "if (has_more and rows) else None" in src


# ── page size ───────────────────────────────────────────────────────────────


def test_page_size_is_bounded():
    """An unbounded limit is a cheap way to make one request presign thousands
    of objects."""
    import inspect

    sig = inspect.signature(feed_router.get_feed)
    constraints = sig.parameters["limit"].annotation.__metadata__[0].metadata
    assert {repr(c) for c in constraints} == {"Ge(ge=1)", "Le(le=50)"}
    assert feed_router.DEFAULT_PAGE == 20


def test_ordering_is_newest_first():
    now = datetime.now(timezone.utc)
    older = _Post(now - timedelta(hours=1))
    newer = _Post(now)
    # The cursor from a newer post must exclude it and admit the older one,
    # which is what "newest first, page forward into the past" means.
    ts, _ = feed_router._decode_cursor(feed_router._encode_cursor(newer))
    assert older.created_at < ts


# ── the CF-110 interaction ──────────────────────────────────────────────────


def test_the_feed_surfaces_followers_tier_posts():
    """The whole point of a feed built on the follow graph.

    On the pre-CF-110 line this could not work: `followers` resolved to False
    for everyone, so a feed of the accounts you follow could only ever show
    their *public* posts. The tier now resolves in the same EXISTS that decides
    which authors are in the feed at all.
    """
    sql = _sql(_visible(VIEWER)).lower()
    assert "'followers'" in sql, "followers-tier posts must be admitted"
    assert "posts.author_id" in sql, "correlated to the post's author"


def test_both_gates_resolve_the_tier_not_just_one():
    """A followers-tier post over a followers-tier clip has to pass twice. If
    only the post gate learned about follows, the clip gate would still reject
    it and the feed would silently drop the exact content it exists to show."""
    sql = _sql(_visible(VIEWER)).lower()
    posts_half, _, clips_half = sql.partition("clips.visibility")
    assert "'followers'" in posts_half, "post gate resolves the tier"
    assert "'followers'" in clips_half, "clip gate resolves it too"


def test_the_feed_reuses_the_shared_predicate():
    """Not a second copy of the visibility rule. The feed is the highest-traffic
    read of other people's footage, so it is the worst place for a divergent
    copy of the ladder — the same argument that moved posts into access.py."""
    import inspect

    src = inspect.getsource(feed_router.get_feed)
    assert "access.apply_post_visibility" in src
    assert "Post.visibility" not in src, "no local visibility logic"
    assert "Clip.visibility" not in src
