"""CF-111: `get_feed` executed against a real Postgres.

**Why this exists.** Every other test in `test_feed.py` either compiles a
statement and greps the SQL, or greps the handler's source. The review of #192
showed exactly what that misses: nothing called `get_feed`, so the entire
serialization half of the endpoint — roughly a third of the file — was reached
by no test at all. Three separate mutations were demonstrated against it with a
fully green suite:

* reordering `rows = rows[:limit]` above `has_more = len(rows) > limit`, which
  makes `next_cursor` permanently null so nothing past page one is reachable;
* setting `caption=None` and swapping `start_time`/`end_time`;
* dropping `limit=limit` from the `feed_query(...)` call.

And the two defects the review found by reading — the author serialized through
`PostAuthor.model_validate` rather than `from_author`, and the avatar never
presigned — were both in that same unreached code.

So: a throwaway database, the real tables, real rows, and the actual coroutine.
The fixture pattern is `test_posts_visibility_pg.py`'s and
`test_follows_pg.py`'s, for the reasons those give — Postgres-specific schema
(native enums, UUID columns), a `postgres:16` service already in CI, and
discovery through `tests/_pg.py`, which is localhost-only and never
`settings.database_url`.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")
pytest.importorskip("psycopg2")
pytest.importorskip("asyncpg")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from tests._pg import pg_url  # noqa: E402


@pytest.fixture(scope="module")
def pg_db():
    """A throwaway database carrying the models' schema."""
    import psycopg2
    from sqlalchemy import create_engine

    admin_url = pg_url("the feed query needs a real server")
    name = f"clipfarm_feed_{uuid.uuid4().hex[:12]}"

    conn = psycopg2.connect(admin_url)
    conn.autocommit = True
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
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        conn.close()


@pytest.fixture
def world(pg_db, monkeypatch):
    """A viewer, three authors, and one public post each.

    * `followed` — accepted edge, so their posts belong in the feed.
    * `pending` — requested only; their **public** posts must stay out.
    * `stranger` — no edge at all.
    * `backfill` — followed, but a CF-107 generated handle and an avatar.

    Returns `(async_url, ids)`.
    """
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

    base = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    rows: list[object] = []
    ids: dict[str, uuid.UUID] = {}

    def _user(key, handle, *, generated=False, avatar=None):
        u = User(
            id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@test.local",
            username=handle, display_name=handle.title(),
            username_is_generated=generated, avatar_url=avatar,
        )
        rows.append(u)
        ids[key] = u.id
        return u

    viewer = _user("viewer", "viewer")
    followed = _user("followed", "followed")
    pending = _user("pending", "pending")
    stranger = _user("stranger", "stranger")
    backfill = _user("backfill", "johnsmith", generated=True,
                     avatar="https://pub.example.com/avatars/js.png")

    def _post(author, *, when, tier=Visibility.public, caption="hi"):
        game = Game(id=uuid.uuid4(), owner_id=author.id, title="g", visibility=tier)
        clip = Clip(
            id=uuid.uuid4(), game_id=game.id, action_type=ActionType.spike,
            confidence=0.9, start_time=1.5, end_time=4.5,
            clip_url="https://pub.example.com/clips/c.mp4",
            thumbnail_url="https://pub.example.com/thumbs/c.jpg",
            visibility=None,
        )
        post = Post(
            id=uuid.uuid4(), author_id=author.id, clip_id=clip.id,
            caption=caption, visibility=tier, created_at=when,
        )
        rows.extend([game, clip, post])
        return post

    ids["own"] = _post(viewer, when=base).id
    ids["followed_public"] = _post(followed, when=base + timedelta(minutes=1)).id
    ids["followed_tier"] = _post(
        followed, when=base + timedelta(minutes=2), tier=Visibility.followers
    ).id
    ids["pending_public"] = _post(pending, when=base + timedelta(minutes=3)).id
    ids["stranger_public"] = _post(stranger, when=base + timedelta(minutes=4)).id
    ids["backfill_public"] = _post(backfill, when=base + timedelta(minutes=5)).id

    rows.append(Follow(id=uuid.uuid4(), follower_id=viewer.id,
                       followee_id=followed.id, status=FollowStatus.accepted))
    rows.append(Follow(id=uuid.uuid4(), follower_id=viewer.id,
                       followee_id=pending.id, status=FollowStatus.pending))
    rows.append(Follow(id=uuid.uuid4(), follower_id=viewer.id,
                       followee_id=backfill.id, status=FollowStatus.accepted))

    sync = create_engine(pg_db)
    with Session(sync) as s:
        s.execute(text("TRUNCATE users, games, clips, posts, follows CASCADE"))
        for row in rows:
            s.add(row)
            s.flush()
        s.commit()
    sync.dispose()

    return pg_db.replace("postgresql://", "postgresql+asyncpg://"), ids


def _feed(async_url, viewer_id, **kwargs):
    """Run the real `get_feed` coroutine and return its FeedPage."""
    from app.routers import feed as feed_router

    async def go():
        engine = create_async_engine(async_url)
        try:
            async with AsyncSession(engine) as db:
                await db.execute(text("SELECT 1"))
                return await feed_router.get_feed(db=db, user_id=viewer_id, **kwargs)
        finally:
            await engine.dispose()

    return asyncio.run(go())


# ── what the feed contains ───────────────────────────────────────────────────


def test_the_feed_is_followed_authors_plus_self(world):
    """The card's rule, asserted over rows rather than over compiled SQL."""
    async_url, ids = world
    page = _feed(async_url, ids["viewer"])
    got = {i.id for i in page.items}

    assert ids["own"] in got, "a user who follows nobody still sees their own posts"
    assert ids["followed_public"] in got
    assert ids["followed_tier"] in got, "an accepted edge resolves the followers tier"
    assert ids["stranger_public"] not in got, "not followed"
    assert ids["pending_public"] not in got, (
        "a pending request must not widen the feed — this is the regression the "
        "author-filter test could not see, because 'accepted' appears in the SQL "
        "via CF-110's visibility EXISTS regardless"
    )


def test_newest_first(world):
    """Ordering over real rows. The test this backs up asserted `now - 1h < now`,
    which holds for an ascending sort and for no ORDER BY at all."""
    async_url, ids = world
    items = _feed(async_url, ids["viewer"]).items
    assert [i.created_at for i in items] == sorted(
        (i.created_at for i in items), reverse=True
    )
    assert items[0].id == ids["backfill_public"], "the most recent post leads"


# ── serialization: the half no test reached ──────────────────────────────────


def test_a_generated_handle_is_withheld(world):
    """`PostAuthor.from_author`, not `model_validate`.

    The schema's own docstring says so: the CF-107 backfill derives handles from
    email local parts, so publishing one turns any response carrying it into an
    existence oracle keyed to a real address. The feed called `model_validate`,
    which skips the classmethod that nulls it — and the feed is where backfilled
    handles get shown at scale.
    """
    async_url, ids = world
    card = next(i for i in _feed(async_url, ids["viewer"]).items
                if i.id == ids["backfill_public"])
    assert card.author.username is None, "a handle its owner never chose"
    assert card.author.display_name == "Johnsmith", "the rest of the card still renders"


def test_playback_carries_the_clip_window(world):
    """Covers the serialization loop end to end.

    `start_time`/`end_time` transposed, or `caption` dropped, passed the entire
    suite before this file existed.
    """
    async_url, ids = world
    card = next(i for i in _feed(async_url, ids["viewer"]).items if i.id == ids["own"])
    assert card.caption == "hi"
    assert (card.playback.start_time, card.playback.end_time) == (1.5, 4.5)
    assert card.playback.clip_url == "https://pub.example.com/clips/c.mp4"
    assert card.viewer_has_liked is False


def test_urls_are_presigned_when_r2_is_configured(world, monkeypatch):
    """The avatar is the one that was never signed at all.

    The bucket is not public, so an unsigned URL is one the client cannot load
    while the API reports 200 — 20 cards, 20 broken images. Clips and thumbs
    were signed; the author's avatar was not, because the feed's copy of the
    serializer skipped `profiles.serialize` and never replaced it.
    """
    from app.services import storage

    async_url, ids = world
    monkeypatch.setattr(storage, "r2_configured", lambda: True)
    monkeypatch.setattr(
        storage, "presign_from_stored_url", lambda url, **kw: f"{url}?signed=1"
    )

    card = next(i for i in _feed(async_url, ids["viewer"]).items
                if i.id == ids["backfill_public"])
    assert card.playback.clip_url.endswith("?signed=1")
    assert card.playback.thumbnail_url.endswith("?signed=1")
    assert card.author.avatar_url == (
        "https://pub.example.com/avatars/js.png?signed=1"
    ), "the author's avatar must be signed like every other media URL"


def test_one_unsignable_row_does_not_take_the_page_down(world, monkeypatch):
    """40 signings per page with no guard meant one bad stored URL was a 500 for
    the whole feed. `profiles.serialize` has wrapped the identical call since
    CF-107; the feed's blast radius is 40x a profile's."""
    from app.services import storage

    async_url, ids = world
    monkeypatch.setattr(storage, "r2_configured", lambda: True)

    def explode(url, **kw):
        if "thumbs" in url:
            raise RuntimeError("unsignable")
        return f"{url}?signed=1"

    monkeypatch.setattr(storage, "presign_from_stored_url", explode)

    page = _feed(async_url, ids["viewer"])
    assert len(page.items) == 4, "the page still renders"
    assert all(i.playback.clip_url.endswith("?signed=1") for i in page.items)


# ── paging ───────────────────────────────────────────────────────────────────


def test_the_cursor_walks_the_whole_feed_without_gaps_or_repeats(world):
    """The assertion the source-greps could not make.

    Reordering `rows[:limit]` above the `has_more` that reads it makes
    `next_cursor` permanently null — nothing past page one is ever reachable —
    and every test in `test_feed.py` stayed green through it.
    """
    async_url, ids = world

    seen: list[uuid.UUID] = []
    cursor = None
    for _ in range(10):
        page = _feed(async_url, ids["viewer"], cursor=cursor, limit=1)
        assert len(page.items) == 1
        seen.append(page.items[0].id)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert cursor is None, "paging must terminate"
    assert len(seen) == len(set(seen)) == 4, "every post once, no repeats"
    assert seen == sorted(seen, key=lambda i: seen.index(i))  # order preserved
    assert ids["own"] in seen and ids["followed_tier"] in seen


def test_the_last_page_reports_no_next_cursor(world):
    """An exact-boundary page is still the last page."""
    async_url, ids = world
    page = _feed(async_url, ids["viewer"], limit=4)
    assert len(page.items) == 4
    assert page.next_cursor is None, (
        "deriving this from len(items) < limit would stop a page early here"
    )


def test_an_empty_cursor_is_rejected_rather_than_restarting(world):
    """`?cursor=` used to skip the decoder and silently return page 1, so a
    client emitting a null template value scrolled forever without advancing."""
    from fastapi import HTTPException

    async_url, ids = world
    with pytest.raises(HTTPException) as exc:
        _feed(async_url, ids["viewer"], cursor="")
    assert exc.value.status_code == 400


# ── what the query loads ─────────────────────────────────────────────────────


def test_the_feed_does_not_load_credentials(world):
    """`select(Post, Clip, User)` materialized all 12 user columns per card,
    `hashed_password` and `email` among them, on the hottest read path — where
    any `repr()` in an error path or a Sentry breadcrumb can pick them up."""
    from sqlalchemy import inspect as sa_inspect

    from app.routers import feed as feed_router

    async_url, ids = world

    async def go():
        engine = create_async_engine(async_url)
        try:
            async with AsyncSession(engine) as db:
                rows = (await db.execute(feed_query(feed_router, ids["viewer"]))).all()
                return [sa_inspect(author).unloaded for *_, author in rows]
        finally:
            await engine.dispose()

    for unloaded in asyncio.run(go()):
        assert "hashed_password" in unloaded, "credentials must not be materialized"
        assert "email" in unloaded


def feed_query(feed_router, viewer_id):
    return feed_router.feed_query(viewer_id)
