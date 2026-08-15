"""CF-110: follow graph invariants.

The security-relevant rules are that a *pending* request grants nothing, and
that the `followers` tier resolves identically in Python and in SQL. Both are
checkable without a database; the counter and constraint behaviour is exercised
live against Postgres (see the PR body).
"""
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import select  # noqa: E402

from app.models.clip import Clip  # noqa: E402
from app.models.follow import FollowStatus  # noqa: E402
from app.models.game import Game  # noqa: E402
from app.models.visibility import Visibility  # noqa: E402
from app.services import access  # noqa: E402

VIEWER = uuid.uuid4()
OWNER = uuid.uuid4()


class _Game:
    def __init__(self, visibility, owner_id=OWNER):
        self.id = uuid.uuid4()
        self.owner_id = owner_id
        self.visibility = visibility


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def test_pending_is_not_accepted():
    """The two statuses must stay distinct — collapsing them would hand a
    requester access the target never granted."""
    assert FollowStatus.pending is not FollowStatus.accepted
    assert FollowStatus.pending.value == "pending"


def test_only_accepted_edges_appear_in_the_sql_filter():
    """A pending row must not satisfy the visibility EXISTS. If this ever
    matched on status alone, every request would silently grant access."""
    sql = _sql(access.apply_clip_visibility(select(Clip), VIEWER)).lower()
    assert "'accepted'" in sql
    assert "'pending'" not in sql


def test_exists_clause_is_scoped_to_both_ends_of_the_edge():
    """Guards against a filter that matches any accepted follow anywhere rather
    than this viewer following this owner."""
    from app.models.game import Game

    sql = _sql(access.accepted_follow_exists(VIEWER, Game.owner_id)).lower()
    assert "follower_id" in sql, "must pin the viewer end"
    assert "followee_id" in sql, "must pin the owner end"
    assert "games.owner_id" in sql, "the owner end must correlate to the game"


def test_followers_tier_matches_between_python_and_sql():
    """The two halves of the rule must agree.

    Python: `can_view_game(..., viewer_follows_owner=True)`.
    SQL: the EXISTS clause in the same query.
    A change to one without the other is the drift the CF-108 seam existed to
    prevent — this is its replacement guard.
    """
    game = _Game(Visibility.followers)
    assert access.can_view_game(VIEWER, game, viewer_follows_owner=True) is True
    assert access.can_view_game(VIEWER, game, viewer_follows_owner=False) is False

    sql = _sql(select(Game).where(access.visible_games_filter(VIEWER))).lower()
    assert "'followers'" in sql, "SQL must admit the same tier Python does"


def test_anonymous_never_gets_a_follow_subquery():
    """Signed-out visitors can't follow anyone; an always-false EXISTS on every
    anonymous read would be pure cost."""
    sql = _sql(select(Game).where(access.visible_games_filter(None))).lower()
    assert "exists" not in sql


# ── review findings, pinned ─────────────────────────────────────────────────


def test_resolve_follow_short_circuits_on_non_followers_tiers():
    """The docstring claimed a short-circuit that wasn't there — every
    authenticated read of a public or private object issued a `follows` lookup
    that could not change the answer. Asserted now rather than described.
    """
    import asyncio

    from app.services import follow_graph

    class _ExplodingSession:
        async def execute(self, *a, **kw):
            raise AssertionError("resolve_follow must not query for this tier")

    viewer, owner = uuid.uuid4(), uuid.uuid4()
    for level in (Visibility.private, Visibility.public, None):
        assert asyncio.run(
            follow_graph.resolve_follow(_ExplodingSession(), viewer, owner, level)
        ) is False


def test_resolve_follow_never_queries_for_anonymous_or_self():
    import asyncio

    from app.services import follow_graph

    class _ExplodingSession:
        async def execute(self, *a, **kw):
            raise AssertionError("no lookup should happen")

    me = uuid.uuid4()
    run = asyncio.run
    assert run(follow_graph.resolve_follow(
        _ExplodingSession(), None, me, Visibility.followers)) is False
    assert run(follow_graph.resolve_follow(
        _ExplodingSession(), me, me, Visibility.followers)) is False


def test_a_post_read_resolves_the_edge_once():
    """A post is gated on two levels — its own and its clip's — and its author
    is always the owner of the game behind it. Asking twice was two round trips
    for one fact."""
    import inspect

    from app.routers import posts as posts_router

    src = inspect.getsource(posts_router._load_for_read)
    assert src.count("follow_graph.resolve_follow(") == 1, "one lookup, not two"
    assert "is_accepted_follower" not in src


def test_the_counter_update_is_inside_the_integrity_guard():
    """`_adjust_counts` autoflushes the pending Follow, so a duplicate-pair
    violation surfaces there. With the call outside the try, two concurrent
    follows of a *public* account 500'd instead of returning existing state."""
    import inspect

    from app.routers import follows as follows_router

    src = inspect.getsource(follows_router.follow_user)
    try_at = src.index("try:")
    assert src.index("_adjust_counts") > try_at, "must be inside the try"
    assert "except IntegrityError" in src


def test_accepting_a_request_is_a_conditional_update():
    """Two concurrent accepts must not both increment. Atomic `x = x + n`
    prevents lost updates, not duplicate ones — only a guarded UPDATE does."""
    import inspect

    from app.routers import follows as follows_router

    src = inspect.getsource(follows_router.accept_follow_request)
    assert "Follow.status == FollowStatus.pending" in src, "the guard condition"
    assert "rowcount == 1" in src, "counters only when this request won"


def test_the_edge_cursor_carries_the_tiebreaker():
    """The ORDER BY names `id` as tiebreaker; a cursor on `created_at` alone
    skips the rest of a tied group at a page boundary — which bulk-accepting a
    backlog produces."""
    import inspect

    from app.routers import follows as follows_router

    src = inspect.getsource(follows_router._edge_page)
    assert "tuple_(Follow.created_at, Follow.id)" in src
    assert ".offset(" not in src


def test_a_naive_cursor_timestamp_is_a_400_not_a_500():
    """`fromisoformat` accepts a naive string; comparing it to a timestamptz
    column raises inside the driver, so it has to be rejected here."""
    import base64

    from fastapi import HTTPException

    from app.routers import follows as follows_router

    naive = base64.urlsafe_b64encode(
        f"2026-01-01T00:00:00|{uuid.uuid4()}".encode()
    ).decode()
    with pytest.raises(HTTPException) as exc:
        follows_router._decode_cursor(naive)
    assert exc.value.status_code == 400


def test_follower_lists_presign_avatars():
    """A raw `model_validate` hands the client a bucket URL it cannot load while
    the API reports 200 — the reason CF-107 built a serializer."""
    import inspect

    from app.routers import follows as follows_router

    for fn in (follows_router._edge_page, follows_router.list_follow_requests):
        src = inspect.getsource(fn)
        assert "profiles.serialize" in src
        assert "ProfileOut.model_validate" not in src


def test_follow_endpoints_cannot_resolve_a_generated_handle():
    """The public profile route 404s generated handles because the backfill
    derives them from email local parts. `follows` resolving them put the
    existence oracle back through a different door."""
    import inspect

    from app.routers import follows as follows_router
    from app.services import profiles as profile_service

    assert "username_is_generated" in inspect.getsource(profile_service.by_handle)
    src = inspect.getsource(follows_router)
    assert "profiles.by_handle" in src
    assert "func.lower(User.username)" not in src, "no second, unguarded lookup"
