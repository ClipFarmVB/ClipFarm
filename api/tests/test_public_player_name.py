"""CF-263: a viewable clip carries its player's name, deliberately.

`list_clips` and `list_collection_clips` attach `player_name` with a bare id
lookup and no ownership filter, so whoever may read a clip may read the name
tagged on it — an anonymous viewer of a public clip included. That is the
product decision: publishing a clip publishes its attribution, which is what
makes a share link (CF-222) or a feed entry (CF-111) worth anything.

It is pinned here because a missing `where` reads exactly like an oversight.
Without a test, the next person to audit this path removes the "gap" and
silently strips attribution from every public clip. The reasoning lives in
services/access.py; this file makes it fail loudly if reversed.

What this does NOT cover, deliberately: the name is reachable *through a clip
the viewer may already see*. It is not enumerable, and no write path accepts a
foreign player_id — CF-234 closed the one that did, and
test_clip_tag_access.py pins that boundary.

House pattern from test_clip_tag_access.py — a stand-in session and direct
calls into the router, no TestClient and no database.
"""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from app.models.clip import ActionType  # noqa: E402
from app.models.visibility import Visibility  # noqa: E402
from app.routers import clips as clips_router  # noqa: E402
from app.routers import collections as collections_router  # noqa: E402

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()
ANONYMOUS = None


class _Game:
    def __init__(self, visibility=Visibility.public, owner_id=OWNER):
        self.id = uuid.uuid4()
        self.owner_id = owner_id
        self.visibility = visibility
        self.raw_video_url = "raw/x.mp4"


class _Player:
    def __init__(self, name="Jordan Vance"):
        self.id = uuid.uuid4()
        self.name = name
        # Owned by nobody the viewer knows: the point is that ownership is not
        # consulted, so a player from an unrelated tenant renders the same.
        self.team_id = uuid.uuid4()


class _Clip:
    def __init__(self, game, player):
        self.id = uuid.uuid4()
        self.game_id = game.id
        self.player_id = player.id
        self.action_type = ActionType.spike
        self.confidence = 0.8
        self.highlight_score = 0.5
        self.start_time = 1.0
        self.end_time = 6.0
        self.clip_url = "https://example.invalid/clip.mp4"
        self.thumbnail_url = None
        self.labels = ["spike"]
        self.visibility = None
        self.created_at = datetime.now(timezone.utc)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    """`list_clips` issues exactly two execute() calls, in a fixed order: the
    clip page, then the player lookup. Queue the results in that order."""

    def __init__(self, game, *result_sets):
        self._game = game
        self._queued = list(result_sets)
        self.executed = 0
        # Kept, not discarded. Every assertion below about *results* is
        # satisfied by whatever this fake hands back, so a route that added an
        # ownership filter to the player lookup would still return the queued
        # player and every test would stay green. The statements are the only
        # evidence of what was actually asked for, which is the thing CF-263
        # pins — see test_the_player_lookup_is_not_filtered_by_ownership.
        self.statements = []

    async def get(self, _model, _pk):
        return self._game

    async def execute(self, stmt):
        self.executed += 1
        self.statements.append(stmt)
        if not self._queued:
            raise AssertionError(
                f"execute() called {self.executed} times, but only "
                f"{self.executed - 1} result set(s) were queued — the route "
                f"issued a query this test did not expect"
            )
        return _Result(self._queued.pop(0))


def _list(session, game, viewer_id):
    return asyncio.run(clips_router.list_clips(game.id, session, viewer_id))


@pytest.mark.parametrize("viewer", [ANONYMOUS, STRANGER, OWNER])
def test_a_viewable_clip_carries_its_player_name(viewer):
    """The decision. Anonymous included — that is the case that matters."""
    game = _Game(Visibility.public)
    player = _Player()
    clip = _Clip(game, player)
    session = _FakeSession(game, [clip], [player])

    out = _list(session, game, viewer)

    assert len(out) == 1
    assert out[0].player_name == "Jordan Vance", (
        "public clips publish their attribution (CF-263) — if this was removed "
        "on purpose, update services/access.py and this file together"
    )


def test_the_name_is_not_gated_on_the_viewer_owning_the_player():
    """Same clip, same player, two viewers: the name must not differ.

    Asserting the two agree rather than checking one in isolation — a build
    that returned None for everyone would satisfy a single-viewer test that
    only pinned 'no exception raised'.
    """
    game = _Game(Visibility.public)
    player = _Player()

    anon = _list(_FakeSession(game, [_Clip(game, player)], [player]), game, ANONYMOUS)
    owner = _list(_FakeSession(game, [_Clip(game, player)], [player]), game, OWNER)

    assert anon[0].player_name == owner[0].player_name == "Jordan Vance"


def test_the_lookup_still_happens_only_when_a_clip_is_tagged():
    """An untagged page must not issue the second query at all — the CF-263
    decision is about what a tagged clip discloses, not about querying more."""
    game = _Game(Visibility.public)
    clip = _Clip(game, _Player())
    clip.player_id = None
    session = _FakeSession(game, [clip])

    out = _list(session, game, ANONYMOUS)

    assert out[0].player_name is None
    assert session.executed == 1, "no tagged clip, so no player lookup"


def test_a_private_game_still_404s_before_any_of_this():
    """The name rides along with a *viewable* clip. Visibility is the gate, and
    it is unchanged — CF-263 does not widen who can reach the listing."""
    from fastapi import HTTPException

    game = _Game(Visibility.private)
    player = _Player()
    session = _FakeSession(game, [_Clip(game, player)], [player])

    with pytest.raises(HTTPException) as exc:
        _list(session, game, ANONYMOUS)

    assert exc.value.status_code == 404
    assert session.executed == 0, "rejected before the clip query, let alone the name"


def test_the_player_lookup_is_not_filtered_by_ownership():
    """**This is the pin.** Everything above asserts on what the route
    *returned*, and the fake session returns whatever was queued — so a route
    that joined `teams` and filtered on `owner_id` would hand back the same
    player and leave every other test in this file green. That was measured,
    not supposed: applying the SQL filter issue #293 proposes passed the whole
    suite before this test existed.

    So this one reads the statement instead of the result. The player lookup
    must select players by id and nothing else: no join, and no predicate
    naming a team or an owner. If that stops being the decision, this fails and
    services/access.py should change in the same commit.
    """
    game = _Game(Visibility.public)
    player = _Player()
    session = _FakeSession(game, [_Clip(game, player)], [player])

    _list(session, game, ANONYMOUS)

    assert len(session.statements) == 2, "clip page, then player lookup"
    sql = str(session.statements[1].compile(compile_kwargs={"literal_binds": False}))

    assert "players" in sql.lower(), f"second query is not the player lookup: {sql}"
    for forbidden in ("join", "owner_id", "teams"):
        assert forbidden not in sql.lower(), (
            f"the player lookup filters on {forbidden!r}, so a viewer no longer "
            f"sees the name tagged on a clip they may read — that reverses "
            f"CF-263 (#293). Statement was: {sql}"
        )


# ── The collection listing, which the decision covers equally ────────────────
#
# `list_collection_clips` attaches `player_name` the same way and the comment
# there says so. Nothing above reaches it — every test in this file drives
# `clips_router.list_clips` — so before these two, deleting the collections
# attach outright left the whole suite green. Half a pin on a decision that
# names two routes is worse than none: it reads as coverage.


class _Collection:
    def __init__(self, owner_id=OWNER):
        self.id = uuid.uuid4()
        self.owner_id = owner_id


class _FakeCollectionSession:
    """`list_collection_clips` issues three execute() calls in a fixed order:
    the clip page, the player lookup, then the game lookup. `get()` serves the
    collection ownership check that runs before any of them."""

    def __init__(self, collection, *result_sets):
        self._collection = collection
        self._queued = list(result_sets)
        self.executed = 0
        self.statements = []

    async def get(self, _model, _pk):
        return self._collection

    async def execute(self, stmt):
        self.executed += 1
        self.statements.append(stmt)
        if not self._queued:
            raise AssertionError(
                f"execute() called {self.executed} times, but only "
                f"{self.executed - 1} result set(s) were queued"
            )
        return _Result(self._queued.pop(0))


def _list_collection(session, collection, user_id):
    return asyncio.run(
        collections_router.list_collection_clips(collection.id, user_id, session)
    )


def test_a_collection_clip_carries_its_player_name():
    """Same decision, second route. The viewer owns the collection but not the
    player — a collection spans owners once a public clip can be saved into it,
    which is exactly the case the ownership filter would break."""
    game = _Game(Visibility.public)
    player = _Player()
    collection = _Collection(owner_id=OWNER)
    session = _FakeCollectionSession(collection, [_Clip(game, player)], [player], [game])

    out = _list_collection(session, collection, OWNER)

    assert len(out) == 1
    assert out[0].player_name == "Jordan Vance", (
        "the collection listing publishes attribution too (CF-263) — if this "
        "was removed on purpose, update services/access.py and this file"
    )


def test_the_collection_player_lookup_is_not_filtered_by_ownership():
    """The pin, for the second route. See the `list_clips` twin above — the
    result assertions cannot see a filter, only the statement can."""
    game = _Game(Visibility.public)
    player = _Player()
    collection = _Collection(owner_id=OWNER)
    session = _FakeCollectionSession(collection, [_Clip(game, player)], [player], [game])

    _list_collection(session, collection, OWNER)

    assert len(session.statements) == 3, "clips, players, games"
    sql = str(session.statements[1].compile(compile_kwargs={"literal_binds": False}))

    assert "players" in sql.lower(), f"second query is not the player lookup: {sql}"
    for forbidden in ("join", "owner_id", "teams"):
        assert forbidden not in sql.lower(), (
            f"the collection player lookup filters on {forbidden!r} — that "
            f"reverses CF-263 (#293) for this route. Statement was: {sql}"
        )
