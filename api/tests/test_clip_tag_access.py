"""CF-234: tagging a clip must gate the *player*, not just the clip.

`tag_clip` verified that the clip's parent game was owned, then took
`body.player_id` on trust — `db.get(Player, ...)` answers "does this row
exist", not "may this caller use it". So an authenticated user could tag their
own clip with another tenant's player and read that player's name back off the
response, making the endpoint an oracle for any id held, and leaving a
cross-tenant foreign key on a row that follows the clip into every later
listing.

Every other player-touching route already walks Player -> Team -> Team.owner_id
via `players._get_owned_player`. This was the only route where a caller-supplied
`player_id` reached a write.

The negative case alone would pass against a route that rejects everything, so
the positive case is pinned beside it deliberately.

Follows the house pattern from test_profile_routes.py / test_corrections.py — a
stand-in session and direct calls into the router, no TestClient and no database.
"""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402

from app.models.clip import ActionType  # noqa: E402
from app.routers import clips  # noqa: E402
from app.schemas.clip import ClipTagRequest  # noqa: E402

OWNER = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()


class _Team:
    def __init__(self, owner_id):
        self.id = uuid.uuid4()
        self.owner_id = owner_id
        self.name = "Team"


class _Player:
    def __init__(self, team, name="Someone Else"):
        self.id = uuid.uuid4()
        self.name = name
        self.team_id = team.id if team else None


class _Game:
    def __init__(self, owner_id, raw_video_url="raw/x.mp4"):
        self.id = uuid.uuid4()
        self.owner_id = owner_id
        self.raw_video_url = raw_video_url


class _Clip:
    """The columns ClipOut reads plus the one the route writes."""

    def __init__(self, game):
        self.id = uuid.uuid4()
        self.game_id = game.id
        self.player_id = None
        self.action_type = ActionType.spike
        self.confidence = 0.8
        self.highlight_score = 0.5
        self.start_time = 1.0
        self.end_time = 6.0
        self.clip_url = "https://example.invalid/clip.mp4"
        self.thumbnail_url = None
        self.labels = ["spike"]
        self.created_at = datetime.now(timezone.utc)


class _FakeSession:
    """Dispatches get() by model name off whatever rows the test set up."""

    def __init__(self, *rows):
        self._rows = {(type(r).__name__.lstrip("_"), r.id): r for r in rows}
        self.committed = False

    async def get(self, model, pk):
        return self._rows.get((model.__name__, pk))

    async def commit(self):
        self.committed = True

    async def refresh(self, _obj):
        return None


def _tag(session, clip, player_id, user_id=OWNER):
    return asyncio.run(
        clips.tag_clip(clip.id, ClipTagRequest(player_id=player_id), session, user_id)
    )


# ── the boundary ─────────────────────────────────────────────────────────────

def test_clip_tag_rejects_foreign_player():
    """The IDOR. Own clip, another tenant's player — 404, and nothing written.

    The team must be the *same object* the player points at and the one the
    session hands back: build two and `db.get(Team, player.team_id)` misses,
    the 404 comes from the dangling-FK branch instead, and the test passes
    against a build with the owner comparison removed. That case is worth
    covering — it has its own test below — but not in place of this one."""
    game = _Game(OWNER)
    clip = _Clip(game)
    foreign_team = _Team(OTHER_TENANT)
    foreign = _Player(foreign_team)
    session = _FakeSession(game, clip, foreign, foreign_team)

    with pytest.raises(HTTPException) as exc:
        _tag(session, clip, foreign.id)

    assert exc.value.status_code == 404
    assert clip.player_id is None, "a rejected tag must not reach the row"
    assert session.committed is False


def test_player_whose_team_is_missing_is_rejected():
    """A player row pointing at a team that isn't there. No owner to read, so
    the walk must fail closed rather than fall through to the write."""
    game = _Game(OWNER)
    clip = _Clip(game)
    dangling = _Player(_Team(OTHER_TENANT))  # team deliberately not in session
    session = _FakeSession(game, clip, dangling)

    with pytest.raises(HTTPException) as exc:
        _tag(session, clip, dangling.id)

    assert exc.value.status_code == 404
    assert clip.player_id is None
    assert session.committed is False


def test_rejection_is_404_so_the_endpoint_is_not_an_existence_oracle():
    """403 would confirm the id exists. Same convention as _get_owned_clip."""
    game = _Game(OWNER)
    clip = _Clip(game)
    foreign_team = _Team(OTHER_TENANT)
    foreign = _Player(foreign_team)
    session = _FakeSession(game, clip, foreign, foreign_team)

    with pytest.raises(HTTPException) as exc:
        _tag(session, clip, foreign.id)

    assert exc.value.status_code == 404
    assert "not found" in str(exc.value.detail).lower()


def test_foreign_player_is_indistinguishable_from_one_that_does_not_exist():
    """The disclosure half. Asserting only that the name is absent from the
    404 detail proves nothing — that detail is a constant, so the assertion
    cannot fail once anything is raised at all. The property with teeth is
    that the two rejections are *identical*: a caller holding an id cannot
    tell "belongs to someone else" from "no such row", and no part of the
    foreign player leaks into the difference."""
    game = _Game(OWNER)
    clip = _Clip(game)
    foreign_team = _Team(OTHER_TENANT)
    foreign = _Player(foreign_team, name="Confidential Name")
    session = _FakeSession(game, clip, foreign, foreign_team)

    with pytest.raises(HTTPException) as foreign_exc:
        _tag(session, clip, foreign.id)

    with pytest.raises(HTTPException) as absent_exc:
        _tag(_FakeSession(game, clip), clip, uuid.uuid4())

    assert foreign_exc.value.status_code == absent_exc.value.status_code
    assert foreign_exc.value.detail == absent_exc.value.detail
    assert "Confidential Name" not in str(foreign_exc.value.detail)
    assert clip.player_id is None


# ── the positive case, so the fix can't pass by rejecting everything ─────────

def test_clip_tag_accepts_own_player():
    own_team = _Team(OWNER)
    game = _Game(OWNER)
    clip = _Clip(game)
    mine = _Player(own_team, name="My Player")
    session = _FakeSession(game, clip, mine, own_team)

    out = _tag(session, clip, mine.id)

    assert clip.player_id == mine.id
    assert out.player_name == "My Player"
    assert out.source_available is True
    assert session.committed is True


def test_orphan_player_is_rejected():
    """team_id NULL has no owner to check, so it cannot be proven safe."""
    game = _Game(OWNER)
    clip = _Clip(game)
    orphan = _Player(None)
    session = _FakeSession(game, clip, orphan)

    with pytest.raises(HTTPException) as exc:
        _tag(session, clip, orphan.id)

    assert exc.value.status_code == 404
    assert clip.player_id is None
    assert session.committed is False


def test_clip_ownership_is_still_checked_first():
    """The pre-existing gate must not regress: someone else's clip is 404 even
    when the player is legitimately the caller's."""
    own_team = _Team(OWNER)
    foreign_game = _Game(OTHER_TENANT)
    foreign_clip = _Clip(foreign_game)
    mine = _Player(own_team)
    session = _FakeSession(foreign_game, foreign_clip, mine, own_team)

    with pytest.raises(HTTPException) as exc:
        _tag(session, foreign_clip, mine.id)

    assert exc.value.status_code == 404
    assert foreign_clip.player_id is None
    assert session.committed is False
