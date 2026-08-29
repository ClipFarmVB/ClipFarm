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

# Sentinel so `team_id=None` is distinguishable from "not specified".
_UNSET = object()

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
    def __init__(self, name="Jordan Vance", team_id=_UNSET):
        self.id = uuid.uuid4()
        self.name = name
        # Owned by nobody the viewer knows: the point is that ownership is not
        # consulted, so a player from an unrelated tenant renders the same.
        # `team_id=None` is the orphan case — access.py asserts those stay
        # visible through these listings, so it must be constructible here.
        self.team_id = uuid.uuid4() if team_id is _UNSET else team_id


class _Clip:
    def __init__(self, game, player, visibility=None):
        self.id = uuid.uuid4()
        self.game_id = game.id
        self.player_id = player.id if player is not None else None
        self.action_type = ActionType.spike
        self.confidence = 0.8
        self.highlight_score = 0.5
        self.start_time = 1.0
        self.end_time = 6.0
        self.clip_url = "https://example.invalid/clip.mp4"
        self.thumbnail_url = None
        self.labels = ["spike"]
        # NULL means "inherit the game's tier". Parametrised rather than
        # hardcoded: a gate on the *clip's* own tier is invisible to a fixture
        # that only ever produces one value, which is how three separate
        # reversals reached green in review.
        self.visibility = visibility
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
        # May be None: `get` serving a missing row is the other half of each
        # 404 branch, and a fake that can only produce the ownership failure
        # leaves "the row is not there" untested.
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


def _tables_touched(stmt):
    """Every table the statement references, by name.

    Not a substring scan of the compiled SQL. That was the first version, and
    it forbade the literal strings "join", "teams" and "owner_id" anywhere in
    the text — so a `joined_at` column, or a legitimate join added later to
    fetch something unrelated, would fail the test with a message accusing
    CF-263 of being reversed. Walking the statement asks the question the
    decision actually cares about: does this query reach beyond `players`?
    """
    from sqlalchemy import Table
    from sqlalchemy.sql import visitors

    return {el.name for el in visitors.iterate(stmt) if isinstance(el, Table)}


def _filtered_columns(stmt):
    """Every column the statement's WHERE clause compares, fully qualified.

    Table names alone are not enough. A predicate that never leaves `players`
    — `Player.team_id.isnot(None)`, say — reaches no second table, so the walk
    above sees nothing wrong, and the fake session ignores the WHERE entirely
    so no result changes either. Both blind spots line up, which is how an
    orphan filter pushed down into SQL stayed green through five rounds.

    The lookup is entitled to select players by id and by nothing else, so the
    honest assertion is on the columns rather than on the tables.
    """
    from sqlalchemy.sql import visitors
    from sqlalchemy.sql.elements import ColumnClause

    where = stmt.whereclause
    if where is None:
        return set()
    return {
        f"{el.table.name}.{el.name}"
        for el in visitors.iterate(where)
        if isinstance(el, ColumnClause) and el.table is not None
    }


def _raw_sql_fragments(stmt):
    """Any literal SQL text spliced into the statement.

    `_filtered_columns` walks typed column objects, so a predicate written as
    `text("players.team_id IS NOT NULL")` contributes no columns and reads as
    an unfiltered query — the fifth-round finding routed around the fix for
    the fifth-round finding. Raw text in this lookup has no legitimate use, so
    the honest assertion is that there is none rather than trying to parse it.
    """
    from sqlalchemy.sql import visitors
    from sqlalchemy.sql.elements import TextClause

    return [str(el) for el in visitors.iterate(stmt) if isinstance(el, TextClause)]


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
    touched = _tables_touched(session.statements[1])

    assert touched == {"players"}, (
        f"the player lookup reaches {sorted(touched)} rather than players "
        f"alone, so it can gate the name on something other than the clip the "
        f"viewer may already read — that reverses CF-263 (#293)."
    )

    filtered = _filtered_columns(session.statements[1])
    assert filtered == {"players.id"}, (
        f"the player lookup filters on {sorted(filtered)} rather than the id "
        f"alone. A predicate that stays inside players reaches no second table, "
        f"so the check above cannot see it — that reverses CF-263 (#293)."
    )

    raw = _raw_sql_fragments(session.statements[1])
    assert raw == [], (
        f"the clips player lookup splices raw SQL: {raw}. Text is opaque to "
        f"the column check above, so a filter written that way reverses "
        f"CF-263 (#293) invisibly."
    )

    assert session.statements[1]._limit_clause is None, (
        "the clips player lookup is limited, so a page with more tagged "
        "players than the limit silently loses names — CF-263 (#293)."
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
        # May be None — see the note on _FakeSession.
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
    # The game is owned by somebody else — that is the whole point. A viewer
    # who owned the game too would still see the name under a route that gated
    # the attach on `game.owner_id == user_id`, so this test would pass while
    # the decision was reversed. The statement pin cannot cover that: a Python
    # gate leaves the SQL untouched.
    game = _Game(Visibility.public, owner_id=STRANGER)
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
    game = _Game(Visibility.public, owner_id=STRANGER)
    player = _Player()
    collection = _Collection(owner_id=OWNER)
    session = _FakeCollectionSession(collection, [_Clip(game, player)], [player], [game])

    _list_collection(session, collection, OWNER)

    assert len(session.statements) == 3, "clips, players, games"
    touched = _tables_touched(session.statements[1])

    assert touched == {"players"}, (
        f"the collection player lookup reaches {sorted(touched)} rather than "
        f"players alone — that reverses CF-263 (#293) for this route."
    )

    filtered = _filtered_columns(session.statements[1])
    assert filtered == {"players.id"}, (
        f"the collection player lookup filters on {sorted(filtered)} rather "
        f"than the id alone — that reverses CF-263 (#293) for this route."
    )

    raw = _raw_sql_fragments(session.statements[1])
    assert raw == [], (
        f"the collections player lookup splices raw SQL: {raw}. Text is opaque to "
        f"the column check above, so a filter written that way reverses "
        f"CF-263 (#293) invisibly."
    )

    assert session.statements[1]._limit_clause is None, (
        "the collections player lookup is limited, so a page with more tagged "
        "players than the limit silently loses names — CF-263 (#293)."
    )


def test_a_collection_that_is_not_yours_404s_before_any_of_this():
    """The twin of the private-game test above, for the second route. The
    ownership gate here is on the *collection*, and it runs before the clip
    query — so a caller who is not the owner never reaches the name at all.
    Without this, `_FakeCollectionSession.get` only ever served the happy path
    and the 404 branch of `_get_owned_collection` was unexercised.
    """
    from fastapi import HTTPException

    game = _Game(Visibility.public, owner_id=STRANGER)
    player = _Player()
    collection = _Collection(owner_id=STRANGER)
    session = _FakeCollectionSession(collection, [_Clip(game, player)], [player], [game])

    with pytest.raises(HTTPException) as exc:
        _list_collection(session, collection, OWNER)

    assert exc.value.status_code == 404
    assert session.executed == 0, "rejected before the clip query, let alone the name"


# ── Non-public tiers, because "entitled" is not "public" ─────────────────────
#
# Every game above is `Visibility.public` and no clip carries its own tier, so
# a gate keyed on the *content's tier* rather than on the viewer is invisible
# to all of them: adding `and widest_allowed(c, game) == public` to either
# attach left the whole suite green. Two rounds varied who is asking; nobody
# varied what the content is.
#
# The decision is that whoever may *read* a clip may read the name on it. Read
# access at a non-public tier is exactly where that has teeth, and it is about
# to matter more — CF-110 makes `followers` genuinely reachable by someone
# other than the owner.


def test_the_name_rides_along_at_a_non_public_tier():
    """A private game, read by its owner. Unambiguously readable today, and
    unambiguously not public — so a gate keyed on the tier blanks the name here
    while every public-tier test above stays green."""
    game = _Game(Visibility.private, owner_id=OWNER)
    player = _Player()
    session = _FakeSession(game, [_Clip(game, player)], [player])

    out = _list(session, game, OWNER)

    assert out[0].player_name == "Jordan Vance", (
        "the name follows read access, not the public tier (CF-263) — a viewer "
        "entitled to the clip is entitled to the name tagged on it"
    )


def test_the_collection_name_rides_along_at_a_non_public_tier():
    """The same, for the collection route, at `followers`.

    Owned by a stranger on purpose: this is the clip CF-110 makes reachable by
    someone who is not the owner, and it is the case a tier-keyed gate would
    silently blank at exactly the moment the follow graph starts serving it.
    The route's SQL decides which clips come back; this asserts only that the
    name rides along with whichever ones do.
    """
    game = _Game(Visibility.followers, owner_id=STRANGER)
    player = _Player()
    collection = _Collection(owner_id=OWNER)
    session = _FakeCollectionSession(collection, [_Clip(game, player)], [player], [game])

    out = _list_collection(session, collection, OWNER)

    assert out[0].player_name == "Jordan Vance", (
        "the collection listing publishes attribution at every tier it serves, "
        "not only the public one (CF-263)"
    )


def test_a_missing_game_404s_before_the_lookup():
    """The other half of the clip route's 404. The tests above exercise the
    *visibility* refusal; this one is the row simply not being there, which
    reaches the same guard by a different path and was never executed."""
    from fastapi import HTTPException

    session = _FakeSession(None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(clips_router.list_clips(uuid.uuid4(), session, ANONYMOUS))

    assert exc.value.status_code == 404
    assert session.executed == 0


def test_a_missing_collection_404s_before_the_lookup():
    """The same for the collection route: `_get_owned_collection` refuses a
    missing row and an unowned one through one condition, and only the unowned
    half had a test."""
    from fastapi import HTTPException

    session = _FakeCollectionSession(None)

    with pytest.raises(HTTPException) as exc:
        _list_collection(session, _Collection(), OWNER)

    assert exc.value.status_code == 404
    assert session.executed == 0


# ── The shape of the data, not just the identity of the viewer ───────────────
#
# Four rounds of review each found a reversal the file could not see, and every
# one was the same defect wearing a different hat: a fixture that produces one
# value cannot detect a gate keyed on that value. The tests above vary who is
# asking and what tier the *game* is. These vary the rest of the shape — the
# clip's own tier, how many clips and players a page has, whether every clip is
# tagged, and whether the player is an orphan.
#
# Written as a group deliberately. Adding one test per reversal someone happens
# to think of is how this file got four rounds deep; the point of these is to
# make the next unnamed gate fail too.


@pytest.mark.parametrize(
    "clip_tier",
    [None, Visibility.public, Visibility.followers, Visibility.private],
    ids=["inherit", "public", "followers", "private"],
)
def test_the_name_rides_along_at_every_clip_tier(clip_tier):
    """A clip carries its own `visibility`, which may narrow below its game's.
    The name follows read access, so it must not be keyed on that value —
    every tier the route serves publishes the attribution with it."""
    game = _Game(Visibility.public, owner_id=OWNER)
    player = _Player()
    clip = _Clip(game, player, visibility=clip_tier)
    session = _FakeSession(game, [clip], [player])

    out = _list(session, game, OWNER)

    assert out[0].player_name == "Jordan Vance", (
        f"a clip at tier {clip_tier!r} lost its attribution — the name follows "
        f"read access, not any particular tier (CF-263)"
    )


def test_every_tagged_clip_on_a_mixed_page_gets_its_name():
    """Two games, three players, one untagged clip, one clip narrowed below its
    game. A single-clip fixture cannot see a reversal that is correct for the
    first clip and wrong for the rest — attaching only to `clips[0]`, or only
    when the page holds one player, or only when every clip is tagged."""
    game_a = _Game(Visibility.public, owner_id=OWNER)
    game_b = _Game(Visibility.followers, owner_id=STRANGER)
    p1, p2, p3 = _Player("Jordan Vance"), _Player("Alex Rivera"), _Player("Sam Okafor")

    tagged = [_Clip(game_a, p1), _Clip(game_a, p2, visibility=Visibility.private),
              _Clip(game_b, p3)]
    untagged = _Clip(game_a, None)
    clips = [tagged[0], untagged, tagged[1], tagged[2]]

    session = _FakeSession(game_a, clips, [p1, p2, p3])
    out = _list(session, game_a, OWNER)

    assert [c.player_name for c in out] == [
        "Jordan Vance", None, "Alex Rivera", "Sam Okafor"
    ], "every tagged clip on the page carries its own name, and only the untagged one does not"


def test_an_orphan_player_still_carries_its_name():
    """`access.py` states in as many words that a player with a NULL `team_id`
    stays visible through these listings — that is what makes the orphan case
    (CF-238, #241) a management problem rather than a disclosure one. A fixture
    that always sets `team_id` cannot fail if the loop starts skipping them."""
    game = _Game(Visibility.public, owner_id=OWNER)
    player = _Player(team_id=None)
    session = _FakeSession(game, [_Clip(game, player)], [player])

    out = _list(session, game, ANONYMOUS)

    assert out[0].player_name == "Jordan Vance", (
        "an orphaned player lost its name — access.py asserts the opposite, "
        "and CF-238 depends on these rows staying visible"
    )


def test_every_tagged_collection_clip_gets_its_name():
    """The mixed-page case for the second route."""
    game_a = _Game(Visibility.public, owner_id=STRANGER)
    game_b = _Game(Visibility.followers, owner_id=STRANGER)
    # p2 is an orphan. The clips route had this covered and the collection
    # twin did not — the same asymmetry, in the same commit that fixed the
    # clip-tier one. Covering an axis on one route says nothing about the other.
    p1, p2 = _Player("Jordan Vance"), _Player("Alex Rivera", team_id=None)

    # p2's clip is narrowed below its game — the clip's *own* tier is a
    # separate axis from the game's, and covering it on the clips route only
    # left this one open. Found by running the mutation matrix against both
    # routes rather than assuming the twin was symmetric.
    clips = [
        _Clip(game_a, p1),
        _Clip(game_a, None),
        _Clip(game_b, p2, visibility=Visibility.private),
    ]
    collection = _Collection(owner_id=OWNER)
    session = _FakeCollectionSession(
        collection, clips, [p1, p2], [game_a, game_b]
    )

    out = _list_collection(session, collection, OWNER)

    assert [c.player_name for c in out] == ["Jordan Vance", None, "Alex Rivera"]


# ── The name has to survive serialisation, and the route has to emit it ──────
#
# Every assertion above reads `player_name` off the returned object. Nothing
# leaves the process, so a reversal at the *schema* boundary is invisible to
# all of them: `Field(default=None, exclude=True)` on ClipOut.player_name
# strips the name from every response on both routes with the whole file green,
# and `response_model_exclude={"player_name"}` on a route decorator does the
# same per-route. Neither touches a router body, which is where every other
# test in this file is looking.


def test_the_name_survives_serialisation():
    """`exclude=True` on the field would satisfy every attribute assertion in
    this file and still strip the name from the wire. Dump it."""
    from fastapi.encoders import jsonable_encoder

    game = _Game(Visibility.public, owner_id=OWNER)
    player = _Player()
    session = _FakeSession(game, [_Clip(game, player)], [player])

    out = _list(session, game, ANONYMOUS)

    assert out[0].model_dump().get("player_name") == "Jordan Vance", (
        "the name is on the object but not in its dump — a field-level "
        "`exclude` reverses CF-263 without touching either router"
    )
    assert jsonable_encoder(out[0]).get("player_name") == "Jordan Vance"


def test_no_route_excludes_the_name_from_its_response():
    """The other half: `response_model_exclude` is declared on the decorator
    and applied by FastAPI after the handler returns, so calling the handler
    directly cannot see it. Read the route table instead."""
    from app.routers import collections as _collections

    for router in (clips_router.router, _collections.router):
        for route in router.routes:
            excluded = getattr(route, "response_model_exclude", None) or set()
            assert "player_name" not in excluded, (
                f"{route.path} excludes player_name from its response model, "
                f"which reverses CF-263 (#293) for that route alone"
            )
