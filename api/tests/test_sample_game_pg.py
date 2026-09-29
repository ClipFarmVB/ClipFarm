"""CF-220: the example game copied at signup, against a real Postgres.

**Why a real server.** The copy runs inside `_ensure_user_exists`, in a
savepoint, in the same transaction as the `users` INSERT. What has to hold is
transactional: a copy that fails rolls back to the savepoint and the user row
still commits; a concurrent first load blocks on the `users` unique index and
must not copy a second time. A stub session can only assert that the code
calls `begin_nested` — `test_sample_game_guards.py` does that much, and this
file checks what Postgres actually does with it.

**The concurrency test holds A's transaction open** rather than racing two
`gather`ed calls. `test_auth_provisioning_paths.py` explains why a bare race
proves nothing: the interleaving that matters cannot be scheduled from
outside, so most runs never reach it. Here A has inserted the user and copied
the example but not committed, and the test waits until B is observed blocked
on a lock before releasing A — so B's path is the contended one every run.

`asyncio.run` inside plain defs is the house pattern — see
`test_async_tests_would_be_silently_skipped.py`. Discovery is shared via
`tests/_pg.py`: localhost or `LOCK_TEST_DATABASE_URL` only, never
`settings.database_url`. Without a server every test here skips.
"""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg2")
pytest.importorskip("asyncpg")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from tests._pg import pg_url  # noqa: E402

SOURCE_TITLE = "Varsity vs Lincoln"
CLIP_URLS = [
    ("https://r2.test/clips/src/a.mp4", "https://r2.test/clips/src/a.jpg"),
    ("https://r2.test/clips/src/b.mp4", None),
]


@pytest.fixture(scope="module")
def pg_db():
    """A throwaway database carrying the models' schema."""
    import psycopg2

    admin_url = pg_url("the sample-game copy is transactional and needs a real server")
    name = f"clipfarm_sample_{uuid.uuid4().hex[:12]}"

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


@pytest.fixture(scope="module")
def sources(pg_db):
    """A ready source game with tagged clips, and one still processing.

    The ready source is public, and so is its first clip, so a copier that
    carried either visibility across would fail the copy test's `private` and
    NULL assertions. A private source with inheriting clips could not tell.
    """
    from app.models.clip import ActionType, Clip
    from app.models.game import Game, GameStatus
    from app.models.player import Player
    from app.models.user import User
    from app.models.visibility import Visibility

    owner, player = uuid.uuid4(), uuid.uuid4()
    ready, processing = uuid.uuid4(), uuid.uuid4()
    sync = create_engine(pg_db)
    with Session(sync) as s:
        s.add(User(id=owner, email=f"samples-{owner.hex[:8]}@example.com"))
        s.add(Player(id=player, name="Jordan Real-Name"))
        s.flush()
        s.add(Game(
            id=ready, owner_id=owner, title=SOURCE_TITLE, status=GameStatus.ready,
            raw_video_url="https://r2.test/raw/src.mp4",
            condense_requested=True,
            condensed_video_url="https://r2.test/condensed/src.mp4",
            original_duration=1800.0, condensed_duration=900.0, progress=1.0,
            processed_at=datetime.now(timezone.utc),
            visibility=Visibility.public,
        ))
        s.add(Game(
            id=processing, owner_id=owner, title="Still going",
            status=GameStatus.processing, progress=0.4,
        ))
        s.flush()
        for i, (clip_url, thumb) in enumerate(CLIP_URLS):
            s.add(Clip(
                game_id=ready, player_id=player, action_type=ActionType.spike,
                confidence=0.9, highlight_score=0.8, start_time=10.0 * i,
                end_time=10.0 * i + 6, clip_url=clip_url, thumbnail_url=thumb,
                labels=["spike"],
                visibility=Visibility.public if i == 0 else None,
            ))
        s.commit()
    sync.dispose()
    return {"ready": ready, "processing": processing}


def _provision(pg_db, uid, email, *, stale_first_read=False):
    """`stale_first_read` makes the opening `users` lookup miss, as it does
    for a request whose read ran before another request's INSERT committed.
    That request goes on to the INSERT, gets DO NOTHING, and must not copy."""
    from app.auth import _ensure_user_exists

    reads_missed = []

    async def go():
        engine = create_async_engine(
            pg_db.replace("postgresql://", "postgresql+asyncpg://", 1)
        )
        try:
            async with AsyncSession(engine) as db:
                if stale_first_read:
                    real_scalar = db.scalar

                    async def stale_scalar(*args, **kwargs):
                        db.scalar = real_scalar  # type: ignore[method-assign]
                        reads_missed.append(args[0])
                        return None

                    db.scalar = stale_scalar  # type: ignore[method-assign]
                await _ensure_user_exists(uid, email, db)
        finally:
            await engine.dispose()

    asyncio.run(go())
    return reads_missed


def _query(pg_db, sql, **params):
    sync = create_engine(pg_db)
    try:
        with sync.connect() as c:
            return c.execute(text(sql), params).all()
    finally:
        sync.dispose()


def _user_exists(pg_db, uid):
    return bool(_query(pg_db, "SELECT 1 FROM users WHERE id = :u", u=uid))


def _games_of(pg_db, uid):
    return _query(
        pg_db,
        "SELECT id, title, status, is_sample, raw_video_url, condensed_video_url, "
        "visibility, upload_id FROM games WHERE owner_id = :u",
        u=uid,
    )


def _new_user():
    uid = uuid.uuid4()
    return uid, f"new-{uid.hex[:8]}@example.com"


def test_off_by_default_copies_nothing(pg_db, sources, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "sample_game_id", "")
    uid, email = _new_user()

    _provision(pg_db, uid, email)

    assert _user_exists(pg_db, uid)
    assert _games_of(pg_db, uid) == []


def test_a_new_account_gets_exactly_one_marked_private_copy(pg_db, sources, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "sample_game_id", str(sources["ready"]))
    uid, email = _new_user()

    _provision(pg_db, uid, email)

    games = _games_of(pg_db, uid)
    assert len(games) == 1
    game = games[0]
    assert game.is_sample is True
    assert game.title == "Demo: " + SOURCE_TITLE
    assert game.status == "ready"
    assert game.raw_video_url is None, "a copy has no upload of its own to re-cut from"
    assert game.condensed_video_url is None
    assert game.visibility == "private"
    assert game.upload_id is None

    clips = _query(
        pg_db,
        "SELECT clip_url, thumbnail_url, player_id, visibility, action_type, "
        "confidence, highlight_score FROM clips "
        "WHERE game_id = :g ORDER BY start_time",
        g=game.id,
    )
    assert [(c.clip_url, c.thumbnail_url) for c in clips] == CLIP_URLS, (
        "same stored strings — references to the same objects"
    )
    assert all(c.player_id is None for c in clips), "the source's names must not leak"
    assert all(c.visibility is None for c in clips)
    assert all(
        (c.action_type, c.confidence, c.highlight_score) == ("spike", 0.9, 0.8)
        for c in clips
    ), "the example keeps its action types and highlight ranking"

    charged = _query(pg_db, "SELECT 1 FROM upload_events WHERE owner_id = :u", u=uid)
    assert charged == [], "the example costs no quota"


def test_a_second_provisioning_does_not_copy_again(pg_db, sources, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "sample_game_id", str(sources["ready"]))
    uid, email = _new_user()

    _provision(pg_db, uid, email)
    # Past the opening read, so the second call reaches the INSERT, gets DO
    # NOTHING, and is refused by the `created` gate rather than returning early.
    missed = _provision(pg_db, uid, email, stale_first_read=True)

    assert len(missed) == 1, "the second call never took the INSERT path"
    assert len(_games_of(pg_db, uid)) == 1


@pytest.mark.parametrize("which", ["missing", "processing", "malformed"])
def test_an_unusable_source_still_creates_the_user(pg_db, sources, monkeypatch, which):
    from app.config import settings

    value = {
        "missing": str(uuid.uuid4()),
        "processing": str(sources["processing"]),
        "malformed": "not-a-uuid",
    }[which]
    monkeypatch.setattr(settings, "sample_game_id", value)
    uid, email = _new_user()

    _provision(pg_db, uid, email)

    assert _user_exists(pg_db, uid)
    assert _games_of(pg_db, uid) == []


def test_a_copy_that_fails_in_the_database_still_creates_the_user(
    pg_db, sources, monkeypatch
):
    """A real IntegrityError inside the savepoint — a clip whose game does not
    exist. It must roll back to the savepoint and not reach the outer commit,
    where the provisioning handler would roll back the user row with it."""
    from app.config import settings
    from app.models.clip import Clip
    from app.models.game import Game, GameStatus
    from app.services import sample_game

    async def broken(db, user_id):
        db.add(Game(owner_id=user_id, title="half a copy", status=GameStatus.ready,
                    is_sample=True))
        await db.flush()
        db.add(Clip(game_id=uuid.uuid4(), start_time=0.0, end_time=1.0,
                    clip_url="https://r2.test/x.mp4"))
        await db.flush()

    monkeypatch.setattr(settings, "sample_game_id", str(sources["ready"]))
    monkeypatch.setattr(sample_game, "copy_sample_game", broken)
    uid, email = _new_user()

    _provision(pg_db, uid, email)                      # must not raise

    assert _user_exists(pg_db, uid)
    assert _games_of(pg_db, uid) == [], "the half-made copy rolled back"


def test_a_concurrent_first_load_copies_once(pg_db, sources, monkeypatch):
    from app.auth import _ensure_user_exists
    from app.config import settings

    monkeypatch.setattr(settings, "sample_game_id", str(sources["ready"]))
    uid, email = _new_user()
    url = pg_db.replace("postgresql://", "postgresql+asyncpg://", 1)

    async def go():
        engines = [create_async_engine(url) for _ in range(3)]
        try:
            async with AsyncSession(engines[0]) as a, AsyncSession(engines[1]) as b:
                ready_to_commit, release = asyncio.Event(), asyncio.Event()
                real_commit = a.commit

                async def held_commit():
                    ready_to_commit.set()
                    await release.wait()
                    await real_commit()

                a.commit = held_commit  # type: ignore[method-assign]

                task_a = asyncio.create_task(_ensure_user_exists(uid, email, a))
                await asyncio.wait_for(ready_to_commit.wait(), 10)
                # A has inserted the user and copied the example, uncommitted.
                task_b = asyncio.create_task(_ensure_user_exists(uid, email, b))

                # Wait until B is actually stuck behind A's row lock, so B's
                # contended path is the one taken — not merely a late B that
                # reads A's committed row and returns at the first SELECT.
                async with engines[2].connect() as watcher:
                    for _ in range(100):
                        waiting = await watcher.scalar(text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND wait_event_type = 'Lock'"
                        ))
                        if waiting:
                            break
                        await asyncio.sleep(0.1)
                    else:
                        release.set()
                        await asyncio.gather(task_a, task_b, return_exceptions=True)
                        pytest.fail("B never blocked on A's insert")

                assert not task_b.done()
                release.set()
                await asyncio.gather(task_a, task_b)
        finally:
            for engine in engines:
                await engine.dispose()

    asyncio.run(go())

    games = _games_of(pg_db, uid)
    assert len(games) == 1, f"expected exactly one example, found {len(games)}"
    assert games[0].is_sample is True
