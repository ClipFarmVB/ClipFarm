"""CF-238 / CF-253: `update_player` must not let a team_id be cleared to null.

Clearing it is a one-way door. `get_owned_player` rejects `team_id IS NULL` —
there is no team whose owner it could check — and `update_player` calls it
first, so an orphaned player 404s on every subsequent PATCH and the team cannot
be put back through the API. `list_players` filters to owned teams, so the row
also vanishes from the UI. Nothing surfaces it and nothing fixes it.

CF-238 refused it with a runtime 400 in the handler. CF-253 gave PATCH its own
body, `PlayerUpdate`, which refuses an explicit null `team_id` at validation,
a 422 before the handler runs, so the 400 branch and its tests are gone.
`create_player` keeps its 400 for a missing team; the two routes now reject the
same value with different codes on purpose (`test_both_routes_refuse_...`).

Bodies are built with `model_validate` on a dict, not with kwargs: `updates`
comes from `model_dump(exclude_unset=True)`, so whether a field counts as "set"
is the thing under test, and only the dict path matches what FastAPI does to a
real request body.

Follows the house pattern from test_profile_routes.py — stand-in session, direct
calls into the router, no database. Nothing here imports `get_owned_player` by
name; these tests go through the route handlers. The exception is the
`TestClient` section at the end: a direct call is handed an already-parsed
body, so only a request through FastAPI's routing proves the PATCH route is
declared with `PlayerUpdate` at all.
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
from pydantic import ValidationError  # noqa: E402

from app.routers import players  # noqa: E402
from app.schemas.player import PlayerCreate, PlayerOut, PlayerUpdate  # noqa: E402

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
    parsed = PlayerUpdate.model_validate(body)
    return asyncio.run(players.update_player(player_id, parsed, session, OWNER))


# ── the null team_id is refused at the boundary ─────────────────────────────

def test_clearing_team_id_is_a_validation_error():
    """A null team_id never reaches the handler, so it cannot orphan the row."""
    with pytest.raises(ValidationError) as exc:
        PlayerUpdate.model_validate({"team_id": None})
    assert [e["loc"] for e in exc.value.errors()] == [("team_id",)]


@pytest.mark.parametrize("name", [None, ""])
def test_a_null_or_empty_name_is_a_validation_error(name):
    """`name` is NOT NULL; omitting it is fine, blanking it is not."""
    with pytest.raises(ValidationError) as exc:
        PlayerUpdate.model_validate({"name": name})
    assert [e["loc"] for e in exc.value.errors()] == [("name",)]


@pytest.mark.parametrize("name", ["", "x" * 256])
def test_create_refuses_an_empty_or_oversized_name(name):
    """POST's name mirrors String(255): blank and over-long are 422s, not a
    database error at commit."""
    with pytest.raises(ValidationError) as exc:
        PlayerCreate.model_validate({"name": name})
    assert [e["loc"] for e in exc.value.errors()] == [("name",)]


# ── partial updates ─────────────────────────────────────────────────────────

def test_a_jersey_only_patch_changes_only_the_jersey():
    """CF-253's headline: PlayerCreate required `name`, so this body was a 422."""
    team = _Team()
    player = _Player(team_id=team.id, name="Rosa", jersey_number=7)
    session = _FakeSession(player, [team])

    _patch(session, player.id, {"jersey_number": 9})

    assert player.jersey_number == 9
    assert player.name == "Rosa"
    assert player.team_id == team.id
    assert player.photo_url is None
    assert session.committed


def test_omitting_team_id_still_patches():
    """`exclude_unset` is what keeps an omitted team_id from being read as its
    default. If the router ever reads a *default* rather than a *set* value,
    this turns into `team_id is required on every patch`."""
    team = _Team()
    player = _Player(team_id=team.id, name="Rosa")
    session = _FakeSession(player, [team])

    _patch(session, player.id, {"name": "Renamed"})

    assert player.name == "Renamed"
    assert player.team_id == team.id
    assert session.committed


def test_clearing_a_different_nullable_field_is_still_allowed():
    """The schema rejects a null `team_id`, not a null.

    `jersey_number` is nullable on both the schema and the column, and clearing
    it is legitimate — a player whose number is not known yet. Without this,
    nothing in the suite distinguishes the validator from an over-broad one
    that refuses every null, which would break a valid request.
    """
    team = _Team()
    player = _Player(team_id=team.id, jersey_number=7)
    session = _FakeSession(player, [team])

    _patch(session, player.id, {"jersey_number": None})

    assert player.jersey_number is None
    assert player.team_id == team.id
    assert session.committed


def test_a_valid_team_id_alongside_another_null_still_patches():
    """The same specificity check, but with `team_id` actually present.

    The test above never enters the `"team_id" in updates` branch, so it says
    nothing about an over-broad condition written *inside* it. This request —
    a valid reassignment that also clears the jersey number — is an ordinary
    one a client would make.
    """
    old_team, new_team = _Team(), _Team()
    player = _Player(team_id=old_team.id, jersey_number=7)
    session = _FakeSession(player, [old_team, new_team])

    _patch(session, player.id, {"team_id": str(new_team.id), "jersey_number": None})

    assert player.team_id == new_team.id
    assert player.jersey_number is None
    assert session.committed


def test_reassigning_to_another_owned_team_still_works():
    old, new = _Team(), _Team()
    player = _Player(team_id=old.id)
    session = _FakeSession(player, [old, new])

    _patch(session, player.id, {"team_id": str(new.id)})

    assert player.team_id == new.id
    assert session.committed


def test_reassigning_to_someone_elses_team_is_still_a_404():
    """The pre-existing tenant guard, pinned against a refactor that drops it
    along with the retired null branch beside it."""
    own, foreign = _Team(), _Team(owner_id=STRANGER)
    player = _Player(team_id=own.id)
    session = _FakeSession(player, [own, foreign])

    with pytest.raises(HTTPException) as exc:
        _patch(session, player.id, {"team_id": str(foreign.id)})

    assert exc.value.status_code == 404
    assert player.team_id == own.id
    assert not session.committed


def test_an_already_orphaned_player_is_unreachable():
    """Why this is a one-way door and not a cosmetic inconsistency.

    An orphan 404s in `get_owned_player` before any body field is looked at,
    which is also why refusing the null cannot repair existing rows — only stop
    new ones.
    """
    player = _Player(team_id=None)
    session = _FakeSession(player, [])

    with pytest.raises(HTTPException) as exc:
        _patch(session, player.id, {"team_id": str(uuid.uuid4())})

    assert exc.value.status_code == 404


# ── photo_url ───────────────────────────────────────────────────────────────

def test_patch_sets_photo_url_and_player_out_returns_it():
    team = _Team()
    player = _Player(team_id=team.id)
    session = _FakeSession(player, [team])

    _patch(session, player.id, {"photo_url": "https://cdn.example/rosa.jpg"})

    assert PlayerOut.model_validate(player).photo_url == "https://cdn.example/rosa.jpg"
    assert player.name == "Rosa"
    assert session.committed


def test_patch_with_a_null_photo_url_clears_it():
    """Unlike name/team_id, a null here means clear (see PlayerUpdate)."""
    team = _Team()
    player = _Player(team_id=team.id)
    player.photo_url = "https://cdn.example/rosa.jpg"
    session = _FakeSession(player, [team])

    _patch(session, player.id, {"photo_url": None})

    assert player.photo_url is None
    assert session.committed


@pytest.mark.parametrize("schema", [PlayerCreate, PlayerUpdate])
def test_an_empty_photo_url_is_a_validation_error(schema):
    """"" would be stored as-is and render as <img src="">; null is the way to
    leave a photo unset or clear it, so "" is refused on both routes."""
    with pytest.raises(ValidationError) as exc:
        schema.model_validate({"name": "Rosa", "photo_url": ""})
    assert [e["loc"] for e in exc.value.errors()] == [("photo_url",)]


def test_create_player_sets_photo_url():
    team = _Team()
    session = _FakeSession(None, [team])
    body = PlayerCreate.model_validate(
        {"name": "Rosa", "team_id": str(team.id), "photo_url": "https://cdn.example/rosa.jpg"}
    )

    asyncio.run(players.create_player(body, session, OWNER))

    [created] = session.added
    assert created.photo_url == "https://cdn.example/rosa.jpg"
    assert created.team_id == team.id


# ── the two routes refuse the same value, with different codes ──────────────

def test_both_routes_refuse_a_null_team_id():
    """create_player refused it and update_player once accepted it (CF-238).
    They still agree that it is invalid, but now differ on purpose in *how*:
    POST answers 400 from the handler, because `PlayerCreate.team_id` stays
    nullable in the type; PATCH answers 422 from validation, because
    `PlayerUpdate` refuses it before the handler runs (CF-253). Asserting both
    halves together is what fails if a later change relaxes either schema; that
    the PATCH route is still *declared* with `PlayerUpdate` is pinned over HTTP
    below."""
    session = _FakeSession(None, [])
    body = PlayerCreate.model_validate({"name": "Rosa", "team_id": None})
    with pytest.raises(HTTPException) as created:
        asyncio.run(players.create_player(body, session, OWNER))
    assert created.value.status_code == 400
    assert not session.added

    with pytest.raises(ValidationError) as updated:
        PlayerUpdate.model_validate({"name": "Rosa", "team_id": None})
    assert [e["loc"] for e in updated.value.errors()] == [("team_id",)]


# ── the PATCH route is wired to PlayerUpdate ────────────────────────────────

def _client(session):
    """The players router behind a real ASGI stack, with the database and the
    caller overridden. Everything above parses bodies itself, so it would pass
    even if `update_player` were annotated with `PlayerCreate` again; these go
    through FastAPI's own body parsing instead."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.auth import get_current_user_id
    from app.database import get_db

    app = FastAPI()
    app.include_router(players.router)

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user_id] = lambda: OWNER
    return TestClient(app)


def test_patch_route_refuses_a_null_team_id_over_http():
    """CF-238's orphan path, closed at the route rather than in the schema
    alone. With the body typed `PlayerCreate`, this request validates and the
    handler (which no longer has a runtime guard) writes the null."""
    team = _Team()
    player = _Player(team_id=team.id, name="Rosa")
    session = _FakeSession(player, [team])

    with _client(session) as client:
        resp = client.patch(f"/players/{player.id}", json={"name": "x", "team_id": None})

    assert resp.status_code == 422
    assert [e["loc"] for e in resp.json()["detail"]] == [["body", "team_id"]]
    assert player.team_id == team.id
    assert player.name == "Rosa"
    assert not session.committed


def test_patch_route_accepts_a_jersey_only_body_over_http():
    """The other half: `PlayerCreate` requires `name`, so this was a 422."""
    team = _Team()
    player = _Player(team_id=team.id, name="Rosa", jersey_number=7)
    session = _FakeSession(player, [team])

    with _client(session) as client:
        resp = client.patch(f"/players/{player.id}", json={"jersey_number": 9})

    assert resp.status_code == 200
    assert resp.json()["jersey_number"] == 9
    assert player.name == "Rosa"
    assert player.team_id == team.id
    assert session.committed
