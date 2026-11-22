"""CF-110: the follow gates as the routers wire them, without a database.

`test_follows.py` pins the predicates: `is_accepted_follower` and
`accepted_follow_exists` both filter on `status = 'accepted'`, and a mutation
to either fails a test. What nothing pinned was the *wiring*: each single-object
read path has to resolve the edge and hand it to `access.can_view_*`, and
replacing that argument with `viewer_id is not None` — every signed-in stranger
treated as an accepted follower — left the whole suite green on all five call
sites (review of #191, R1-M1).

So these drive the router coroutines themselves. The session is a stub that
answers `db.get` from a fixture and evaluates the one `follows` SELECT the real
`is_accepted_follower` issues against an in-memory edge table, reading the
bound parameters rather than trusting the call — so a lookup that dropped its
status filter would match a pending edge here as it would in Postgres. Any
other statement means the gate was passed; it raises `_PastTheGate` so the test
can tell "served" from "404" without building the rest of the response.

Each gate is checked for the three viewers the card's acceptance criterion
names: a stranger, a requester whose edge is pending, and an accepted follower.
The fourth path, `list_clips`'s clip filter, is the SQL half and needs rows;
`test_follows_pg.py` runs it.

Also here, for the same reason: unfollow's idempotency, the generated-handle
caller being refused, and the owner's remove-follower route (R1-M2..M4), each
asserted on the handler's behaviour rather than its source text.
"""
import asyncio
import pathlib
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

from app.models.clip import Clip  # noqa: E402
from app.models.collection import Collection  # noqa: E402
from app.models.follow import Follow, FollowStatus  # noqa: E402
from app.models.game import Game  # noqa: E402
from app.models.post import Post  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.visibility import Visibility  # noqa: E402
from app.routers import clips as clips_router  # noqa: E402
from app.routers import collections as collections_router  # noqa: E402
from app.routers import follows as follows_router  # noqa: E402
from app.routers import games as games_router  # noqa: E402
from app.routers import posts as posts_router  # noqa: E402
from app.schemas.collection import CollectionAddClip  # noqa: E402

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()
REQUESTER = uuid.uuid4()   # pending edge to OWNER
FOLLOWER = uuid.uuid4()    # accepted edge to OWNER

EDGES = (
    (REQUESTER, OWNER, FollowStatus.pending),
    (FOLLOWER, OWNER, FollowStatus.accepted),
)


class _PastTheGate(Exception):
    """The handler issued a statement that comes after its visibility gate."""


class _Obj:
    def __init__(self, **fields):
        self.id = fields.pop("id", uuid.uuid4())
        for name, value in fields.items():
            setattr(self, name, value)


def _game(visibility):
    return _Obj(owner_id=OWNER, visibility=visibility, title="Final")


def _clip(game, visibility=None):
    return _Obj(game_id=game.id, visibility=visibility)


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _Session:
    """`get` from fixtures; `execute` evaluates a `follows` lookup, else stops."""

    def __init__(self, *rows, edges=EDGES):
        self.rows = {(model, obj.id): obj for model, obj in rows}
        self.edges = edges
        self.follow_lookups = 0

    async def get(self, model, pk):
        return self.rows.get((model, pk))

    async def execute(self, stmt):
        froms = {getattr(f, "name", None) for f in stmt.get_final_froms()}
        if froms != {Follow.__tablename__}:
            raise _PastTheGate
        self.follow_lookups += 1
        params = stmt.compile().params

        def _values(prefix):
            return {v for k, v in params.items() if k.startswith(prefix)}

        followers, followees = _values("follower_id"), _values("followee_id")
        statuses = _values("status")
        for follower, followee, edge_status in self.edges:
            if (
                follower in followers
                and followee in followees
                and (not statuses or edge_status in statuses)
            ):
                return _Result((uuid.uuid4(),))
        return _Result(None)


def _served(coro) -> bool:
    """True if the handler got past its gate, False on its 404."""
    try:
        asyncio.run(coro)
    except _PastTheGate:
        return True
    except HTTPException as exc:
        assert exc.status_code == 404, exc.detail
        return False
    return True


VIEWERS = [
    pytest.param(STRANGER, False, id="stranger"),
    pytest.param(REQUESTER, False, id="pending"),
    pytest.param(FOLLOWER, True, id="accepted"),
]


# ── the five router gates ────────────────────────────────────────────────────


@pytest.mark.parametrize("viewer, expected", VIEWERS)
def test_get_game_follows_the_edge(viewer, expected):
    game = _game(Visibility.followers)
    db = _Session((Game, game))
    assert _served(games_router.get_game(game.id, db, viewer)) is expected
    assert db.follow_lookups == 1


@pytest.mark.parametrize("viewer, expected", VIEWERS)
def test_list_clips_game_gate_follows_the_edge(viewer, expected):
    game = _game(Visibility.followers)
    db = _Session((Game, game))
    served = _served(
        clips_router.list_clips(
            game.id, db, viewer, action_type=None, player_id=None,
            min_confidence=0.0, min_score=0.0, sort="time", page=1, page_size=50,
        )
    )
    assert served is expected


@pytest.mark.parametrize("viewer, expected", VIEWERS)
def test_the_clip_read_gate_follows_the_edge(viewer, expected):
    """`_get_viewable_clip` is the gate behind `/clips/{id}/share` and
    `/clips/{id}/download`; driven through `share_clip` so the route is covered
    and not only the helper."""
    game = _game(Visibility.followers)
    clip = _clip(game)
    clip.clip_url = "https://example.invalid/clip.mp4"
    db = _Session((Game, game), (Clip, clip))
    assert _served(clips_router._get_viewable_clip(clip.id, viewer, db)) is expected


def test_share_and_download_read_through_the_gate():
    """The two read routes take their clip from `_get_viewable_clip` — the gate
    the test above drives — rather than a bare `db.get`."""
    import inspect

    for route in (clips_router.share_clip, clips_router.download_clip):
        assert "_get_viewable_clip(" in inspect.getsource(route), route.__name__


@pytest.mark.parametrize("viewer, expected", VIEWERS)
def test_add_clip_to_collection_follows_the_edge(viewer, expected):
    game = _game(Visibility.followers)
    clip = _clip(game)
    collection = _Obj(owner_id=viewer)
    db = _Session((Game, game), (Clip, clip), (Collection, collection))
    served = _served(
        collections_router.add_clip_to_collection(
            collection.id, CollectionAddClip(clip_id=clip.id), viewer, db
        )
    )
    assert served is expected


def _post_fixture(post_level, clip_level):
    game = _game(Visibility.private)
    clip = _clip(game, clip_level)
    author = _Obj(id=OWNER)
    post = _Obj(author_id=OWNER, clip_id=clip.id, visibility=post_level)
    return post, _Session((Post, post), (Clip, clip), (Game, game), (User, author))


@pytest.mark.parametrize("viewer, expected", VIEWERS)
def test_get_post_follows_the_edge(viewer, expected):
    post, db = _post_fixture(Visibility.followers, Visibility.followers)
    assert _served(posts_router._load_for_read(post.id, viewer, db)) is expected


@pytest.mark.parametrize("viewer, expected", VIEWERS)
def test_a_followers_post_over_a_public_clip_gates_on_the_author_edge(viewer, expected):
    """Isolates `viewer_follows_author`: the clip is open to anyone, so only the
    post's own tier can refuse."""
    post, db = _post_fixture(Visibility.followers, Visibility.public)
    assert _served(posts_router._load_for_read(post.id, viewer, db)) is expected


@pytest.mark.parametrize("viewer, expected", VIEWERS)
def test_a_public_post_over_a_followers_clip_gates_on_the_owner_edge(viewer, expected):
    """Isolates `viewer_follows_owner`: the post is open, so only the footage's
    tier can refuse — a clip narrowed after it was posted takes the post with
    it."""
    post, db = _post_fixture(Visibility.public, Visibility.followers)
    assert _served(posts_router._load_for_read(post.id, viewer, db)) is expected


# ── R1-M2: self-follow at the database, unfollow idempotency ─────────────────


def test_the_model_carries_the_self_follow_check():
    """The pg fixtures build from the models, so this is the CHECK they test."""
    checks = {
        c.name: str(c.sqltext)
        for c in Follow.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    assert checks.get("ck_follow_not_self") == "follower_id <> followee_id"


def test_the_migration_creates_the_self_follow_check():
    """And this is the one production runs — `create_all` never reaches it, so
    without this, deleting it from the migration left every test green."""
    versions = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
    creating = [
        p.name
        for p in versions.glob("*.py")
        if 'sa.CheckConstraint("follower_id <> followee_id", name="ck_follow_not_self")'
        in p.read_text(encoding="utf-8")
    ]
    assert len(creating) == 1, creating


class _NoEdgeSession:
    def __init__(self):
        self.commits = 0
        self.writes = 0

    async def execute(self, stmt):
        if stmt.is_select:
            return _ScalarResult(None)
        self.writes += 1
        raise AssertionError("no write is due when there is no edge")

    async def commit(self):
        self.commits += 1


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def _target(monkeypatch, *, generated=False, user_id=None):
    target = _Obj(id=user_id or uuid.uuid4(), is_private=False,
                  username_is_generated=generated)

    async def _by_handle(handle, db):
        return target

    monkeypatch.setattr(follows_router.profiles, "by_handle", _by_handle)
    return target


def test_unfollowing_someone_you_do_not_follow_succeeds(monkeypatch):
    """The double-tap: the second DELETE finds no edge and must still answer
    the state, not 404."""
    _target(monkeypatch)
    db = _NoEdgeSession()
    state = asyncio.run(follows_router.unfollow_user("someone", uuid.uuid4(), db))
    assert state.status is None and state.following is False
    assert db.writes == 0 and db.commits == 0


# ── R1-M4: a generated-handle caller cannot follow ───────────────────────────


class _CallerSession:
    def __init__(self, caller):
        self.caller = caller

    async def get(self, model, pk):
        assert model is User
        return self.caller if pk == self.caller.id else None

    async def execute(self, stmt):
        raise _PastTheGate

    def add(self, obj):
        raise _PastTheGate


def test_a_caller_with_a_generated_handle_cannot_follow(monkeypatch):
    """Every list that would show the edge drops a generated handle, so the
    target could never see the request to answer it."""
    _target(monkeypatch)
    caller = _Obj(username_is_generated=True)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(follows_router.follow_user("someone", caller.id, _CallerSession(caller)))
    assert exc.value.status_code == 409


def test_a_caller_who_chose_a_handle_reaches_the_edge_lookup(monkeypatch):
    _target(monkeypatch)
    caller = _Obj(username_is_generated=False)
    with pytest.raises(_PastTheGate):
        asyncio.run(follows_router.follow_user("someone", caller.id, _CallerSession(caller)))


# ── R1-M3: the owner removes a follower ──────────────────────────────────────


class _RemoveSession:
    def __init__(self, returned):
        self.returned = returned
        self.statements = []
        self.commits = 0

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _ScalarResult(self.returned)

    async def commit(self):
        self.commits += 1


@pytest.mark.parametrize(
    "returned, moves_counters",
    [(FollowStatus.accepted, True), (FollowStatus.pending, False), (None, False)],
)
def test_remove_follower_deletes_the_edge_to_me(monkeypatch, returned, moves_counters):
    follower = _target(monkeypatch)
    me = uuid.uuid4()
    adjusted = []

    async def _adjust(db, follower_id, followee_id, delta):
        adjusted.append((follower_id, followee_id, delta))

    monkeypatch.setattr(follows_router, "_adjust_counts", _adjust)
    db = _RemoveSession(returned)

    asyncio.run(follows_router.remove_follower("someone", me, db))

    delete_stmt = db.statements[0]
    assert delete_stmt.is_delete
    params = set(delete_stmt.compile().params.values())
    assert {follower.id, me} <= params, "must be scoped to that follower and me"
    assert adjusted == ([(follower.id, me, -1)] if moves_counters else [])
    assert db.commits == (0 if returned is None else 1)


def test_remove_follower_is_routed():
    paths = {
        (route.path, tuple(sorted(route.methods)))
        for route in follows_router.router.routes
    }
    assert ("/users/me/followers/{handle}", ("DELETE",)) in paths
