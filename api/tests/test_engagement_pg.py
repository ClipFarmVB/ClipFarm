"""CF-113: likes and comments against a real Postgres.

The card's acceptance criteria are all about rows and races — liking twice
yields one like; counts match reality after concurrent likes; a viewer who
cannot see a post cannot like or comment on it; deletion respects the
author-or-owner rule. None of those are checkable against a fake session, and
the fake-session tests in `test_engagement.py` say so. This file drives the
real routers against a throwaway database, the `test_feed_pg.py` /
`test_follows_pg.py` pattern: Postgres-specific schema, a `postgres:16`
service in CI, discovery through `tests/_pg.py`, never `settings.database_url`.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

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
    import psycopg2
    from sqlalchemy import create_engine

    admin_url = pg_url("the engagement routers need a real server")
    name = f"clipfarm_engagement_{uuid.uuid4().hex[:12]}"

    conn = psycopg2.connect(admin_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    conn.close()

    target = admin_url.rsplit("/", 1)[0] + "/" + name
    try:
        from app.database import Base
        import app.models  # noqa: F401

        sync = create_engine(target)
        Base.metadata.create_all(sync)
        sync.dispose()
        yield target
    finally:
        conn = psycopg2.connect(admin_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        conn.close()


@pytest.fixture
def world(pg_db, monkeypatch):
    """An author with a public, a followers-tier and a private post; a
    follower (accepted), a pending requester, a stranger, and a backfill
    account with a generated handle. Returns `(async_url, ids)`."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models.clip import ActionType, Clip
    from app.models.follow import Follow, FollowStatus
    from app.models.game import Game
    from app.models.post import Post
    from app.models.user import User
    from app.models.visibility import Visibility
    from app.services import storage

    monkeypatch.setattr(storage, "r2_configured", lambda: False)

    rows: list[object] = []
    ids: dict[str, uuid.UUID] = {}

    def _user(key, handle, *, generated=False):
        u = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@test.local",
                 username=handle, display_name=handle.title(), username_is_generated=generated)
        rows.append(u)
        ids[key] = u.id
        return u

    author = _user("author", "author")
    follower = _user("follower", "follower")
    pending = _user("pending", "pending")
    _user("stranger", "stranger")
    _user("backfill", "johnsmith", generated=True)
    extras = [_user(f"u{i}", f"user{i}") for i in range(20)]

    def _post(key, tier):
        game = Game(id=uuid.uuid4(), owner_id=author.id, title="g", visibility=tier)
        clip = Clip(id=uuid.uuid4(), game_id=game.id, action_type=ActionType.spike,
                    confidence=0.9, start_time=1.5, end_time=4.5, visibility=None)
        post = Post(id=uuid.uuid4(), author_id=author.id, clip_id=clip.id,
                    caption="hi", visibility=tier)
        rows.extend([game, clip, post])
        ids[key] = post.id

    _post("public", Visibility.public)
    _post("tier", Visibility.followers)
    _post("private", Visibility.private)

    rows.append(Follow(id=uuid.uuid4(), follower_id=follower.id, followee_id=author.id,
                       status=FollowStatus.accepted))
    rows.append(Follow(id=uuid.uuid4(), follower_id=pending.id, followee_id=author.id,
                       status=FollowStatus.pending))
    for u in extras:
        rows.append(Follow(id=uuid.uuid4(), follower_id=u.id, followee_id=author.id,
                           status=FollowStatus.accepted))

    sync = create_engine(pg_db)
    with Session(sync) as s:
        s.execute(text("TRUNCATE users, games, clips, posts, follows, post_likes, post_comments CASCADE"))
        for row in rows:
            s.add(row)
            s.flush()
        s.commit()
    sync.dispose()

    return pg_db.replace("postgresql://", "postgresql+asyncpg://"), ids


def _run(async_url, coro_factory):
    """Run one router coroutine against a fresh session."""

    async def go():
        engine = create_async_engine(async_url)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                return await coro_factory(db)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _scalar(async_url, sql, **params):
    from sqlalchemy import create_engine

    sync = create_engine(async_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        with sync.begin() as c:
            return c.execute(text(sql), params).scalar_one()
    finally:
        sync.dispose()


# ── liking twice yields one like ─────────────────────────────────────────────


def test_liking_twice_yields_one_like_and_one_increment(world):
    from app.routers import engagement as r
    from app.routers import posts as posts_router

    async_url, ids = world
    pid, who = ids["public"], ids["follower"]

    first = _run(async_url, lambda db: r.like_post(pid, who, db))
    second = _run(async_url, lambda db: r.like_post(pid, who, db))
    assert (first.liked, first.like_count) == (True, 1)
    assert (second.liked, second.like_count) == (True, 1), "the double-tap changed nothing"
    assert _scalar(async_url, "SELECT count(*) FROM post_likes WHERE post_id = :p", p=pid) == 1
    assert _scalar(async_url, "SELECT like_count FROM posts WHERE id = :p", p=pid) == 1

    seen = _run(async_url, lambda db: posts_router.get_post(pid, db, who))
    assert seen.viewer_has_liked is True and seen.like_count == 1
    other = _run(async_url, lambda db: posts_router.get_post(pid, db, ids["stranger"]))
    assert other.viewer_has_liked is False

    gone = _run(async_url, lambda db: r.unlike_post(pid, who, db))
    again = _run(async_url, lambda db: r.unlike_post(pid, who, db))
    assert (gone.liked, gone.like_count) == (False, 0)
    assert (again.liked, again.like_count) == (False, 0), "floored, and idempotent"


def test_concurrent_likes_leave_the_counter_equal_to_the_rows(world):
    """The card's "counts match reality after concurrent likes", driven for
    real: twenty distinct users and one duplicate, all at once, on separate
    sessions. `UPDATE ... SET x = x + 1` cannot lose an increment; the PK
    cannot admit the duplicate."""
    from app.routers import engagement as r

    async_url, ids = world
    pid = ids["public"]
    users = [ids[f"u{i}"] for i in range(20)] + [ids["u0"]]

    async def go():
        engine = create_async_engine(async_url)
        try:
            async def one(uid):
                async with AsyncSession(engine, expire_on_commit=False) as db:
                    return await r.like_post(pid, uid, db)

            return await asyncio.gather(*(one(u) for u in users))
        finally:
            await engine.dispose()

    results = asyncio.run(go())
    assert all(res.liked for res in results)
    rows = _scalar(async_url, "SELECT count(*) FROM post_likes WHERE post_id = :p", p=pid)
    count = _scalar(async_url, "SELECT like_count FROM posts WHERE id = :p", p=pid)
    assert rows == 20 and count == 20, (rows, count)

    async def unlike_half():
        engine = create_async_engine(async_url)
        try:
            async def one(uid):
                async with AsyncSession(engine, expire_on_commit=False) as db:
                    return await r.unlike_post(pid, uid, db)

            return await asyncio.gather(*(one(ids[f"u{i}"]) for i in range(10)))
        finally:
            await engine.dispose()

    asyncio.run(unlike_half())
    assert _scalar(async_url, "SELECT like_count FROM posts WHERE id = :p", p=pid) == 10
    assert _scalar(async_url, "SELECT count(*) FROM post_likes WHERE post_id = :p", p=pid) == 10


# ── a user who cannot see a post cannot like or comment on it ────────────────


def test_the_invisible_post_cannot_be_liked_or_commented(world):
    from app.routers import engagement as r
    from app.schemas.engagement import CommentCreate

    async_url, ids = world

    def _denied(pid, who):
        for call in (
            lambda db: r.like_post(pid, who, db),
            lambda db: r.unlike_post(pid, who, db),
            lambda db: r.create_comment(pid, CommentCreate(body="hi"), who, db),
            lambda db: r.list_comments(pid, db, who),
        ):
            with pytest.raises(HTTPException) as exc:
                _run(async_url, call)
            assert exc.value.status_code == 404

    # followers-tier: a stranger and a *pending* requester see nothing.
    _denied(ids["tier"], ids["stranger"])
    _denied(ids["tier"], ids["pending"])
    # private: even an accepted follower is out.
    _denied(ids["private"], ids["follower"])
    # anonymous cannot list comments on either.
    for key in ("tier", "private"):
        with pytest.raises(HTTPException):
            _run(async_url, lambda db: r.list_comments(ids[key], db, None))

    for key in ("tier", "private"):
        assert _scalar(async_url, "SELECT like_count + comment_count FROM posts WHERE id = :p", p=ids[key]) == 0
        assert _scalar(async_url, "SELECT count(*) FROM post_likes WHERE post_id = :p", p=ids[key]) == 0

    # And the accepted follower is in on the followers tier.
    liked = _run(async_url, lambda db: r.like_post(ids["tier"], ids["follower"], db))
    assert liked.like_count == 1
    made = _run(async_url, lambda db: r.create_comment(
        ids["tier"], CommentCreate(body="nice dig"), ids["follower"], db))
    assert made.body == "nice dig" and made.author.username == "follower"
    page = _run(async_url, lambda db: r.list_comments(ids["tier"], db, ids["follower"]))
    assert [c.id for c in page.items] == [made.id]


# ── deletion respects the author-or-owner rule ───────────────────────────────


def test_comment_deletion_is_author_or_post_owner(world):
    from app.routers import engagement as r
    from app.schemas.engagement import CommentCreate

    async_url, ids = world
    pid = ids["public"]

    mine = _run(async_url, lambda db: r.create_comment(pid, CommentCreate(body="mine"), ids["follower"], db))
    theirs = _run(async_url, lambda db: r.create_comment(pid, CommentCreate(body="theirs"), ids["stranger"], db))
    assert _scalar(async_url, "SELECT comment_count FROM posts WHERE id = :p", p=pid) == 2

    # A third party cannot.
    with pytest.raises(HTTPException) as exc:
        _run(async_url, lambda db: r.delete_comment(theirs.id, ids["follower"], db))
    assert exc.value.status_code == 404
    assert _scalar(async_url, "SELECT deleted_at IS NULL FROM post_comments WHERE id = :c", c=theirs.id)

    # The comment's author can; the count follows; the list no longer shows it.
    _run(async_url, lambda db: r.delete_comment(mine.id, ids["follower"], db))
    assert _scalar(async_url, "SELECT comment_count FROM posts WHERE id = :p", p=pid) == 1
    page = _run(async_url, lambda db: r.list_comments(pid, db, None))
    assert [c.id for c in page.items] == [theirs.id]

    # A second delete matches nothing: 404, and no second decrement.
    with pytest.raises(HTTPException):
        _run(async_url, lambda db: r.delete_comment(mine.id, ids["follower"], db))
    assert _scalar(async_url, "SELECT comment_count FROM posts WHERE id = :p", p=pid) == 1

    # The post's author can remove anyone's.
    _run(async_url, lambda db: r.delete_comment(theirs.id, ids["author"], db))
    assert _scalar(async_url, "SELECT comment_count FROM posts WHERE id = :p", p=pid) == 0


def test_a_comment_delete_from_a_zero_counter_still_hides_it(world):
    """The CHECK is present (the metadata declares it — asserted by trying to
    violate it) and the router's `GREATEST` floor is what keeps a drifted
    counter from wedging the delete."""
    from sqlalchemy.exc import IntegrityError

    from app.routers import engagement as r
    from app.schemas.engagement import CommentCreate

    async_url, ids = world
    pid = ids["public"]
    made = _run(async_url, lambda db: r.create_comment(pid, CommentCreate(body="x"), ids["follower"], db))

    with pytest.raises(IntegrityError):
        _scalar(async_url, "UPDATE posts SET comment_count = -1 WHERE id = :p RETURNING 1", p=pid)
    _scalar(async_url, "UPDATE posts SET comment_count = 0 WHERE id = :p RETURNING 1", p=pid)

    _run(async_url, lambda db: r.delete_comment(made.id, ids["follower"], db))
    assert _scalar(async_url, "SELECT deleted_at IS NOT NULL FROM post_comments WHERE id = :c", c=made.id)
    assert _scalar(async_url, "SELECT comment_count FROM posts WHERE id = :p", p=pid) == 0


# ── paging ───────────────────────────────────────────────────────────────────


def test_the_comment_cursor_round_trips_a_tied_timestamp(world):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models.post_comment import PostComment
    from app.routers import engagement as r

    async_url, ids = world
    pid = ids["public"]
    when = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    a, b, c = (uuid.uuid4() for _ in range(3))

    sync = create_engine(async_url.replace("postgresql+asyncpg://", "postgresql://"))
    with Session(sync) as s:
        for cid, t in ((a, when), (b, when), (c, when - timedelta(minutes=1))):
            s.add(PostComment(id=cid, post_id=pid, author_id=ids["stranger"], body="t", created_at=t))
        s.commit()
    sync.dispose()

    first = _run(async_url, lambda db: r.list_comments(pid, db, None, cursor=None, limit=1))
    assert len(first.items) == 1 and first.next_cursor
    second = _run(async_url, lambda db: r.list_comments(pid, db, None, cursor=first.next_cursor, limit=1))
    assert len(second.items) == 1 and second.next_cursor
    third = _run(async_url, lambda db: r.list_comments(pid, db, None, cursor=second.next_cursor, limit=1))
    assert [x.id for x in third.items] == [c] and third.next_cursor is None
    assert {first.items[0].id, second.items[0].id} == {a, b}, "the tie is split by id, not skipped"


def test_a_generated_handle_commenter_is_withheld_but_the_comment_stays(world):
    from app.routers import engagement as r
    from app.schemas.engagement import CommentCreate

    async_url, ids = world
    pid = ids["public"]
    _run(async_url, lambda db: r.create_comment(pid, CommentCreate(body="hello"), ids["backfill"], db))
    page = _run(async_url, lambda db: r.list_comments(pid, db, None))
    assert [c.body for c in page.items] == ["hello"]
    assert page.items[0].author.username is None, "the handle is withheld"
    assert _scalar(async_url, "SELECT comment_count FROM posts WHERE id = :p", p=pid) == 1


# ── the feed carries viewer_has_liked from the same query ────────────────────


def test_viewer_has_liked_is_filled_on_the_feed_from_the_page_query(world):
    from sqlalchemy import event

    from app.routers import engagement as r
    from app.routers import feed as feed_router

    async_url, ids = world
    _run(async_url, lambda db: r.like_post(ids["public"], ids["follower"], db))

    statements: list[str] = []

    async def go():
        engine = create_async_engine(async_url)

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        try:
            async with AsyncSession(engine) as db:
                return await feed_router.get_feed(db=db, user_id=ids["follower"])
        finally:
            await engine.dispose()

    page = asyncio.run(go())
    by_id = {card.id: card for card in page.items}
    assert by_id[ids["public"]].viewer_has_liked is True
    assert by_id[ids["tier"]].viewer_has_liked is False
    assert sum(1 for s in statements if s.lstrip().upper().startswith("SELECT")) == 1, statements
