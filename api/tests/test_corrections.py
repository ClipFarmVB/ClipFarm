"""CF-10: corrections export — user scoping and CSV safety.

Guarded with `importorskip` because the api CI job installs only ruff/mypy/pytest
today; these run locally and light up in CI once api/requirements.txt is
installed there (see the note in .github/workflows/ci.yml).
"""
import asyncio
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from app.routers import corrections  # noqa: E402


class _CapturingSession:
    """Stands in for AsyncSession: records the statement instead of running it."""

    def __init__(self):
        self.statement = None

    async def execute(self, statement):
        self.statement = statement

        class _Result:
            def scalars(self):
                class _Scalars:
                    def all(self):
                        return []

                return _Scalars()

        return _Result()


def _captured_statement(user_id, **kwargs):
    session = _CapturingSession()
    asyncio.run(corrections._list_corrections(user_id, session, **kwargs))
    return session.statement


def test_list_query_is_scoped_to_the_caller():
    """The security boundary: user A's query must filter on user A's id.

    Without this predicate the endpoint would return every user's corrections.
    """
    user_id = uuid.uuid4()
    stmt = _captured_statement(user_id, limit=100)

    compiled = stmt.compile()
    assert "corrections.user_id" in str(compiled)
    assert user_id in compiled.params.values()


def test_another_users_id_is_never_bound():
    """Two different callers must not produce the same filter value."""
    a, b = uuid.uuid4(), uuid.uuid4()
    params_a = _captured_statement(a, limit=100).compile().params.values()

    assert a in params_a
    assert b not in params_a


def test_export_path_is_scoped_too():
    """The CSV export uses limit=None; scoping must not depend on the limit."""
    user_id = uuid.uuid4()
    stmt = _captured_statement(user_id, limit=None)

    compiled = stmt.compile()
    assert "corrections.user_id" in str(compiled)
    assert user_id in compiled.params.values()


@pytest.mark.parametrize("payload", ["=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx"])
def test_csv_safe_neutralizes_formula_payloads(payload):
    assert corrections._csv_safe(payload).startswith("'")


@pytest.mark.parametrize("payload", ["spike", "", "dig 3", "not_an_action"])
def test_csv_safe_leaves_ordinary_labels_alone(payload):
    assert corrections._csv_safe(payload) == payload
