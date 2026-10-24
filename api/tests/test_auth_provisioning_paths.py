"""CF-295: what `_ensure_user_exists` does after its INSERT raises.

`test_auth_provisioning_pg.py` runs against a real Postgres and pins the
behaviour of the constraint itself, but it cannot reach these branches on
purpose. The one production hit on 2026-09-22 needs two first requests to
interleave inside Postgres's own insert path — the loser past the arbiter
check on `id` before the winner's index entry lands — and no test can schedule
that. Seeding the row first, which the pg suite does, returns at the read-first
SELECT and never reaches the `except` at all.

So the control flow after the IntegrityError is pinned here, with a session
stub that answers each query in order. That is the thing actually worth pinning:
given the INSERT raised, which of the three outcomes follows, and what reaches
the log. It also runs anywhere, where the pg suite needs a server.

Every test checks the log for the email. Postgres puts the row's values in the
error's DETAIL line, the first Sentry event for this bug carried the user's
address, and Sentry's scrubbing redacts secrets rather than personal data.

`asyncio.run` inside plain defs is the house pattern; see
`test_async_tests_would_be_silently_skipped.py`.
"""
import asyncio
import logging
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.auth import _ensure_user_exists  # noqa: E402

EMAIL = "someone.real@example.com"


class _UniqueViolation(Exception):
    """Stands in for asyncpg's UniqueViolationError: the attribute is the
    point, and the message carries the email the way Postgres's does."""

    def __init__(self, constraint_name):
        super().__init__(
            f'duplicate key value violates unique constraint "{constraint_name}"\n'
            f"DETAIL:  Key (email)=({EMAIL}) already exists."
        )
        self.constraint_name = constraint_name


def _integrity_error(constraint_name="users_email_key"):
    return IntegrityError(
        "INSERT INTO users (id, email) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
        {"id": "x", "email": EMAIL},
        _UniqueViolation(constraint_name),
    )


class _StubSession:
    """Answers `scalar` from a script, raises on the INSERT, counts the rest."""

    def __init__(self, scalars, insert_error):
        self._scalars = list(scalars)
        self._insert_error = insert_error
        self.commits = 0
        self.rollbacks = 0

    async def scalar(self, _stmt):
        assert self._scalars, "a query ran that the test did not script"
        return self._scalars.pop(0)

    async def execute(self, _stmt):
        raise self._insert_error

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    def all_answered(self):
        return not self._scalars


def _call(session, user_id):
    return asyncio.run(_ensure_user_exists(user_id, EMAIL, session))


def test_losing_the_race_to_our_own_row_returns(caplog):
    """The production case: the first read found nothing, the INSERT lost to a
    concurrent request for the same user, and the re-read finds our row."""
    uid = uuid.uuid4()
    session = _StubSession([None, uid], _integrity_error())

    with caplog.at_level(logging.DEBUG):
        _call(session, uid)                      # must not raise

    assert session.rollbacks == 1, "the session must be rolled back before the re-read"
    assert session.all_answered()
    assert EMAIL not in caplog.text


def test_an_email_held_by_another_id_is_409_and_logs_no_email(caplog):
    uid, holder = uuid.uuid4(), uuid.uuid4()
    session = _StubSession([None, None, holder], _integrity_error())

    with caplog.at_level(logging.DEBUG), pytest.raises(HTTPException) as exc:
        _call(session, uid)

    assert exc.value.status_code == 409
    assert str(holder) in caplog.text and str(uid) in caplog.text
    assert EMAIL not in caplog.text


def test_an_unexplained_violation_is_503_named_by_constraint(caplog):
    """No row of ours, no holder of the email: something else broke. The log
    has to say what, and must not do it by attaching the error."""
    uid = uuid.uuid4()
    session = _StubSession([None, None, None], _integrity_error("some_other_key"))

    with caplog.at_level(logging.DEBUG), pytest.raises(HTTPException) as exc:
        _call(session, uid)

    assert exc.value.status_code == 503
    assert "some_other_key" in caplog.text
    assert EMAIL not in caplog.text
    assert all(r.exc_info is None for r in caplog.records), (
        "an attached exception carries the email in its message"
    )
