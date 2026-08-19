"""CF-65a/CF-184: worker redelivery/concurrency safety — broker config + per-game lock.

The `importorskip` guard is now belt-and-braces: the api CI job installs
api/requirements-dev.txt, so these execute in CI as well as locally. Run from
the api/ dir: `cd api && pytest tests/test_worker_safety.py`.

The lock tests come in two layers. The fake-connection ones pin the behaviour
that is ours (what `acquire`/`release` do with the connection); the Postgres
ones pin the behaviour that is the *database's* — mutual exclusion, and a lock
dying with its connection, which is the whole point of CF-184 and cannot be
demonstrated against a double. Those skip without a database; CI runs a
throwaway Postgres so they execute there (see `.github/workflows/ci.yml`).
"""
import os
import time
import uuid

import pytest

pytest.importorskip("celery")


def test_celery_redelivery_config():
    """The broker settings that prevent the CF-45 redelivery class must be set."""
    from app.workers.celery_app import celery_app

    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    vt = conf.broker_transport_options.get("visibility_timeout")
    assert vt and vt > 3600, "visibility_timeout must exceed Redis' 3600s default"


def test_results_are_ignored_without_weakening_redelivery_safety():
    """CF-150: results are never read, so storing them only fills the broker.

    In production the broker and result backend are the same Key Value instance
    (#98) under `noeviction`, so unread results accumulate in the store whose
    fullness blocks job submission.

    Asserted together with the CF-65a settings on purpose. The two changes append
    to the same config block and met as a textual conflict, which is exactly the
    merge that silently drops one side — and they are independent, since
    `acks_late` and the retry path re-publish to the *broker* and never touch the
    result backend.
    """
    from app.workers.celery_app import celery_app

    conf = celery_app.conf
    assert conf.task_ignore_result is True
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.broker_transport_options.get("visibility_timeout")


def test_lock_has_no_ttl_setting():
    """CF-184 removed `process_lock_ttl_seconds`, and it must stay removed.

    A TTL was only ever a guess at whether the holder was still alive, and it is
    what stranded a hard-killed game in `processing` for 3h. Re-adding one to
    "bound" the advisory lock would restore that failure mode.
    """
    from app.config import settings

    assert not hasattr(settings, "process_lock_ttl_seconds")


# ── Lock key derivation ──────────────────────────────────────────────────────

def test_lock_key_is_stable_across_processes_and_fits_bigint():
    """Two workers must derive the same key for a game, or the lock is no lock.

    Rules out `hash()`, which PYTHONHASHSEED randomizes per process. The range
    check is what keeps `pg_advisory_lock(bigint)` from erroring on overflow.
    """
    from app.workers.locks import lock_key

    gid = uuid.UUID("11111111-2222-3333-4444-555555555555")
    key = lock_key(gid)

    assert key == lock_key(gid)
    assert key == lock_key(str(gid)), "uuid and its string must agree"
    assert -(2 ** 63) <= key < 2 ** 63
    assert key != lock_key(uuid.UUID("11111111-2222-3333-4444-555555555556"))


# ── acquire()/release() connection handling (fake connection) ────────────────

class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeConn:
    """Minimal stand-in for a SQLAlchemy Connection.

    `results` maps a substring of the statement to what `.scalar()` returns; an
    Exception instance is raised instead.
    """

    def __init__(self, results):
        self.results = results
        self.closed = False
        self.statements: list[str] = []

    def execute(self, clause, params=None):
        sql = str(clause)
        self.statements.append(sql)
        for fragment, value in self.results.items():
            if fragment in sql:
                if isinstance(value, Exception):
                    raise value
                return _FakeResult(value)
        raise AssertionError(f"unexpected statement: {sql}")

    def close(self):
        self.closed = True


def _patch_engine(monkeypatch, conn):
    from app.workers import locks

    class _FakeEngine:
        def connect(self):
            return conn

    monkeypatch.setattr(locks, "_get_engine", lambda: _FakeEngine())
    return locks


def test_acquire_returns_false_and_closes_when_another_holder_has_it(monkeypatch):
    """A duplicate delivery must release its connection, not leak one per retry."""
    conn = _FakeConn({"pg_try_advisory_lock": False})
    locks = _patch_engine(monkeypatch, conn)

    lock = locks.GameLock(uuid.uuid4())
    assert lock.acquire() is False
    assert lock.held is False
    assert conn.closed is True


def test_acquire_refuses_a_lock_that_is_not_session_scoped(monkeypatch):
    """A transaction-mode pooler returns true without holding the lock.

    Processing under that is worse than the bug CF-184 fixes — two workers on
    one game — so acquire() must raise rather than report success, and must drop
    the connection on the way out.
    """
    conn = _FakeConn({"pg_try_advisory_lock": True, "pg_locks": None})
    locks = _patch_engine(monkeypatch, conn)

    lock = locks.GameLock(uuid.uuid4())
    with pytest.raises(locks.LockNotSessionScoped):
        lock.acquire()
    assert lock.held is False
    assert conn.closed is True


def test_release_closes_the_connection_even_if_unlock_fails(monkeypatch):
    """release() runs in a `finally` and must never mask the task's outcome.

    Closing the connection is what actually frees the lock server-side, so it
    has to happen even when the unlock statement errors.
    """
    conn = _FakeConn({"pg_try_advisory_lock": True, "pg_locks": 1})
    locks = _patch_engine(monkeypatch, conn)

    lock = locks.GameLock(uuid.uuid4())
    assert lock.acquire() is True
    conn.results["pg_advisory_unlock"] = RuntimeError("connection gone")

    lock.release()          # must not raise
    assert conn.closed is True
    assert lock.held is False

    lock.release()          # a second release is a no-op, not an error


# ── Against a real Postgres ──────────────────────────────────────────────────

def _lock_db_url() -> str:
    """A Postgres URL for the lock tests, or skip.

    LOCK_TEST_DATABASE_URL is opt-in on purpose: these need a real server, and a
    silent fallback to whatever DATABASE_URL names could take locks on a shared
    database from a test run.
    """
    url = os.environ.get("LOCK_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("set LOCK_TEST_DATABASE_URL to run the advisory-lock tests")
    return url


@pytest.fixture
def pg_locks(monkeypatch):
    """`app.workers.locks` pointed at the test database, engine reset per test."""
    pytest.importorskip("psycopg2")
    from app.config import settings
    from app.workers import locks

    monkeypatch.setattr(settings, "lock_database_url", _lock_db_url())
    monkeypatch.setattr(locks, "_engine", None)
    try:
        locks._get_engine().connect().close()
    except Exception as exc:                      # pragma: no cover - env dependent
        pytest.skip(f"LOCK_TEST_DATABASE_URL is not reachable: {exc}")
    yield locks
    locks._engine = None


def test_lock_is_mutually_exclusive(pg_locks):
    gid = uuid.uuid4()
    a = pg_locks.GameLock(gid)
    b = pg_locks.GameLock(gid)
    try:
        assert a.acquire() is True
        assert b.acquire() is False        # a holds it
        a.release()
        assert b.acquire() is True         # freed
    finally:
        a.release()
        b.release()


def test_different_games_do_not_block_each_other(pg_locks):
    a = pg_locks.GameLock(uuid.uuid4())
    b = pg_locks.GameLock(uuid.uuid4())
    try:
        assert a.acquire() is True
        assert b.acquire() is True
    finally:
        a.release()
        b.release()


def test_lock_dies_with_the_worker(pg_locks):
    """The CF-184 acceptance test, in miniature.

    A hard-killed worker never runs `release()` — its connection just dies. The
    lock must go with it so the redelivered task can acquire it, rather than
    outliving the holder on a TTL and stranding the game in `processing`.
    Dropping the connection without unlocking is that kill.
    """
    gid = uuid.uuid4()
    killed = pg_locks.GameLock(gid)
    assert killed.acquire() is True

    killed._conn.close()                   # the worker dies; no unlock is sent
    killed.held = False

    # Postgres frees the lock when it reaps the backend, which is prompt but not
    # synchronous with our close() — poll rather than assert on the first try.
    redelivered = pg_locks.GameLock(gid)
    try:
        for _ in range(50):
            if redelivered.acquire():
                break
            time.sleep(0.1)
        else:
            pytest.fail("lock outlived the connection that held it")
    finally:
        redelivered.release()
