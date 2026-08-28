"""CF-109: the anonymous list path, executed against a real Postgres.

**Why this exists.** Everything else covering `list_user_posts` asserts on a
string: that `posts.visibility` appears in some compiled SQL, or that the
router's source contains `apply_post_visibility`. `test_posts_endpoints.py`
drives the other four handlers, but its `StubSession` has no `execute`, so the
one endpoint whose whole job is a filtered query is the one never invoked.

The hole that leaves is not theoretical. Rewriting `_posts_predicate` to return
`true()` passes the entire api suite, because a tautology still puts
`posts.visibility` in the statement's text and still leaves the router's source
unchanged. A test that survives the bug it names is worse than no test, because
it reads like cover.

So: a throwaway database, the real tables, real rows, and the actual coroutine.

**Four rows, because there are two independent gates.** `can_view_post`'s
docstring claims the post's tier and the clip's are both required, and the
router's comment leans on that hard — the create-time 409 is called a UX
guarantee and the read path the security boundary. A suite seeded only with
public/public and private/private cannot tell the two apart: the private post
sits under a private clip, so the *clip* gate alone accounts for it and the
post gate could be a tautology with nothing noticing. That is not hypothetical
either; it is what the first draft of this file did, and the mutation test
below is what caught it. The matrix is therefore all four combinations, and
each gate is mutated separately.

The `public post over a private clip` row is the one worth staring at. It
cannot be created through `create_post` — that is what the 409 refuses — so it
stands for the cases the router names as reachable anyway: a bad migration, a
direct DB edit, or a clip narrowed between the check and the INSERT. Inserted
directly here, for the same reason.

Postgres, not sqlite: the schema is Postgres-specific (native enum, UUID
columns), and CI already runs a `postgres:16` service for the CF-184 lock
tests, so this costs nothing there. Locally the compose `db` service is enough.
Discovery is shared with those tests in `tests/_pg.py` — localhost only, never
`settings.database_url`.

A throwaway *database* rather than a schema, or a transaction rolled back: the
`visibility` enum is a database-level type, and `Base.metadata.create_all` is a
much shorter path to a correct schema than running alembic from here. Dropped
in the fixture's teardown.
"""
import asyncio
import uuid

import pytest

pytest.importorskip("sqlalchemy")
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

    admin_url = pg_url("the post-visibility query needs a real server")
    name = f"clipfarm_posts_{uuid.uuid4().hex[:12]}"

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
def seeded(pg_db, monkeypatch):
    """One author; the full post-tier × clip-tier matrix.

    Returns `(async_url, author_id, {label: post_id})` with labels
    `public_public`, `private_public`, `public_private`, `private_private`,
    read as <post tier>_<clip tier>.

    Every clip carries `visibility = NULL` — "inherit from the game" — so the
    clip gate has to resolve the tier through the join rather than read it off
    the row, which is the branch a `clips.visibility = 'public'`-only predicate
    would get wrong.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models.clip import ActionType, Clip
    from app.models.game import Game
    from app.models.post import Post
    from app.models.user import User
    from app.models.visibility import Visibility
    from app.services import storage

    # _serialize presigns when R2 is configured; this test is about the filter.
    monkeypatch.setattr(storage, "r2_configured", lambda: False)

    author = User(
        id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@test.local",
        username="alice", display_name="Alice", username_is_generated=False,
    )

    rows: list[object] = [author]
    ids: dict[str, uuid.UUID] = {}
    matrix = (
        ("public", "public"),
        ("private", "public"),
        ("public", "private"),      # only reachable by a bad write; must not serve
        ("private", "private"),
    )
    for post_tier, clip_tier in matrix:
        game = Game(
            id=uuid.uuid4(), owner_id=author.id, title="g",
            visibility=Visibility(clip_tier),
        )
        clip = Clip(
            id=uuid.uuid4(), game_id=game.id, action_type=ActionType.spike,
            confidence=0.9, start_time=1.0, end_time=2.0,
            clip_url="https://x.test/c.mp4", visibility=None,
        )
        post = Post(
            id=uuid.uuid4(), author_id=author.id, clip_id=clip.id,
            visibility=Visibility(post_tier),
        )
        rows += [game, clip, post]
        ids[f"{post_tier}_{clip_tier}"] = post.id

    # Read off the objects before they are committed and detached: a plain
    # Session expires attributes on commit, so touching author.id afterwards
    # raises DetachedInstanceError. These are the uuid4s assigned above.
    author_id = author.id

    sync = create_engine(pg_db)
    with Session(sync) as s:
        # The database is module-scoped and this fixture is not, so start from
        # empty: without it the second test adds a second "alice" and
        # `_findable_author`'s scalar_one_or_none raises MultipleResultsFound —
        # a fixture bug that would read like a router bug. CASCADE because
        # posts and clips hang off these by FK.
        s.execute(text("TRUNCATE users, games, clips, posts CASCADE"))
        for row in rows:
            s.add(row)
            s.flush()               # FK order: user, then game, then clip, then post
        s.commit()
    sync.dispose()

    async_url = pg_db.replace("postgresql://", "postgresql+asyncpg://")
    return async_url, author_id, ids


def _list(async_url, viewer_id):
    """Run the real `list_user_posts` coroutine and return its PostOut list."""
    from app.routers import posts as posts_router

    async def go():
        engine = create_async_engine(async_url)
        try:
            async with AsyncSession(engine) as db:
                await db.execute(text("SELECT 1"))      # fail loudly on a bad URL
                return await posts_router.list_user_posts(
                    db=db, username="alice", viewer_id=viewer_id, limit=50,
                )
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_a_signed_out_visitor_sees_only_the_public_post_over_a_public_clip(seeded):
    """The assertion the substring tests could not make.

    Not "the WHERE clause mentions posts.visibility" but "those three rows do
    not come back".
    """
    url, _author, ids = seeded

    got = {p.id for p in _list(url, None)}

    assert got == {ids["public_public"]}, (
        "a signed-out visitor must see exactly the public post over the public "
        f"clip; got {got}"
    )


def test_a_public_post_over_a_private_clip_is_not_served(seeded):
    """The security boundary, on the path that actually serves footage.

    The router calls the create-time 409 a UX guarantee and this the real gate,
    on the grounds that a stored `posts.visibility` may be wider than its clip
    allows — a race, a bad migration, a direct edit. That claim was asserted
    only against in-memory objects through `can_view_post`; the list path
    reaches the same rows by a different door, in SQL, and had nothing checking
    it. This is that row, inserted the way the router says it arises.
    """
    url, _author, ids = seeded

    got = {p.id for p in _list(url, None)}

    assert ids["public_private"] not in got, (
        "a public post over a private clip was served — the stored visibility "
        "was trusted without joining the clip"
    )


def test_the_author_sees_all_four(seeded):
    """The other half: the filter must not be so tight it hides your own posts.

    Without this, "return nothing, ever" passes both tests above — the failure
    mode a visibility test most easily degrades into.
    """
    url, author_id, ids = seeded

    got = {p.id for p in _list(url, author_id)}

    assert got == set(ids.values())


def test_neither_gate_is_vacuous(seeded, monkeypatch):
    """Each gate must independently account for a row, asserted not assumed.

    Mutating one predicate at a time is the whole point, and it is why the
    matrix has four rows rather than two. Seeded only with public/public and
    private/private, the private post sits under a private clip — so the clip
    gate alone accounts for it, `_posts_predicate` could return `true()`, and
    nothing would notice. That was this file's first draft.

    Each half below reveals exactly the row its gate was the only thing
    holding back.
    """
    from sqlalchemy import true

    from app.services import access

    url, _author, ids = seeded

    with monkeypatch.context() as m:
        m.setattr(access, "_posts_predicate", lambda viewer_id: true())
        got = {p.id for p in _list(url, None)}
    assert ids["private_public"] in got, (
        "neutering the post gate changed nothing, so the tests above are not "
        "reading it — re-point them before trusting this file"
    )
    assert ids["public_private"] not in got, "the clip gate still stands alone"

    with monkeypatch.context() as m:
        m.setattr(access, "_clips_predicate", lambda viewer_id: true())
        got = {p.id for p in _list(url, None)}
    assert ids["public_private"] in got, (
        "neutering the clip gate changed nothing, so the clip half of the "
        "filter is not being read either"
    )
    assert ids["private_public"] not in got, "the post gate still stands alone"
