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

    async def get(self, _model, _pk):
        return self._game

    async def execute(self, _stmt):
        self.executed += 1
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
