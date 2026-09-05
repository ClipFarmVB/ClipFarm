"""CF-110: follow graph invariants.

The security-relevant rules are that a *pending* request grants nothing, and
that the `followers` tier resolves identically in Python and in SQL. Both are
checkable without a database; the counter and constraint behaviour is exercised
live against Postgres (see the PR body).
"""
import re
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import select  # noqa: E402

from app.models.clip import Clip  # noqa: E402
from app.models.follow import FollowStatus  # noqa: E402
from app.models.game import Game  # noqa: E402
from app.models.visibility import Visibility  # noqa: E402
from app.services import access, follow_graph  # noqa: E402

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


def test_a_post_read_resolves_one_edge_when_the_principals_coincide():
    """A post's tier belongs to its author, its clip's to the game's owner.

    Those are the same person for every post today, so the second lookup is
    short-circuited rather than skipped — the earlier version assumed they were
    always identical and reused one answer for both questions, which would have
    authorized against the wrong follow edge the moment a game changed hands.
    The assertion is that the reuse stays *conditional*, not that it stays a
    single call.
    """
    import inspect

    from app.routers import posts as posts_router

    src = inspect.getsource(posts_router._load_for_read)
    assert "post.author_id == game.owner_id" in src, "reuse must stay conditional"
    assert "if same_principal" in src, "and the condition must actually gate the reuse"
    assert src.count("follow_graph.resolve_follow(") == 2, "a fallback for the split case"
    assert "is_accepted_follower" not in src, "go through resolve_follow's short-circuit"
    # The clip's tier is handed to the *author* lookup only when the principals
    # coincide, which is what keeps the merged path to a single query. In the
    # split case it would fire a `follows` lookup against the author whose
    # result `may_read` discards, and a second one for the owner regardless.
    assert "(post.visibility, clip_level) if same_principal else (post.visibility,)" in src


def test_the_counter_update_is_inside_the_integrity_guard():
    """`_adjust_counts` autoflushes the pending Follow, so a duplicate-pair
    violation surfaces there. With the call outside the try, two concurrent
    follows of a *public* account 500'd instead of returning existing state."""
    import inspect

    from app.routers import follows as follows_router

    src = inspect.getsource(follows_router.follow_user)
    # Sliced from the INSERT: `follow_user` also adjusts counts on the
    # promote-a-stranded-request branch, which runs before this try and is not
    # what this test is about. Anchoring on `db.add(Follow(` keeps the assertion
    # pointed at the insert path whose violation the try exists to catch.
    insert_onward = src[src.index("db.add(Follow("):]
    try_at = insert_onward.index("try:")
    assert insert_onward.index("_adjust_counts") > try_at, "must be inside the try"
    assert "except IntegrityError" in src


def test_the_integrity_recovery_reads_no_expired_orm_attribute():
    """`db.rollback()` expires the identity map.

    `_restore_snapshot` runs with `dirty_only=False` on a top-level transaction,
    so every instance loaded before the rollback — `target`, from
    `profiles.by_handle` — is expired afterwards. Touching one is a lazy refresh,
    which under `AsyncSession` raises `MissingGreenlet` and 500s the *recovery
    path itself*: the duplicate-pair loser this handler exists to answer got a
    500 anyway, and the earlier fix (moving `_adjust_counts` inside the try)
    made the violation surface in the right place without making the handler
    able to finish.

    Pinned as source rather than behaviour because reproducing it needs two
    concurrent requests against a live database. The rule is narrow and
    mechanical: after `rollback()`, use plain locals.
    """
    import inspect

    from app.routers import follows as follows_router

    src = inspect.getsource(follows_router.follow_user)
    after_rollback = src[src.index("await db.rollback()"):]
    assert "target." not in after_rollback, (
        "no ORM attribute access after rollback — it is expired, and refreshing "
        "it inside AsyncSession raises MissingGreenlet"
    )
    assert "target_id = target.id" in src, "capture the id before anything rolls back"


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

    src = inspect.getsource(follows_router._page_query)
    assert "tuple_(Follow.created_at, Follow.id)" in src
    assert ".offset(" not in src


def test_every_edge_list_is_paginated_through_the_same_window():
    """`list_follow_requests` was the one list here with no cursor and no limit.

    It is also the one whose length nobody controls: `follows` has no rate
    limiting yet (CF-116) and follow-spam is the named vector, so an unbounded
    version loads every pending row plus its joined `User` and runs
    `presign_from_stored_url` once per row — a synchronous network round trip
    per avatar, on the event loop thread, sized by whoever is spamming.
    """
    import inspect

    from app.routers import follows as follows_router

    for fn in (follows_router._edge_page, follows_router.list_follow_requests):
        assert "_page_query(" in inspect.getsource(fn), (
            f"{fn.__name__} must page through the shared keyset window"
        )
    for fn in (
        follows_router.list_followers,
        follows_router.list_following,
        follows_router.list_follow_requests,
    ):
        params = inspect.signature(fn).parameters
        assert "cursor" in params and "limit" in params, f"{fn.__name__} is unbounded"


def test_the_edge_lists_do_not_publish_a_generated_handle():
    """`by_handle` guards the *lookup* direction only.

    A CF-107 backfill account — username derived from `john.smith@…`, and 404 at
    `GET /users/johnsmith` by design — can still follow a public account. Without
    a filter on the render side, any viewer reads its full `ProfileOut`,
    username included, straight off that account's follower list, and the same
    for a requester surfacing in `/users/me/follow-requests`. The rule was
    centralised on the input side only; these are the neighbours.
    """
    import inspect

    from app.routers import follows as follows_router

    for fn in (follows_router._edge_page, follows_router.list_follow_requests):
        assert "_findable(" in inspect.getsource(fn), (
            f"{fn.__name__} renders users it never filtered for a chosen handle"
        )
    assert "username_is_generated" in inspect.getsource(follows_router._findable)


def test_public_edge_lists_resolve_without_a_token():
    """The counts are anonymous, so the lists behind them should be.

    `ProfileOut` carries `follower_count` on the public profile route
    deliberately — so someone can find an account and decide to follow it. A
    signed-out visitor reading that number and then getting 401 on the list it
    describes is a lock on an open door; the private-account rule is the real
    boundary, and it needs an optional viewer, not a required one.
    """
    import inspect

    from app.routers import follows as follows_router

    for fn in (follows_router.list_followers, follows_router.list_following):
        annotation = inspect.signature(fn).parameters["viewer_id"].annotation
        assert annotation == follows_router.ViewerId, (
            f"{fn.__name__} requires a token to read a public account's list"
        )
    assert "target.is_private" in inspect.getsource(follows_router._assert_lists_visible)


def test_a_naive_cursor_timestamp_is_a_400_not_a_500():
    """`fromisoformat` accepts a naive string. asyncpg then reinterprets it as
    UTC rather than raising — measured — so an unchecked naive cursor pages
    from the wrong instant silently instead of erroring. Rejected here."""
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


def test_no_mutation_uses_an_unconditional_orm_delete():
    """SQLAlchemy's ORM delete only *warns* when it matches no rows, and commits
    whatever else is in the transaction anyway — which is how a concurrent
    unfollow decremented `follower_count` for an edge that had already gone,
    with 200s on both requests and a corrupt count behind them."""
    import inspect

    from app.routers import follows as follows_router

    for fn in (
        follows_router.unfollow_user,
        follows_router.reject_follow_request,
        follows_router.accept_follow_request,
    ):
        assert "await db.delete(" not in inspect.getsource(fn), (
            f"{fn.__name__} uses an unconditional ORM delete, which matches "
            "zero rows and warns rather than failing"
        )


def test_the_two_status_guards_branch_on_their_rowcount():
    """A guard that no-ops silently is worse than no guard.

    `unfollow` and `accept` scope their write to the status they read, which is
    what stops two concurrent callers both acting on one row. The half that was
    missing is what happens when the guard *doesn't* match: both used to answer
    as though it had. `unfollow` told a user they had revoked access while a
    concurrent accept left them an accepted follower — and since the counters
    agreed with the surviving edge, CF-116's drift job would never have surfaced
    it either.

    `reject` is deliberately not in this list; see the test below.
    """
    import inspect

    from app.routers import follows as follows_router

    for fn in (follows_router.unfollow_user, follows_router.accept_follow_request):
        src = inspect.getsource(fn)
        assert "Follow.status ==" in src, (
            f"{fn.__name__} must scope its write to the status it read"
        )
        assert "rowcount == 1" in src or "rowcount != 1" in src, (
            f"{fn.__name__} adjusts counts unconditionally"
        )
        assert "logger." in src, (
            f"{fn.__name__} must say when it lost the race — the three paths "
            "that silently no-op are exactly where a log line earns its keep"
        )


def test_reject_wins_the_race_against_accept():
    """The one asymmetry, asserted so it stays a decision.

    `_own_pending_request` 404s anything not pending, so both accept and reject
    can pass it while the row still reads `pending` — one owner on two devices,
    or a client retrying a slow accept. A `WHERE status = 'pending'` delete then
    matches nothing and 204s regardless: the owner is told the request was
    declined while the requester holds `followers`-tier access to their footage.

    Between a guard that leaves access alive and one that revokes it, the
    decline wins. RETURNING is what keeps the counters honest about which status
    was actually removed. Behaviour is covered end to end in
    `test_follows_pg.py`; this pins the shape so the "symmetric guards" tidy-up
    can't be reapplied without reading why.
    """
    import inspect

    from app.routers import follows as follows_router

    src = inspect.getsource(follows_router.reject_follow_request)
    assert ".returning(Follow.status)" in src, "counters follow the removed status"
    assert "Follow.status == FollowStatus.pending" not in src, (
        "reject must not scope its delete to `pending` — an accept that won the "
        "race would then survive a decline"
    )


def test_the_database_refuses_a_negative_counter():
    """Belt to the code's braces. The failure this review found was silent —
    a CHECK makes the next variant an error at the write that causes it.

    Searches every migration rather than naming one: this file was 015 when it
    was written and is 017 now, because a collision on main forced the whole
    stack to renumber. A test that pins a revision number breaks on every such
    shift and says nothing useful when it does.
    """
    import pathlib

    versions = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
    sql = "\n".join(p.read_text(encoding="utf-8") for p in versions.glob("*.py"))
    for col in ("follower_count", "following_count"):
        assert re.search(rf"{col}\s*>=\s*0", sql), f"no CHECK guarding {col}"


def test_the_python_half_filters_on_accepted_too():
    """The half nothing pinned.

    Every other part of this feature treats the followers tier as a security
    boundary — the fail-closed default, the pending/accepted split, the
    anonymous short-circuit, the EXISTS pinned to both ends of the edge. The one
    function that decides the answer on the single-object paths had no assertion
    on it at all: deleting `status == accepted` from is_accepted_follower left
    the entire suite green.

    What that costs is the exact drift CF-108's seam existed to prevent. A
    PENDING request would grant followers-tier access on GET /games/{id},
    /clips/{id} and /posts/{id}, while the list endpoints stayed correct because
    they go through the SQL half — the two halves disagreeing, in the direction
    that grants access, with nothing red anywhere.

    `test_followers_tier_matches_between_python_and_sql` doesn't reach it: it
    asserts can_view_game honours the boolean and that 'followers' appears in
    the SQL, both true no matter how the edge was resolved. The resolution lives
    here, in a module that test never touches.

    Needs no database — a capturing session gets at the statement.
    """
    import asyncio

    captured: list[str] = []

    class _CapturingSession:
        async def execute(self, stmt):
            captured.append(_sql(stmt).lower())

            class _Result:
                def first(self):
                    return None

            return _Result()

    asyncio.run(
        follow_graph.is_accepted_follower(_CapturingSession(), VIEWER, OWNER)
    )

    assert captured, "is_accepted_follower must actually query"
    sql = captured[0]
    assert "'accepted'" in sql, (
        "the Python half must filter on status='accepted' — without it a PENDING "
        "request grants followers-tier access on every single-object read, while "
        "the SQL half stays correct and no existing test notices"
    )
    assert "'pending'" not in sql
    # Same scoping the SQL half is held to: this viewer following this owner,
    # not any accepted edge anywhere.
    assert "follower_id" in sql, "must pin the viewer end"
    assert "followee_id" in sql, "must pin the owner end"


def test_both_halves_are_pinned_not_just_the_sql_one():
    """Guards the pair rather than each side alone.

    The gap above existed because the SQL half had two tests naming 'accepted'
    and the Python half had none — an asymmetry nothing declared. This states it
    as a requirement so a third resolver can't arrive unpinned.
    """
    import inspect

    for fn in (follow_graph.is_accepted_follower, access.accepted_follow_exists):
        assert "accepted" in inspect.getsource(fn), (
            f"{fn.__module__}.{fn.__name__} must constrain status to accepted"
        )


def test_every_read_gate_honours_the_follow_edge():
    """`can_identify` was the one that didn't.

    It delegates to `can_view_game`, whose `viewer_follows_owner` defaults to
    False — correct before CF-110, when `followers` resolved False for
    everyone, and silently wrong the moment an accepted follower could read a
    `followers`-tier clip. The delegate then answered a different question than
    `can_view_clip` did for the same viewer: 200 on the download, filename
    stripped of the game title and the player's name, while `list_clips` handed
    that same viewer `player_name` from SQL.

    It fails closed, which is why nothing caught it. Asserted over the set so
    the next gate added here cannot be the one that forgets.
    """
    import inspect

    for fn in (
        access.can_view_game,
        access.can_view_clip,
        access.can_view_post,
        access.can_identify,
        access.assert_can_view_game,
    ):
        params = inspect.signature(fn).parameters
        assert "viewer_follows_owner" in params or "viewer_follows_author" in params, (
            f"{fn.__name__} cannot see the follow edge, so it answers as if "
            "nobody follows anybody"
        )


def test_can_identify_agrees_with_can_view_clip_for_a_follower():
    """The two answers, side by side, over the case that made them differ."""

    class _Clip:
        def __init__(self, game_id):
            self.game_id = game_id
            self.visibility = None  # inherit — every clip the pipeline produces

    game = _Game(Visibility.followers)
    clip = _Clip(game.id)

    assert access.can_view_clip(VIEWER, clip, game, viewer_follows_owner=True) is True
    assert access.can_identify(VIEWER, game, viewer_follows_owner=True) is True
    # And still closed for a viewer with no edge.
    assert access.can_identify(VIEWER, game) is False


def test_the_counter_checks_reach_the_metadata():
    """Migration 017 creates them; `Base.metadata` has to know.

    Every `*_pg.py` fixture builds its schema with `Base.metadata.create_all`,
    so a constraint declared only in the migration is absent from the database
    the tests actually run against. `test_unfollowing_from_a_zero_counter_still_revokes`
    is the one that cared: its subject is the CHECK aborting a transaction, and
    without the constraint present it verified the `GREATEST` floor alone while
    reading as though it covered both.
    """
    from app.models.user import User

    names = {c.name for c in User.__table__.constraints if c.name}
    assert "ck_users_follower_count_non_negative" in names
    assert "ck_users_following_count_non_negative" in names
