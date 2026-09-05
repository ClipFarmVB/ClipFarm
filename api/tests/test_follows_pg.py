"""CF-110: the follow router's handlers, executed against a real Postgres.

**Why this exists.** The rest of `test_follows.py` asserts on source text —
`inspect.getsource(...)` substring matches and predicate shapes — and the review
of #191 showed what that buys: three of those tests were green over broken code.
`test_the_counter_update_is_inside_the_integrity_guard` passed while the
recovery path it guards raised `MissingGreenlet`;
`test_all_three_mutations_guard_their_write_symmetrically` was satisfied by a
`reject` that 204s having deleted nothing. A source-text assertion cannot fail
for the reason it was written, because the thing it inspects is the thing the
author already believed.

So: a throwaway database, the real tables, real rows, and the actual coroutines.
The follow/accept/unfollow cycle, the counters it moves, the cursor round-trip,
and the two race outcomes that a guard alone left describing the wrong world.

The fixture pattern — throwaway database, `Base.metadata.create_all`, discovery
through `tests/_pg.py` (localhost only, never `settings.database_url`) — is
`test_posts_visibility_pg.py`'s, for the reasons its docstring gives: the schema
is Postgres-specific (native enums, UUID columns), CI already runs a
`postgres:16` service for the CF-184 lock tests, and locally the compose `db`
service is enough.

Races are driven by writing the interleaving directly rather than by racing two
requests: the interesting states are "the row changed between the read and the
guarded write", and a second session committing that change is a faithful and
deterministic way to produce it. What is under test is how the handler answers
once it has lost, which is where the bugs were.
"""
import asyncio
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")
pytest.importorskip("psycopg2")
pytest.importorskip("asyncpg")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from tests._pg import pg_url  # noqa: E402


@pytest.fixture(scope="module")
def pg_db():
    """A throwaway database carrying the models' schema."""
    import psycopg2
    from sqlalchemy import create_engine

    admin_url = pg_url("the follow graph needs a real server")
    name = f"clipfarm_follows_{uuid.uuid4().hex[:12]}"

    conn = psycopg2.connect(admin_url)
    conn.autocommit = True                      # CREATE DATABASE cannot be in one
    with conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    conn.close()

    target = admin_url.rsplit("/", 1)[0] + "/" + name
    try:
        from app.database import Base
        import app.models  # noqa: F401  — registers every table on Base.metadata

        sync = create_engine(target)
        Base.metadata.create_all(sync)
        sync.dispose()
        yield target
    finally:
        conn = psycopg2.connect(admin_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            # Terminate stragglers first: a pool can outlive the test, and DROP
            # DATABASE fails while any session is attached — which would leak a
            # database per run instead of failing loudly.
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        conn.close()


@pytest.fixture
def people(pg_db, monkeypatch):
    """Four users: a viewer, a public target, a private target, and a backfill
    account whose handle was never chosen.

    Returns `(async_url, ids)` keyed `viewer`, `pub`, `priv`, `generated`.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models.user import User
    from app.services import storage

    # `serialize` presigns when R2 is configured; these tests are about rows.
    monkeypatch.setattr(storage, "r2_configured", lambda: False)

    def _user(handle: str, *, private: bool = False, generated: bool = False) -> User:
        return User(
            id=uuid.uuid4(),
            email=f"{uuid.uuid4().hex}@test.local",
            username=handle,
            display_name=handle.title(),
            username_is_generated=generated,
            is_private=private,
        )

    rows = {
        "viewer": _user("viewer"),
        "pub": _user("publictarget"),
        "priv": _user("privatetarget", private=True),
        "generated": _user("johnsmith", generated=True),
    }
    ids = {k: u.id for k, u in rows.items()}

    sync = create_engine(pg_db)
    with Session(sync) as s:
        s.execute(text("TRUNCATE users, follows CASCADE"))
        for row in rows.values():
            s.add(row)
        s.commit()
    sync.dispose()

    return pg_db.replace("postgresql://", "postgresql+asyncpg://"), ids


def _run(async_url, fn):
    """Run one handler coroutine against a fresh session."""

    async def go():
        engine = create_async_engine(async_url)
        try:
            async with AsyncSession(engine) as db:
                await db.execute(text("SELECT 1"))      # fail loudly on a bad URL
                return await fn(db)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _counts(async_url, user_id):
    """`(follower_count, following_count)` straight from the row."""
    from sqlalchemy import create_engine

    sync = create_engine(async_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        with sync.connect() as c:
            return c.execute(
                text(
                    "SELECT follower_count, following_count FROM users WHERE id = :i"
                ),
                {"i": user_id},
            ).one()
    finally:
        sync.dispose()


def _set_status(async_url, follower_id, followee_id, status):
    """Commit a status change from a second session — the concurrent writer."""
    from sqlalchemy import create_engine

    sync = create_engine(async_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        with sync.begin() as c:
            c.execute(
                text(
                    "UPDATE follows SET status = :s WHERE follower_id = :a "
                    "AND followee_id = :b"
                ),
                {"s": status, "a": follower_id, "b": followee_id},
            )
    finally:
        sync.dispose()


def _edge_status(async_url, follower_id, followee_id):
    from sqlalchemy import create_engine

    sync = create_engine(async_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        with sync.connect() as c:
            row = c.execute(
                text(
                    "SELECT status FROM follows WHERE follower_id = :a "
                    "AND followee_id = :b"
                ),
                {"a": follower_id, "b": followee_id},
            ).first()
            return row[0] if row else None
    finally:
        sync.dispose()


# ── the ordinary cycle ────────────────────────────────────────────────────────


def test_following_a_public_account_is_accepted_and_moves_both_counters(people):
    """The counter arithmetic, verified against the rows rather than the source."""
    from app.routers import follows as r

    async_url, ids = people

    state = _run(async_url, lambda db: r.follow_user("publictarget", ids["viewer"], db))
    assert state.status.value == "accepted" and state.following is True
    assert _counts(async_url, ids["pub"])[0] == 1        # follower_count
    assert _counts(async_url, ids["viewer"])[1] == 1     # following_count

    # Idempotent: a second tap returns state, does not create a second edge, and
    # above all does not increment again.
    again = _run(async_url, lambda db: r.follow_user("publictarget", ids["viewer"], db))
    assert again.following is True
    assert _counts(async_url, ids["pub"])[0] == 1

    _run(async_url, lambda db: r.unfollow_user("publictarget", ids["viewer"], db))
    assert _counts(async_url, ids["pub"])[0] == 0
    assert _counts(async_url, ids["viewer"])[1] == 0
    assert _edge_status(async_url, ids["viewer"], ids["pub"]) is None


def test_a_private_account_holds_the_edge_pending_and_counts_nothing(people):
    """Pending must grant nothing and show nothing — counting it would leak that
    someone asked."""
    from app.routers import follows as r

    async_url, ids = people

    state = _run(async_url, lambda db: r.follow_user("privatetarget", ids["viewer"], db))
    assert state.status.value == "pending" and state.following is False
    assert _counts(async_url, ids["priv"])[0] == 0

    page = _run(async_url, lambda db: r.list_follow_requests(ids["priv"], db))
    assert [i.requester.username for i in page.items] == ["viewer"]

    accepted = _run(
        async_url, lambda db: r.accept_follow_request(page.items[0].id, ids["priv"], db)
    )
    assert accepted.following is True
    assert _counts(async_url, ids["priv"])[0] == 1


def test_the_python_and_sql_halves_agree_on_a_real_edge(people):
    """The seam CF-108 left, closed over rows.

    `is_accepted_follower` is the single-object half; a pending edge must answer
    False on it, an accepted edge True. Everything else asserts this against
    generated SQL text.
    """
    from app.routers import follows as r
    from app.services import follow_graph

    async_url, ids = people

    _run(async_url, lambda db: r.follow_user("privatetarget", ids["viewer"], db))
    assert (
        _run(
            async_url,
            lambda db: follow_graph.is_accepted_follower(db, ids["viewer"], ids["priv"]),
        )
        is False
    ), "a pending request must not resolve the followers tier"

    _set_status(async_url, ids["viewer"], ids["priv"], "accepted")
    assert (
        _run(
            async_url,
            lambda db: follow_graph.is_accepted_follower(db, ids["viewer"], ids["priv"]),
        )
        is True
    )


# ── the races the guards used to answer wrongly ───────────────────────────────


def test_unfollow_that_loses_to_an_accept_says_so(people):
    """The user taps Withdraw as the target accepts.

    The guarded DELETE matches nothing. Answering `following: false` there tells
    the user they revoked access while they remain an accepted follower — and
    because the counters agree with the surviving edge, CF-116's drift job never
    surfaces it. The honest answer is the row that exists.
    """
    from app.routers import follows as r

    async_url, ids = people

    _run(async_url, lambda db: r.follow_user("privatetarget", ids["viewer"], db))

    async def interleaved(db):
        # The handler reads the edge as `pending`; the accept commits before its
        # guarded DELETE runs. `_edge` is the seam because it *is* the read the
        # guard is built on — wrapping it lands the concurrent write in the one
        # window that matters, deterministically.
        original = r._edge
        fired = []

        async def once(session, a, b):
            edge = await original(session, a, b)
            if not fired:
                fired.append(True)
                _set_status(async_url, ids["viewer"], ids["priv"], "accepted")
            return edge

        r._edge = once
        try:
            return await r.unfollow_user("privatetarget", ids["viewer"], db)
        finally:
            r._edge = original

    state = _run(async_url, interleaved)
    assert _edge_status(async_url, ids["viewer"], ids["priv"]) == "accepted", (
        "precondition: the accept won"
    )
    assert state.following is True, (
        "the user is still an accepted follower — reporting `following: false` "
        "renders a Follow button over live access that a second tap won't revoke"
    )


def test_reject_revokes_an_accept_that_won_the_race(people):
    """One owner, two devices: accept lands, then reject.

    Both pass `_own_pending_request` while the row reads `pending`. A delete
    scoped to `pending` matches nothing and 204s anyway — the owner is told the
    request was declined while the requester holds `followers`-tier access. The
    decline has to win, and the counters have to follow the status that was
    actually removed.
    """
    from app.routers import follows as r

    async_url, ids = people

    _run(async_url, lambda db: r.follow_user("privatetarget", ids["viewer"], db))
    page = _run(async_url, lambda db: r.list_follow_requests(ids["priv"], db))
    request_id = page.items[0].id

    _run(async_url, lambda db: r.accept_follow_request(request_id, ids["priv"], db))
    assert _counts(async_url, ids["priv"])[0] == 1

    # The reject arrives with the row already accepted. `_own_pending_request`
    # would 404 a *sequential* one — the race is that both passed it while the
    # row was pending — so this drives the delete directly, which is the part
    # whose scoping was wrong.
    _run(async_url, lambda db: _reject_racing(r, request_id, ids["priv"], db))

    assert _edge_status(async_url, ids["viewer"], ids["priv"]) is None, (
        "the decline must revoke the access the accept granted"
    )
    assert _counts(async_url, ids["priv"])[0] == 0, (
        "the counter must follow the status that was actually removed"
    )


async def _reject_racing(r, request_id, owner_id, db):
    """`reject_follow_request` with its pending precondition already passed.

    The guard is not what is under test — the interleaving is, and it reaches
    the delete by both callers passing `_own_pending_request` while the row
    still read `pending`.
    """
    original = r._own_pending_request

    async def passed(rid, uid, session):
        from app.models.follow import Follow

        return await session.get(Follow, rid)

    r._own_pending_request = passed
    try:
        return await r.reject_follow_request(request_id, owner_id, db)
    finally:
        r._own_pending_request = original


def test_a_stranded_request_is_promoted_when_the_account_goes_public(people):
    """Request while private, target flips public, press Follow again.

    `follow_user` short-circuits on any existing edge, so without the promotion
    the requester waits on an approval queue the target no longer has, while
    everyone arriving after the flip is accepted outright.
    """
    from app.routers import follows as r

    async_url, ids = people

    _run(async_url, lambda db: r.follow_user("privatetarget", ids["viewer"], db))

    from sqlalchemy import create_engine

    sync = create_engine(async_url.replace("postgresql+asyncpg://", "postgresql://"))
    with sync.begin() as c:
        c.execute(
            text("UPDATE users SET is_private = false WHERE id = :i"), {"i": ids["priv"]}
        )
    sync.dispose()

    state = _run(async_url, lambda db: r.follow_user("privatetarget", ids["viewer"], db))
    assert state.following is True
    assert _counts(async_url, ids["priv"])[0] == 1, "promotion must move the counters"


def test_a_promote_that_wrote_nothing_does_not_claim_to_have_followed(people):
    """The third mutation, held to the same standard as the other two.

    The counters were guarded on `rowcount` and the response was not, so a
    promote matching zero rows still answered `accepted / following: true`.
    Withdraw on one device while tapping Follow on another: the DELETE lands
    first, the UPDATE matches nothing, and the client renders Following over an
    edge that does not exist — while the very next `follow-state` call says
    `null`. The same lie `unfollow` and `reject` were rewritten to stop telling.
    """
    from sqlalchemy import create_engine

    from app.routers import follows as r

    async_url, ids = people

    _run(async_url, lambda db: r.follow_user("privatetarget", ids["viewer"], db))
    sync = create_engine(async_url.replace("postgresql+asyncpg://", "postgresql://"))
    with sync.begin() as c:
        c.execute(
            text("UPDATE users SET is_private = false WHERE id = :i"), {"i": ids["priv"]}
        )
    sync.dispose()

    async def interleaved(db):
        # The handler reads a `pending` edge; the withdrawal commits before its
        # guarded UPDATE runs. `_edge` is the read the guard is built on.
        original = r._edge
        fired = []

        async def once(session, a, b):
            edge = await original(session, a, b)
            if not fired:
                fired.append(True)
                inner = create_engine(
                    async_url.replace("postgresql+asyncpg://", "postgresql://")
                )
                with inner.begin() as c:
                    c.execute(
                        text(
                            "DELETE FROM follows WHERE follower_id = :a "
                            "AND followee_id = :b"
                        ),
                        {"a": ids["viewer"], "b": ids["priv"]},
                    )
                inner.dispose()
            return edge

        r._edge = once
        try:
            return await r.follow_user("privatetarget", ids["viewer"], db)
        finally:
            r._edge = original

    state = _run(async_url, interleaved)

    assert _edge_status(async_url, ids["viewer"], ids["priv"]) is None, (
        "precondition: the withdrawal won"
    )
    assert state.following is False, "must not report a follow it did not write"
    assert state.status is None
    assert _counts(async_url, ids["priv"])[0] == 0, "and must not move the counters"


def test_unfollowing_from_a_zero_counter_still_revokes(people):
    """The CHECK must not be able to wedge a revocation.

    `follows` cascades on user delete without adjusting the other side, so the
    counters are known not to be authoritative. If one reaches 0 while an
    accepted edge exists, an unguarded decrement hits
    `ck_users_follower_count_non_negative`, and because the commit isn't wrapped
    the whole transaction rolls back — *including the DELETE*. Every retry does
    the same, and the follower keeps access with no way to revoke it.
    """
    from app.routers import follows as r
    from sqlalchemy import create_engine

    async_url, ids = people

    _run(async_url, lambda db: r.follow_user("publictarget", ids["viewer"], db))

    sync = create_engine(async_url.replace("postgresql+asyncpg://", "postgresql://"))
    with sync.begin() as c:
        c.execute(
            text("UPDATE users SET follower_count = 0 WHERE id = :i"), {"i": ids["pub"]}
        )
    sync.dispose()

    state = _run(async_url, lambda db: r.unfollow_user("publictarget", ids["viewer"], db))
    assert state.following is False
    assert _edge_status(async_url, ids["viewer"], ids["pub"]) is None, (
        "the edge must go even when the counter cannot be decremented"
    )
    assert _counts(async_url, ids["pub"])[0] == 0, "floored, not negative"


# ── what the lists publish ────────────────────────────────────────────────────


def test_a_generated_handle_never_appears_in_an_edge_list(people):
    """`by_handle` guards the lookup direction only.

    The backfill account can follow a public one; the leak is on the render
    side. `GET /users/publictarget` is fine, `GET /users/johnsmith` 404s — and
    the follower list must not undo that.
    """
    from app.routers import follows as r

    async_url, ids = people

    # It cannot reach the endpoint (by_handle 404s its own handle, not the
    # target's), so the edge is written directly — which is also how a backfill
    # account that followed before CF-107 would already be sitting in the table.
    from sqlalchemy import create_engine

    sync = create_engine(async_url.replace("postgresql+asyncpg://", "postgresql://"))
    with sync.begin() as c:
        c.execute(
            text(
                "INSERT INTO follows (id, follower_id, followee_id, status, created_at)"
                " VALUES (:i, :a, :b, 'accepted', now())"
            ),
            {"i": uuid.uuid4(), "a": ids["generated"], "b": ids["pub"]},
        )
    sync.dispose()

    page = _run(
        async_url,
        lambda db: r.list_followers("publictarget", db, viewer_id=ids["viewer"]),
    )
    assert [i.user.username for i in page.items] == [], (
        "a handle the profile route refuses to resolve must not be published by "
        "the follower list either"
    )


def test_a_public_accounts_lists_resolve_for_a_signed_out_visitor(people):
    """The counts are anonymous; the lists behind them should be."""
    from app.routers import follows as r

    async_url, ids = people

    _run(async_url, lambda db: r.follow_user("publictarget", ids["viewer"], db))
    page = _run(async_url, lambda db: r.list_followers("publictarget", db, viewer_id=None))
    assert [i.user.username for i in page.items] == ["viewer"]


def test_a_private_accounts_lists_stay_owner_only(people):
    """Who follows you is information about you."""
    from app.routers import follows as r

    async_url, ids = people

    with pytest.raises(HTTPException) as exc:
        _run(
            async_url,
            lambda db: r.list_followers("privatetarget", db, viewer_id=ids["viewer"]),
        )
    assert exc.value.status_code == 404

    # The owner sees their own.
    page = _run(
        async_url, lambda db: r.list_followers("privatetarget", db, viewer_id=ids["priv"])
    )
    assert page.items == []


def test_the_cursor_round_trips_through_a_real_page(people):
    """Paging, actually paged.

    The tiebreaker test asserts the ORDER BY names `id`; this walks two pages of
    size 1 and checks the second is the row the first left off at, rather than
    the same row again.
    """
    from app.routers import follows as r
    from sqlalchemy import create_engine

    async_url, ids = people

    # Two edges sharing a created_at — the tie the cursor's id half exists for.
    sync = create_engine(async_url.replace("postgresql+asyncpg://", "postgresql://"))
    with sync.begin() as c:
        for follower in (ids["viewer"], ids["priv"]):
            c.execute(
                text(
                    "INSERT INTO follows (id, follower_id, followee_id, status,"
                    " created_at) VALUES (:i, :a, :b, 'accepted',"
                    " '2026-01-01T00:00:00+00:00')"
                ),
                {"i": uuid.uuid4(), "a": follower, "b": ids["pub"]},
            )
    sync.dispose()

    first = _run(
        async_url,
        lambda db: r.list_followers(
            "publictarget", db, viewer_id=ids["viewer"], limit=1
        ),
    )
    assert len(first.items) == 1 and first.next_cursor

    second = _run(
        async_url,
        lambda db: r.list_followers(
            "publictarget", db, viewer_id=ids["viewer"], cursor=first.next_cursor, limit=1
        ),
    )
    assert len(second.items) == 1
    assert second.items[0].user.username != first.items[0].user.username, (
        "a cursor on created_at alone would return the same tied row again"
    )
    assert second.next_cursor is None, "two rows, two pages"


def test_an_empty_cursor_is_rejected_on_every_follows_list(people):
    """CF-111's contract change, pinned on the endpoints it actually changed.

    Extracting `services/cursors` also moved these three from `if cursor:` to
    `if cursor is not None:`, so `?cursor=` went from silently returning page 1
    — a client emitting an empty template value scrolling forever without
    advancing — to a 400. That is the better contract and it is CF-111 that
    made it, on endpoints CF-110 had already shipped. The feed pinned it for
    itself; these three had nothing, so the change was invisible in the diff
    that caused it.
    """
    from fastapi import HTTPException

    from app.routers import follows as r

    async_url, ids = people

    calls = (
        ("followers", lambda db: r.list_followers(
            "publictarget", db, viewer_id=ids["viewer"], cursor="")),
        ("following", lambda db: r.list_following(
            "publictarget", db, viewer_id=ids["viewer"], cursor="")),
        ("follow-requests", lambda db: r.list_follow_requests(
            ids["priv"], db, cursor="")),
    )
    for name, call in calls:
        with pytest.raises(HTTPException) as exc:
            _run(async_url, call)
        assert exc.value.status_code == 400, f"{name} accepted an empty cursor"
