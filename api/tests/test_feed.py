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
from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")
pytest.importorskip("boto3")

from fastapi import HTTPException  # noqa: E402


from app.routers import feed as feed_router  # noqa: E402
from app.services import cursors  # noqa: E402


def _visible(viewer):
    """The query the router actually runs — not a stand-in for it.

    This used to be `apply_post_visibility(select(Post), viewer)`, which omitted
    the author filter, the User join, the ordering and the limit. Every
    assertion below was therefore exercising CF-109/CF-110's shared predicate
    rather than the feed, and the review that caught it showed the cost: with
    `status == accepted` removed from the author filter, all 22 tests here still
    passed.
    """
    return feed_router.feed_query(viewer)

VIEWER = uuid.uuid4()


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
    """A signed-out visitor can't follow anyone, so the shared predicate must
    not emit an always-false EXISTS on every anonymous read.

    Asserted against `access.apply_post_visibility` rather than `feed_query`.
    The feed is authenticated and `feed_query(None)` is not an anonymous
    read — it is a dead statement (`author_id IS NULL OR author_id IN (SELECT
    ... WHERE follower_id IS NULL)`) that no row can satisfy, so asserting
    "no EXISTS appears" against it passed for a query that returned nothing at
    all. The predicate is the thing anonymous profile reads actually use.
    """
    from sqlalchemy import select

    from app.models.post import Post
    from app.services import access

    sql = _sql(access.apply_post_visibility(select(Post), None)).lower()
    assert "exists" not in sql
    assert "'followers'" not in sql


def test_a_private_post_is_visible_only_to_its_author():
    """The self clause is what makes the empty state work — a user who follows
    nobody still sees their own posts, including private ones."""
    sql = _sql(_visible(VIEWER)).lower()
    assert "posts.author_id" in sql, "must admit the viewer's own posts"


def test_visibility_is_not_optional_for_signed_in_viewers():
    """Guards against a future refactor that gates the filter behind a flag."""
    assert "'public'" in _sql(_visible(VIEWER))


def test_the_feed_query_is_not_reusable_for_anonymous_reads():
    """`feed_query(None)` is a bug, not an anonymous feed — and now it says so.

    It used to compile to `author_id IS NULL OR author_id IN (SELECT ... WHERE
    follower_id IS NULL)`: unsatisfiable, so the query returned nothing forever
    with no error, and three tests here asserted against that dead statement.
    The docstring explained the trap and then relied on the route being
    authenticated to prevent it, which is advice rather than enforcement —
    CF-114's explore feed is named as the caller most likely to reach for this
    builder. A raise makes it enforceable.
    """
    with pytest.raises(ValueError, match="requires a viewer"):
        feed_router.feed_query(None)  # type: ignore[arg-type]

# ── cursor ──────────────────────────────────────────────────────────────────


def test_cursor_round_trips_the_full_sort_key():
    """Both halves must survive. Encoding only the timestamp would let two posts
    created in the same millisecond hide behind each other permanently."""
    now = datetime.now(timezone.utc)
    post = _Post(now)
    ts, post_id = cursors.decode(cursors.encode(post.created_at, post.id))
    assert ts == now
    assert post_id == post.id


def test_cursor_is_opaque():
    """Not a security boundary — the query is visibility-filtered regardless —
    but it should not read as an invitation to hand-craft one."""
    _p = _Post(datetime.now(timezone.utc))
    cursor = cursors.encode(_p.created_at, _p.id)
    assert "|" not in cursor
    assert base64.urlsafe_b64decode(cursor.encode()).decode().count("|") == 1


@pytest.mark.parametrize(
    "bad",
    ["not-base64!!", "", base64.urlsafe_b64encode(b"garbage").decode(),
     base64.urlsafe_b64encode(b"2020-01-01T00:00:00|not-a-uuid").decode()],
)
def test_a_malformed_cursor_is_a_400_not_a_500(bad):
    with pytest.raises(HTTPException) as exc:
        cursors.decode(bad)
    assert exc.value.status_code == 400





# The keyset comparison, the ORDER BY it must match, the `limit + 1` and the
# absence of an N+1 were all asserted here as substrings of the router's own
# source. Each passed for any semantically broken rewrite that kept the text,
# and each failed on a `ruff format` that wrapped the line — a test that cannot
# fail for its stated reason and can fail for an unrelated one. They are now
# behavioural in `test_feed_pg.py`: the cursor walk covers the keyset and the
# ordering, `test_the_page_costs_one_query` counts the statements, and
# `test_has_more_is_read_before_the_page_is_truncated` below covers the +1 over
# real values.






def test_has_more_is_read_before_the_page_is_truncated():
    """The ordering that made this worth a shared function.

    Computing `has_more` from the already-sliced list makes it permanently
    False, so `next_cursor` is always null and nothing past the first page is
    ever reachable — a silent, total pagination failure. Asserted on
    `cursors.split_page` directly, over real values, rather than on the router's
    source: the source assertion this replaces was satisfied by the broken
    ordering too.
    """
    page, has_more = cursors.split_page(list(range(21)), 20)
    assert len(page) == 20 and has_more is True
    page, has_more = cursors.split_page(list(range(20)), 20)
    assert len(page) == 20 and has_more is False, "exact boundary is the last page"
    page, has_more = cursors.split_page([], 20)
    assert page == [] and has_more is False


# ── page size ───────────────────────────────────────────────────────────────


def test_page_size_is_bounded():
    """An unbounded limit is a cheap way to make one request presign thousands
    of objects."""
    import inspect

    sig = inspect.signature(feed_router.get_feed)
    constraints = sig.parameters["limit"].annotation.__metadata__[0].metadata
    # Read the constraint *values*, not `repr()` of the objects holding them.
    # The old form compared against the literals {"Ge(ge=1)", "Le(le=50)"}, so
    # an annotated-types release that changed its own repr broke this with no
    # behaviour change whatsoever.
    bounds = {
        name: getattr(c, name)
        for c in constraints
        for name in ("ge", "gt", "le", "lt")
        if hasattr(c, name)
    }
    assert bounds == {"ge": 1, "le": 50}
    assert feed_router.DEFAULT_PAGE == 20


def test_ordering_is_newest_first():
    """Asserted on the statement, not on two datetimes.

    The previous version built two `_Post` stand-ins and asserted
    `now - 1h < now` — true for a query that sorts ascending, and true for one
    with no ORDER BY at all. `test_feed_pg.py` walks real rows; this pins the
    direction in the SQL so a silent flip is caught without a database.
    """
    sql = _sql(_visible(VIEWER)).lower()
    order_by = sql.split("order by")[1]
    assert "posts.created_at desc" in order_by
    assert "posts.id desc" in order_by


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
    # Scope to the WHERE clause. The SELECT list names clips.visibility as a
    # column now that the real query returns the clip, so partitioning the whole
    # statement lands inside the column list rather than between the gates.
    where_at = sql.index("where ")
    posts_half, sep, clips_half = sql[where_at:].partition("clips.visibility")
    assert sep, "the clip gate must be in the WHERE clause"
    assert "'followers'" in posts_half, "post gate resolves the tier"
    assert "'followers'" in clips_half, "clip gate resolves it too"


def test_the_feed_reuses_the_shared_predicate():
    """Not a second copy of the visibility rule. The feed is the highest-traffic
    read of other people's footage, so it is the worst place for a divergent
    copy of the ladder — the same argument that moved posts into access.py."""
    import inspect

    src = inspect.getsource(feed_router.feed_query)
    assert "access.apply_post_visibility" in src
    assert "Post.visibility" not in src, "no local visibility logic"
    assert "Clip.visibility" not in src


def test_a_naive_cursor_timestamp_is_rejected():
    """The feed's decoder had no timezone check while `follows` did.

    asyncpg does not raise on naive-vs-timestamptz — measured, it reinterprets
    naive input as UTC and returns rows — so the unchecked version paged from
    the wrong instant silently rather than failing. Both cursors are strict now.
    """
    naive = base64.urlsafe_b64encode(
        f"2026-01-01T00:00:00|{uuid.uuid4()}".encode()
    ).decode()
    with pytest.raises(HTTPException) as exc:
        cursors.decode(naive)
    assert exc.value.status_code == 400


def test_both_cursor_decoders_agree_on_what_is_malformed():
    """There is one decoder now, which is the real fix.

    This test previously compared two character-identical functions for drift —
    which is the point at which they should be one function. `services/cursors`
    is that function; what is left to assert is that both routers reach for it
    rather than growing a third copy, and that the survivor is the strict one.
    """
    import inspect

    from app.routers import follows as follows_router

    for module in (feed_router, follows_router):
        src = inspect.getsource(module)
        assert "cursors.decode(" in src, f"{module.__name__} must use the shared decoder"
        assert "def _decode_cursor" not in src, f"{module.__name__} grew a local copy"

    assert "tzinfo is None" in inspect.getsource(cursors.decode)
    for bad in ("", "not-base64!!", base64.urlsafe_b64encode(b"garbage").decode()):
        with pytest.raises(HTTPException) as exc:
            cursors.decode(bad)
        assert exc.value.status_code == 400


# ── the author filter itself (review finding 1) ─────────────────────────────


def test_the_author_filter_requires_an_accepted_edge():
    """The assertion the old suite was missing.

    `test_only_accepted_follows_widen_the_feed` checks `'accepted'` appears in
    the compiled SQL — and it does, inside CF-110's visibility EXISTS, which is
    a different clause about a different question. So dropping
    `status == accepted` from the *author* filter passed the whole suite.

    This asserts against the author subquery specifically: the IN (...) that
    decides whose posts are eligible at all, before visibility is considered.
    """
    from app.services import follow_graph

    sub = _sql(follow_graph.followed_author_ids(VIEWER)).lower()
    assert "follows.status" in sub, "the author filter must constrain status"
    assert "'accepted'" in sub
    assert "'pending'" not in sub, "a request must not make its target's posts eligible"
    assert "follows.follower_id" in sub, "scoped to this viewer"


def test_the_feed_takes_the_followed_set_from_follow_graph():
    """One source for 'which accounts count as followed'.

    It had drifted into three copies — access.accepted_follow_exists,
    is_accepted_follower, and one written out in the router — of which only the
    first was pinned. The router's is now the shared one.
    """
    import inspect

    src = inspect.getsource(feed_router.feed_query)
    assert "follow_graph.followed_author_ids" in src
    assert "FollowStatus.accepted" not in src, "no fourth copy of the rule"


def test_the_tests_assert_against_the_query_the_router_runs():
    """Guards the fix itself.

    `_visible` returning anything other than the router's own builder is how
    this suite drifted into testing the shared predicate instead of the feed.
    """
    import inspect

    assert "feed_query" in inspect.getsource(_visible)
    sql = _sql(_visible(VIEWER)).lower()
    # Properties that exist only on the real query, absent from the old stand-in.
    assert "join users" in sql, "the User join the router adds"
    assert "order by" in sql, "the ordering"
    assert "limit" in sql, "the page bound"
    assert "posts.author_id in" in sql, "the author filter"
