"""CF-318: the device-token registry — ownership, idempotence, and scoping.

Three properties are worth pinning here, and all three are invisible in a
green "it inserted a row":

*Registering re-owns.* The token is unique across the table and a conflict
updates `owner_id`, so a phone that changes hands stops notifying its previous
owner. The dangerous alternative — a row per (user, token) — looks identical
from the caller's side and differs only in the SQL, so the SQL is what is
asserted.

*Unregistering is scoped and silent.* `delete` filters on the caller's id as
well as the token, and returns 204 whether or not anything matched. Both halves
matter: without the owner filter anyone could unregister anyone's device, and
without the unconditional 204 the route answers "is this token registered?" for
whoever asks. A test that only checked the happy path would catch neither.

*The owner comes from the token, never the body.* There is no `owner_id` field
in the request schemas, and this pins that the router reads the authenticated
id instead.

Follows the house pattern from test_player_routes.py and test_profile_routes.py
— stand-in session, direct calls into the router, no TestClient and no
database. The statements are compiled against the real postgresql dialect
rather than inspected as objects: `ON CONFLICT ... DO UPDATE SET` is exactly
the part a stub would let you get wrong, and it only exists once compiled.
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

from pydantic import ValidationError  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from app.routers import devices  # noqa: E402
from app.schemas.device_token import (  # noqa: E402
    MAX_TOKEN_LENGTH,
    DeviceTokenRegister,
    DeviceTokenUnregister,
)

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()
TOKEN = "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]"


class _FakeSession:
    """Records what was executed and whether it was committed.

    `execute` returns None: neither route reads a result, and a stub that
    handed back a row would let a route start depending on one without the
    tests noticing.
    """

    def __init__(self):
        self.executed = []
        self.commits = 0

    async def execute(self, stmt):
        self.executed.append(stmt)
        return None

    async def commit(self):
        self.commits += 1


def _sql(stmt) -> str:
    """The statement as postgres will see it, with values inlined."""
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _register(body, user_id=OWNER):
    db = _FakeSession()
    result = asyncio.run(devices.register_device_token(body=body, db=db, user_id=user_id))
    return db, result


def _unregister(body, user_id=OWNER):
    db = _FakeSession()
    result = asyncio.run(devices.unregister_device_token(body=body, db=db, user_id=user_id))
    return db, result


# ── Registering ───────────────────────────────────────────────────────────────


def test_register_upserts_on_the_token_not_on_the_pair():
    """One row per device. Conflicting on (owner_id, token) instead would let
    the same phone hold a row for every account that ever signed into it."""
    db, _ = _register(DeviceTokenRegister(platform="ios", token=TOKEN))
    sql = _sql(db.executed[0]).lower()
    assert "on conflict" in sql
    assert "(token)" in sql, f"expected a conflict target of (token) alone, got: {sql}"
    assert "owner_id, token" not in sql.split("on conflict")[1].split("do update")[0]


def test_register_re_owns_an_existing_token():
    """The SET clause has to move owner_id, or a resold phone keeps notifying
    whoever registered it first — the leak CF-343's sign-out also guards."""
    db, _ = _register(DeviceTokenRegister(platform="android", token=TOKEN))
    set_clause = _sql(db.executed[0]).lower().split("do update set")[1]
    assert "owner_id" in set_clause
    assert "platform" in set_clause, "a device that switched platforms is the same device"
    assert "last_seen_at" in set_clause


def test_register_does_not_touch_created_at_on_conflict():
    """A re-registration is the same registration; it keeps its first-seen date.

    Also the reason last_seen_at is passed explicitly: a python-side `default=`
    is applied to the INSERT and never to the SET clause, so relying on it
    would leave last_seen_at frozen at whatever the first insert wrote.
    """
    db, _ = _register(DeviceTokenRegister(platform="ios", token=TOKEN))
    set_clause = _sql(db.executed[0]).lower().split("do update set")[1]
    assert "created_at" not in set_clause


def test_register_takes_the_owner_from_the_token_not_the_body():
    """There is no owner_id in the schema; this pins that there is no route
    around it either."""
    db, _ = _register(DeviceTokenRegister(platform="ios", token=TOKEN), user_id=STRANGER)
    assert str(STRANGER) in _sql(db.executed[0])
    assert str(OWNER) not in _sql(db.executed[0])


def test_register_commits():
    """`get_db` does not commit; every write path in this app does it itself."""
    db, result = _register(DeviceTokenRegister(platform="ios", token=TOKEN))
    assert db.commits == 1
    assert result is None  # 204


# ── Unregistering ─────────────────────────────────────────────────────────────


def test_unregister_is_scoped_to_the_caller():
    """Without the owner_id predicate, anyone holding a token — it is not a
    secret — could silence somebody else's phone."""
    db, _ = _unregister(DeviceTokenUnregister(token=TOKEN))
    sql = _sql(db.executed[0]).lower()
    assert "delete from device_tokens" in sql
    assert "owner_id" in sql, f"delete is not scoped to the caller: {sql}"
    assert str(OWNER) in _sql(db.executed[0])


def test_unregister_returns_204_even_when_nothing_matched():
    """Sign-out must not be able to fail, and a 404 on a token belonging to
    somebody else would answer whether it is registered.

    The stand-in returns no rowcount at all, which is the point: the route may
    not branch on one.
    """
    db, result = _unregister(DeviceTokenUnregister(token="never-registered"))
    assert result is None
    assert db.commits == 1


def test_unregister_asks_for_the_token_only():
    """A platform sent anyway is ignored, not honoured and not refused.

    Ignoring is pydantic's default here and the right answer rather than merely
    the cheap one: the token alone identifies the row, so a caller who sends a
    *mismatched* platform has said nothing the route needs to reconcile. What
    would be wrong is carrying it into the delete predicate, where a wrong
    guess would silently leave the device registered — so this pins that the
    field does not reach the model at all.
    """
    body = DeviceTokenUnregister(token=TOKEN, platform="ios")  # type: ignore[call-arg]
    assert not hasattr(body, "platform")
    assert body.model_dump() == {"token": TOKEN}


# ── The edge validation, which is the only validation there is ────────────────


def test_platform_is_restricted_to_the_two_we_ship():
    """The column is a plain String(16) with no database check, so this Literal
    is the whole of it."""
    for good in ("ios", "android"):
        assert DeviceTokenRegister(platform=good, token=TOKEN).platform == good
    for bad in ("web", "IOS", "", "ios "):
        with pytest.raises(ValidationError):
            DeviceTokenRegister(platform=bad, token=TOKEN)  # type: ignore[arg-type]


def test_an_empty_token_is_rejected():
    """`ON CONFLICT` would happily make an empty string the one true device."""
    with pytest.raises(ValidationError):
        DeviceTokenRegister(platform="ios", token="")
    with pytest.raises(ValidationError):
        DeviceTokenUnregister(token="")


def test_the_token_is_bounded_at_the_edge_because_the_column_is_not():
    """`token` is Text, so nothing downstream will refuse an absurd value —
    and a btree unique index stops indexing around 2704 bytes. The bound has to
    be here, and it has to be under that."""
    assert MAX_TOKEN_LENGTH < 2704
    at_limit = "x" * MAX_TOKEN_LENGTH
    assert DeviceTokenRegister(platform="ios", token=at_limit).token == at_limit
    with pytest.raises(ValidationError):
        DeviceTokenRegister(platform="ios", token="x" * (MAX_TOKEN_LENGTH + 1))
    with pytest.raises(ValidationError):
        DeviceTokenUnregister(token="x" * (MAX_TOKEN_LENGTH + 1))
