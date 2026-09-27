"""CF-295: `_ensure_user_exists` against a real Postgres.

**Why a real server.** The whole defect is `ON CONFLICT` arbiter semantics:
`on_conflict_do_nothing(index_elements=["id"])` suppresses a conflict on
`users_pkey` and *raises* on any other unique index. `users.email` has one
(`users_email_key`, migration 001). None of that is reproducible against a stub
session or sqlite — a mock would assert that the code calls the functions it
calls, which is what let this reach production in the first place.

**What reached production.** On 2026-09-22 a signup 500ed with
`UniqueViolationError: users_email_key`. The row it collided with turned out to
carry *the same id* as the failing insert, 860 microseconds earlier — two
concurrent first-load requests, not the id/email mismatch the card assumed. A
cross-check of all eight accounts on 2026-09-26 found zero id/email mismatches.
Both cases are pinned below anyway: one is what happened, the other is what the
constraint still permits.

`asyncio.run` inside plain defs is the house pattern — this suite installs no
async plugin, so an `async def test_` would be collected, skipped, and reported
green. See `test_async_tests_would_be_silently_skipped.py`.

Discovery is shared with the other pg suites via `tests/_pg.py` — localhost
only, never `settings.database_url`.
"""
import asyncio
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg2")
pytest.importorskip("asyncpg")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import create_engine, event, select, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from tests._pg import pg_url  # noqa: E402


@pytest.fixture(scope="module")
def pg_db():
    """A throwaway database carrying the models' schema."""
    import psycopg2

    admin_url = pg_url("the ON CONFLICT arbiter behaviour needs a real server")
    name = f"clipfarm_auth_{uuid.uuid4().hex[:12]}"

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
def async_url(pg_db):
    return pg_db.replace("postgresql://", "postgresql+asyncpg://", 1)


def _seed(pg_db, user_id, email):
    sync = create_engine(pg_db)
    with sync.begin() as c:
        c.execute(
            text("INSERT INTO users (id, email, is_private, username_is_generated) "
                 "VALUES (:i, :e, true, false)"),
            {"i": str(user_id), "e": email},
        )
    sync.dispose()


def _run(async_url, body, engine_hook=None):
    """Run body(db) on one AsyncSession, disposing the engine either way."""
    async def go():
        engine = create_async_engine(async_url)
        if engine_hook is not None:
            engine_hook(engine)
        try:
            async with AsyncSession(engine) as db:
                return await body(db)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_id_arbiter_does_not_cover_the_email_index(async_url, pg_db):
    """The defect, stated as a property of Postgres rather than of our code.

    If this ever stops raising, the `users_email_key` index is gone and the
    handling in `_ensure_user_exists` is dead weight — that is the only thing
    that should make this test change.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.models.user import User

    email = f"arbiter-{uuid.uuid4().hex[:8]}@example.com"
    _seed(pg_db, uuid.uuid4(), email)

    async def body(db):
        stmt = (
            pg_insert(User)
            .values(id=uuid.uuid4(), email=email)
            .on_conflict_do_nothing(index_elements=["id"])
        )
        with pytest.raises(IntegrityError, match="users_email_key"):
            await db.execute(stmt)

    _run(async_url, body)


def test_row_already_present_is_a_no_op(async_url, pg_db):
    """The benign race: the request we lost to already committed our row."""
    from app.auth import _ensure_user_exists

    uid = uuid.uuid4()
    email = f"benign-{uuid.uuid4().hex[:8]}@example.com"
    _seed(pg_db, uid, email)

    _run(async_url, lambda db: _ensure_user_exists(uid, email, db))


def test_first_login_creates_the_row(async_url):
    from app.auth import _ensure_user_exists
    from app.models.user import User

    uid = uuid.uuid4()
    email = f"new-{uuid.uuid4().hex[:8]}@example.com"

    async def body(db):
        await _ensure_user_exists(uid, email, db)
        return await db.scalar(select(User.id).where(User.id == uid))

    assert _run(async_url, body) == uid


def test_known_user_costs_no_write(async_url, pg_db):
    """Acceptance criterion from the card: the per-request write disappears.

    Counted at the cursor, so a SELECT is allowed and an INSERT/UPDATE is not.
    Reverting the read-first guard puts an INSERT back here and fails this.
    """
    from app.auth import _ensure_user_exists

    uid = uuid.uuid4()
    email = f"noop-{uuid.uuid4().hex[:8]}@example.com"
    _seed(pg_db, uid, email)
    writes = []

    def hook(engine):
        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _record(conn, cursor, statement, parameters, context, executemany):
            head = statement.lstrip().split(" ", 1)[0].upper()
            if head in {"INSERT", "UPDATE", "DELETE"}:
                writes.append(head)

    _run(async_url, lambda db: _ensure_user_exists(uid, email, db), engine_hook=hook)
    assert writes == [], f"hot path issued writes: {writes}"


def test_email_held_by_another_id_is_409_not_a_500(async_url, pg_db):
    """The case the card predicted. It has not occurred in production — the
    cross-check on 2026-09-26 found zero id/email mismatches across all eight
    accounts — but the constraint still allows it, and an unhandled
    IntegrityError here is a 500 on every request that user makes, forever.
    """
    from app.auth import _ensure_user_exists

    email = f"collide-{uuid.uuid4().hex[:8]}@example.com"
    _seed(pg_db, uuid.uuid4(), email)
    newcomer = uuid.uuid4()

    async def body(db):
        with pytest.raises(HTTPException) as exc:
            await _ensure_user_exists(newcomer, email, db)
        return exc.value

    err = _run(async_url, body)
    assert err.status_code == 409
    assert "already registered" in err.detail
