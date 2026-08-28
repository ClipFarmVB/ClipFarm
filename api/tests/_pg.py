"""Finding a real Postgres for the tests that need one.

Extracted from test_worker_safety.py when a second suite needed the same
answer. Two copies of "which database do the integration tests use" is the
duplication that ends with one of them pointed somewhere it must not be — and
the rule that matters here is a safety rule, not a convenience one: **never
`settings.database_url`**. Auto-detection must not be able to reach a shared
database because somebody ran the suite.

CI sets `LOCK_TEST_DATABASE_URL` (named for CF-184, which was the first
caller) against its throwaway `postgres:16` service; locally the compose `db`
service publishes 5432, so a running dev stack is enough and nobody has to opt
in. With neither, the callers skip.
"""
import os
import socket
from urllib.parse import urlsplit

import pytest

# Tried in order when LOCK_TEST_DATABASE_URL is unset. Localhost only.
_LOCAL_CANDIDATES = (
    "postgresql://postgres:postgres@localhost:5432/clipfarm",
    "postgresql://postgres:postgres@localhost:5432/postgres",
)


def _reachable(url: str) -> bool:
    """Can we open a session on `url`? Cheap probe first, so a machine with no
    Postgres at all costs a refused TCP connect rather than a connect timeout."""
    import psycopg2

    parts = urlsplit(url)
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex((parts.hostname or "localhost", parts.port or 5432)) != 0:
            return False
    try:
        psycopg2.connect(url, connect_timeout=2).close()
        return True
    except Exception:
        return False


# Detection runs once per session: it is the same answer every time, and paying
# it per test is what makes an unrunnable suite feel slow.
_RESOLVED_URL: str | None = None


def pg_url(purpose: str) -> str:
    """A Postgres URL, or skip with `purpose` in the message.

    `purpose` rather than a fixed sentence because the skip line is the only
    thing a developer sees when these do not run, and "the advisory-lock tests
    need a real server" is a confusing thing to read when the suite that
    skipped was about post visibility.
    """
    global _RESOLVED_URL
    if _RESOLVED_URL is None:
        _RESOLVED_URL = os.environ.get("LOCK_TEST_DATABASE_URL", "") or next(
            (c for c in _LOCAL_CANDIDATES if _reachable(c)), ""
        )
    if not _RESOLVED_URL:
        pytest.skip(
            "no local Postgres (start the compose stack, or set "
            f"LOCK_TEST_DATABASE_URL) — {purpose}"
        )
    return _RESOLVED_URL
