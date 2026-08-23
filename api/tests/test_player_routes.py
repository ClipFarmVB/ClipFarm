"""CF-238: `update_player` must not let a team_id be cleared to null.

Clearing it is a one-way door. `_get_owned_player` rejects `team_id IS NULL` —
there is no team whose owner it could check — and `update_player` calls it
first, so an orphaned player 404s on every subsequent PATCH and the team cannot
be put back through the API. `list_players` filters to owned teams, so the row
also vanishes from the UI. Nothing surfaces it and nothing fixes it.

`create_player` has always refused a null `team_id` with a 400. The defect was
the two routes disagreeing about the same value, so the parity itself is pinned
here (`test_the_two_routes_agree_...`) rather than only the new branch.

Bodies are built with `model_validate` on a dict, not with kwargs: `updates`
comes from `model_dump(exclude_unset=True)`, so whether a field counts as "set"
is the thing under test, and only the dict path matches what FastAPI does to a
real request body.

Follows the house pattern from test_profile_routes.py — stand-in session, direct
calls into the router, no TestClient and no database. Nothing here imports
`_get_owned_player` by name; PR #239 renames it, and these tests should not care.
"""
import asyncio
import uuid

import pytest

# The house pair, plus two the router's import graph actually reaches:
# `app.database` calls create_async_engine at import time (asyncpg), and
# `app.auth` imports PyJWT. Guarding only sqlalchemy/fastapi turns a bare
# environment into a collection error rather than a skip.
pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")
pytest.importorskip("asyncpg")
pytest.importorskip("jwt")

from fastapi import HTTPException  # noqa: E402

from app.routers import players  # noqa: E402
from app.schemas.player import PlayerCreate  # noqa: E402

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()


class _Team:
    def __init__(self, owner_id=OWNER):
        self.id = uuid.uuid4()
        self.owner_id = owner_id


class _Player:
    def __init__(self, team_id, name="Rosa", jersey_number=7):
        self.id = uuid.uuid4()
        self.name = name
        self.jersey_number = jersey_number
        self.team_id = team_id
        self.photo_url = None


class _FakeSession:
    """Dispatches `get()` on the model.

    A stub returning one object for every `get()` would make the foreign-team
    case pass for the wrong reason — `update_player` looks up a Player and a
    Team through the same call.
    """

    def __init__(self, player=None, teams=()):
        self._player = player
        self._teams = {t.id: t for t in teams}
        self.committed = False
        self.added = []

    async def get(self, model, pk):
        if model is players.Team:
            return self._teams.get(pk)
        return self._player if self._player is not None and self._player.id == pk else None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, _obj):
        return None


def _patch(session, player_id, body):
    return asyncio.run(players.update_player(player_id, PlayerCreate.model_validate(body), session, OWNER))


# ── the new guard ───────────────────────────────────────────────────────────

def test_clearing_team_id_is_rejected():
    """The bug: the old guard's `is not None` let an explicit null straight
    through to the setattr loop."""
    team = _Team()
    player = _Player(team_id=team.id)
    session = _FakeSession(player, [team])

    with pytest.raises(HTTPException) as exc:
        _patch(session, player.id, {"name": "Rosa", "team_id": None})

    assert exc.value.status_code == 400
    assert "cannot be cleared" in exc.value.detail


def test_a_rejected_clear_leaves_the_row_untouched():
    """The raise must precede the setattr loop, not follow it — a 400 that has
    already written the other fields is a worse outcome than the original bug."""
    team = _Team()
    player = _Player(team_id=team.id, name="Rosa", jersey_number=7)
    session = _FakeSession(player, [team])

    with pytest.raises(HTTPException):
        _patch(session, player.id, {"name": "Renamed", "jersey_number": 99, "team_id": None})

    assert player.team_id == team.id
    assert player.name == "Rosa"
    assert player.jersey_number == 7
    assert not session.committed


# ── what the guard must NOT break ───────────────────────────────────────────

def test_omitting_team_id_still_patches():
    """`exclude_unset` is the whole reason this can be a 400 and not a schema
    change. If the guard ever reads a *default* rather than a *set* value, this
    turns into `team_id is required on every patch`."""
    team = _Team()
    player = _Player(team_id=team.id, name="Rosa")
    session = _FakeSession(player, [team])

    _patch(session, player.id, {"name": "Renamed"})

    assert player.name == "Renamed"
    assert player.team_id == team.id
    assert session.committed


def test_reassigning_to_another_owned_team_still_works():
    old, new = _Team(), _Team()
    player = _Player(team_id=old.id)
    session = _FakeSession(player, [old, new])

    _patch(session, player.id, {"name": "Rosa", "team_id": str(new.id)})

    assert player.team_id == new.id
    assert session.committed


def test_reassigning_to_someone_elses_team_is_still_a_404():
    """The pre-existing tenant guard. The nesting moved it a level deeper, so
    it is pinned here against a refactor that drops it."""
    own, foreign = _Team(), _Team(owner_id=STRANGER)
    player = _Player(team_id=own.id)
    session = _FakeSession(player, [own, foreign])

    with pytest.raises(HTTPException) as exc:
        _patch(session, player.id, {"name": "Rosa", "team_id": str(foreign.id)})

    assert exc.value.status_code == 404
    assert player.team_id == own.id
    assert not session.committed


def test_an_already_orphaned_player_is_unreachable():
    """Why this is a one-way door and not a cosmetic inconsistency.

    An orphan 404s before the new guard is even reached, which is also why the
    guard cannot repair existing rows — only stop new ones.
    """
    player = _Player(team_id=None)
    session = _FakeSession(player, [])

    with pytest.raises(HTTPException) as exc:
        _patch(session, player.id, {"name": "Rosa", "team_id": str(uuid.uuid4())})

    assert exc.value.status_code == 404


# ── the parity that was actually broken ─────────────────────────────────────

def test_the_two_routes_agree_that_a_null_team_id_is_invalid():
    """create_player refused it and update_player accepted it. Asserting the
    agreement, rather than each route alone, is what fails if a later change
    relaxes one of them."""
    session = _FakeSession(None, [])
    with pytest.raises(HTTPException) as created:
        asyncio.run(players.create_player(PlayerCreate.model_validate({"name": "Rosa", "team_id": None}), session, OWNER))
    assert created.value.status_code == 400
    assert not session.added

    team = _Team()
    player = _Player(team_id=team.id)
    session = _FakeSession(player, [team])
    with pytest.raises(HTTPException) as updated:
        _patch(session, player.id, {"name": "Rosa", "team_id": None})
    assert updated.value.status_code == created.value.status_code


def test_a_body_without_a_name_never_reaches_the_handler():
    """Recording a real limit rather than asserting a fiction.

    CF-238's acceptance says `PATCH {"team_id": null}` returns 400. It does not
    — `PlayerCreate.name` has no default, so that body is a 422 in validation
    and the handler never runs. The reproducing body needs `name` too, which is
    what every test above sends. Pinned because the day `PlayerCreate` gains a
    default (or update_player takes a PlayerUpdate), the bare body starts
    reaching the handler and the guard above becomes the only thing stopping it.
    """
    with pytest.raises(Exception):
        PlayerCreate.model_validate({"team_id": None})
