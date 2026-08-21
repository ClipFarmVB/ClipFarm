"""CF-65a/CF-184: worker redelivery/concurrency safety — broker config + per-game lock.

The `importorskip` guard is now belt-and-braces: the api CI job installs
api/requirements-dev.txt, so these execute in CI as well as locally. Run from
the api/ dir: `cd api && pytest tests/test_worker_safety.py`.

The lock tests come in two layers. The fake-connection ones pin the behaviour
that is ours (what `acquire`/`release` do with the connection); the Postgres
ones pin the behaviour that is the *database's* — mutual exclusion, and a lock
dying with its connection, which is the whole point of CF-184 and cannot be
demonstrated against a double. Those find a local Postgres by themselves (the
compose `db` service is enough) or take LOCK_TEST_DATABASE_URL, which is what CI
sets for its throwaway server; they skip only when there is no database at all.
"""
import ast
import os
import pathlib
import socket
import struct
import time
import uuid
from urllib.parse import urlsplit

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


# ── Lock-loss handler routing (structural) ───────────────────────────────────

# LockLost subclasses RuntimeError, so it is caught by any `except Exception`
# ahead of it. The three `_require_lock()` checkpoints sit inside stage `try`
# blocks that catch `Exception` to keep a stage failure non-fatal — and one of
# them (the condense stage) does, then falls through to the `ready` write, which
# is how a lost lock once got stamped `ready` over another worker's live run.
# The fix is one `except LockLost: raise` per such block. The behaviour is not
# unit-testable without a seam into `process_game_task`, but the routing is:
# resolve each checkpoint to the handler that catches it and require it to be a
# LockLost one. This flips the moment a guard is removed.

_TASKS_PY = pathlib.Path(__file__).resolve().parents[1] / "app" / "workers" / "tasks.py"

# Any of these ahead of a LockLost handler would swallow a LockLost.
_CATCHES_LOCKLOST = {"LockLost", "RuntimeError", "Exception", "BaseException"}


def _caught_type_names(handler: ast.ExceptHandler) -> list[str] | None:
    """Exception names an `except` clause names, or None for a bare `except:`."""
    node = handler.type
    if node is None:
        return None
    elts = node.elts if isinstance(node, ast.Tuple) else [node]
    names = []
    for e in elts:
        if isinstance(e, ast.Name):
            names.append(e.id)
        elif isinstance(e, ast.Attribute):
            names.append(e.attr)
    return names


def _first_handler_catching_locklost(try_node: ast.Try) -> str | None:
    """The name the first LockLost-catching handler on `try_node` matches on."""
    for handler in try_node.handlers:
        names = _caught_type_names(handler)
        if names is None:            # bare except catches everything
            return "bare except"
        for name in names:
            if name in _CATCHES_LOCKLOST:
                return name
    return None


def test_every_require_lock_checkpoint_routes_to_a_locklost_handler():
    """Each `_require_lock()` must reach a LockLost handler before any broad one.

    Walks the enclosing `try` blocks of every checkpoint from innermost out; the
    first one whose handlers can catch a LockLost has to catch it *as* LockLost.
    A stage's `except Exception` sitting in front of that is the condense-stage
    bug: LockLost swallowed, execution falling through to a terminal status
    write for a game another worker owns.
    """
    tree = ast.parse(_TASKS_PY.read_text(encoding="utf-8"))
    parent_of = {
        child: node
        for node in ast.walk(tree)
        for child in ast.iter_child_nodes(node)
    }

    def enclosing_catchers(call: ast.AST):
        """(Try, child) pairs where the call is in the try *body*, innermost out.

        Only the body is caught by that try's handlers — a call in an `else`,
        `except`, or `finally` propagates past them — so those are skipped.
        """
        node = call
        while node in parent_of:
            parent = parent_of[node]
            if isinstance(parent, ast.Try) and node in parent.body:
                yield parent
            node = parent

    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_require_lock"
    ]
    assert len(calls) == 3, (
        f"expected 3 _require_lock() checkpoints, found {len(calls)} — update "
        "this test if a checkpoint was added or removed"
    )

    for call in calls:
        for try_node in enclosing_catchers(call):
            match = _first_handler_catching_locklost(try_node)
            if match is None:
                continue            # this try cannot catch it; look further out
            assert match == "LockLost", (
                f"_require_lock() at tasks.py:{call.lineno} is caught by "
                f"`except {match}` (try at line {try_node.lineno}) before any "
                "LockLost handler — a lost lock would be swallowed and the job "
                "would run on. Add `except LockLost: raise` ahead of it."
            )
            break
        else:
            raise AssertionError(
                f"_require_lock() at tasks.py:{call.lineno} is not inside any "
                "try block — it can no longer abort the job on a lost lock."
            )


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


def test_engine_asks_for_server_side_keepalives(monkeypatch):
    """Client keepalives detect a dead *server*; these detect a dead *client*.

    They are what bounds the one case the lock does not cover instantly — a
    connection severed with no FIN, where the backend would otherwise hold the
    lock until the OS default (~2h) noticed. Silently ignored through a pooler
    (the backend's peer is the pooler, not us), which is why locks.py documents
    the residual rather than claiming release is always immediate.
    """
    from app.workers import locks

    opts = locks.SERVER_KEEPALIVE_OPTIONS
    assert "tcp_keepalives_idle=" in opts
    assert "tcp_keepalives_interval=" in opts
    assert "tcp_keepalives_count=" in opts

    captured = {}
    monkeypatch.setattr(locks, "_engine", None)
    monkeypatch.setattr(
        locks, "create_engine", lambda url, **kw: captured.update(kw) or "engine"
    )
    locks._get_engine()
    monkeypatch.setattr(locks, "_engine", None)

    assert opts in captured["connect_args"]["options"]
    assert captured["connect_args"]["keepalives"] == 1


def test_every_call_on_the_lock_connection_is_bounded_in_time(monkeypatch):
    """Keepalives are not a bound on a call that is already in flight.

    They only fire on an *idle* socket, so a query sent just as the connection
    is severed waits on TCP retransmission instead — ~15 min at the default
    tcp_retries2. `still_held()` is one round trip at a stage boundary and
    `release()` runs in a `finally`; neither may stall a worker that long on a
    job it has already lost. `tcp_user_timeout` covers the severed socket,
    `statement_timeout` the backend that is alive but never answers, and
    `connect_timeout` the acquire that cannot reach the server at all.
    """
    from app.workers import locks

    captured = {}
    monkeypatch.setattr(locks, "_engine", None)
    monkeypatch.setattr(
        locks, "create_engine", lambda url, **kw: captured.update(kw) or "engine"
    )
    locks._get_engine()
    monkeypatch.setattr(locks, "_engine", None)

    args = captured["connect_args"]
    assert args["connect_timeout"] > 0
    assert args["tcp_user_timeout"] == locks.LOCK_TCP_USER_TIMEOUT_MS
    assert f"statement_timeout={locks.LOCK_STATEMENT_TIMEOUT_MS}" in args["options"]


class _LockLostProbe(RuntimeError):
    """Stand-in for LockLost. Module level so the eager result can unpickle it."""


def test_retry_exhaustion_reraises_the_original_exception():
    """What the LockLost handler in tasks.py reads as "retries are spent".

    `self.retry(exc=...)` does *not* raise MaxRetriesExceededError once the
    retries are gone — Celery re-raises the exception it was handed. So the
    handler distinguishes the two cases by `Retry` vs anything else, and this
    pins the behaviour that makes that the right reading.
    """
    from celery import Celery
    from celery.exceptions import MaxRetriesExceededError, Retry

    app = Celery("lock_retry_probe")
    app.conf.task_always_eager = True

    seen = {}

    @app.task(bind=True, max_retries=0)
    def _spent(self):
        try:
            raise self.retry(exc=_LockLostProbe("lock lost"))
        except Retry:                       # a real retry — never on this path
            seen["retry"] = True
            raise

    result = _spent.apply()
    assert "retry" not in seen
    assert isinstance(result.result, _LockLostProbe)
    assert not isinstance(result.result, MaxRetriesExceededError)


def test_acquire_unlocks_before_closing_on_the_error_path(monkeypatch):
    """The pooler case is exactly where `close()` alone is not enough.

    A transaction pooler returns the backend to its pool without a DISCARD, so a
    session lock left on it outlives the connection and blocks that game's key
    for the life of that backend. Unlock explicitly, then close.
    """
    conn = _FakeConn(
        {"pg_try_advisory_lock": True, "pg_locks": None, "pg_advisory_unlock": True}
    )
    locks = _patch_engine(monkeypatch, conn)

    with pytest.raises(locks.LockNotSessionScoped):
        locks.GameLock(uuid.uuid4()).acquire()

    assert any("pg_advisory_unlock" in stmt for stmt in conn.statements)
    assert conn.closed is True


def test_refused_pooler_ports_are_configurable(monkeypatch):
    """6543 is Supabase's; a PgBouncer elsewhere answers on whatever port it got.

    Nothing in the protocol announces transaction pooling, and the runtime check
    fails open under load, so a deployment behind another pooler has to be able
    to declare its port rather than silently falling through to the net.
    """
    from app.config import settings
    from app.workers import locks

    monkeypatch.setattr(settings, "lock_pooler_ports", "6543, 6432")
    monkeypatch.setattr(settings, "lock_database_url", "postgresql://u:p@bouncer:6432/postgres")
    monkeypatch.setattr(locks, "_engine", None)

    with pytest.raises(locks.LockNotSessionScoped):
        locks._lock_url()

    monkeypatch.setattr(settings, "lock_database_url", "postgresql://u:p@direct:5432/postgres")
    assert locks._lock_url().endswith("5432/postgres")


def test_still_held_re_probes_before_declaring_the_lock_lost(monkeypatch):
    """A blip must not cost an hour of ball tracking.

    `still_held()` answering False aborts the job and re-runs it from scratch,
    so a single failed query is re-probed. A *successful* query reporting no
    lock is final — that one is not ambiguous.
    """
    from app.workers import locks

    monkeypatch.setattr(locks, "_RE_PROBE_DELAY_SECONDS", 0)
    conn = _FakeConn({"pg_try_advisory_lock": True, "pg_locks": 1})
    locks_mod = _patch_engine(monkeypatch, conn)

    lock = locks_mod.GameLock(uuid.uuid4())
    assert lock.acquire() is True

    calls = {"n": 0}
    blip = {"armed": True}
    real_execute = conn.execute

    def flaky(clause, params=None):
        if "pg_locks" in str(clause):
            calls["n"] += 1
            if blip["armed"]:
                blip["armed"] = False
                raise RuntimeError("momentary hiccup")
        return real_execute(clause, params)

    monkeypatch.setattr(conn, "execute", flaky)
    assert lock.still_held() is True        # recovered on the second probe
    assert calls["n"] == 2

    conn.results["pg_locks"] = None         # a clean "not held" is final
    calls["n"] = 0
    assert lock.still_held() is False
    assert calls["n"] == 1, "a definitive answer must not be re-probed"


def test_transaction_pooler_port_is_refused_without_connecting(monkeypatch):
    """The runtime `pg_locks` check is a net; the port is the deterministic guard.

    A transaction pooler with an idle pool often serves both statements from the
    same backend, so the check passes precisely when someone is validating a new
    LOCK_DATABASE_URL by hand. Port 6543 cannot be right, so it never gets as far
    as connecting.
    """
    from app.config import settings
    from app.workers import locks

    monkeypatch.setattr(settings, "lock_database_url", "postgresql://u:p@db.example:6543/postgres")
    monkeypatch.setattr(locks, "_engine", None)

    with pytest.raises(locks.LockNotSessionScoped) as exc:
        locks._lock_url()
    assert "6543" in str(exc.value)


def test_still_held_reports_false_on_a_dead_connection(monkeypatch):
    """"Cannot ask" and "not held" are the same answer here.

    Treating an unanswerable connection as still-locked is the reading that lets
    a job keep processing next to a redelivered copy.
    """
    conn = _FakeConn({"pg_try_advisory_lock": True, "pg_locks": 1})
    locks = _patch_engine(monkeypatch, conn)

    lock = locks.GameLock(uuid.uuid4())
    assert lock.acquire() is True
    assert lock.still_held() is True

    conn.results["pg_locks"] = RuntimeError("server closed the connection")
    assert lock.still_held() is False


def test_release_warns_when_the_lock_was_already_gone(monkeypatch, caplog):
    """`pg_advisory_unlock` returning false is the only trace of a lost lock."""
    conn = _FakeConn(
        {"pg_try_advisory_lock": True, "pg_locks": 1, "pg_advisory_unlock": False}
    )
    locks = _patch_engine(monkeypatch, conn)

    lock = locks.GameLock(uuid.uuid4())
    assert lock.acquire() is True
    with caplog.at_level("WARNING"):
        lock.release()

    assert "may have run unprotected" in caplog.text


# ── Against a real Postgres ──────────────────────────────────────────────────

# Tried in order when LOCK_TEST_DATABASE_URL is unset. Localhost only, and
# never `settings.database_url`: auto-detection must not be able to take locks
# on a shared database because someone ran the suite. The compose `db` service
# publishes 5432, so a running dev stack is enough for these to execute — in the
# pre-commit hook as well as CI, which is the point (they are the tests that
# actually demonstrate CF-184).
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


def _lock_db_url() -> str:
    """A Postgres URL for the lock tests, or skip.

    An explicit LOCK_TEST_DATABASE_URL wins (CI sets it). Otherwise a local
    Postgres is auto-detected, so a developer with the compose stack up runs
    these without opting in.
    """
    global _RESOLVED_URL
    if _RESOLVED_URL is None:
        _RESOLVED_URL = os.environ.get("LOCK_TEST_DATABASE_URL", "") or next(
            (c for c in _LOCAL_CANDIDATES if _reachable(c)), ""
        )
    if not _RESOLVED_URL:
        pytest.skip(
            "no local Postgres (start the compose stack, or set "
            "LOCK_TEST_DATABASE_URL) — the advisory-lock tests need a real server"
        )
    return _RESOLVED_URL


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
        pytest.skip(f"the lock test database is not reachable: {exc}")
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


def _sever(conn) -> None:
    """Make the next close of `conn` an RST rather than a clean shutdown.

    `SO_LINGER {on, 0}` means the final close discards the socket and sends a
    reset instead of a FIN, which is the closest a test can get to a worker that
    disappears — a graceful `close()` is a *polite* disconnect and would let the
    lock go even if abrupt loss did not.

    The wrapper is `detach()`ed rather than closed: it borrows libpq's fd only
    to set the option, and closing it too would free an fd libpq still owns.
    """
    raw = conn.connection.dbapi_connection
    sock = socket.socket(fileno=raw.fileno())
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.detach()
    raw.close()
    # Tell SQLAlchemy the DBAPI connection is gone, so its own cleanup doesn't
    # try to roll back a dead socket at GC time and print a spurious traceback.
    conn.invalidate()


def test_still_held_notices_a_terminated_backend(pg_locks):
    """The failure the checkpoints in tasks.py exist for.

    The worker is alive and working; its *lock connection* is killed out from
    under it (failover, `idle_session_timeout`, `pg_terminate_backend`). Postgres
    releases the lock immediately, so a redelivery would acquire it and run
    beside a job that never stopped — unless the holder notices, which is what
    `still_held()` is for.
    """
    from sqlalchemy import text

    lock = pg_locks.GameLock(uuid.uuid4())
    assert lock.acquire() is True
    assert lock.still_held() is True

    with pg_locks._get_engine().connect() as killer:
        pid = lock._conn.execute(text("SELECT pg_backend_pid()")).scalar()
        killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})

    assert lock.still_held() is False
    lock.release()      # must stay quiet on a dead connection


def test_lock_dies_with_the_worker(pg_locks):
    """The CF-184 acceptance test, in miniature.

    A hard-killed worker never runs `release()` — its connection just dies. The
    lock must go with it so the redelivered task can acquire it, rather than
    outliving the holder on a TTL and stranding the game in `processing`.

    The kill here is an RST: no unlock, no FIN, the connection is torn away.
    What this still does NOT cover is a *silent* loss — a partition where no
    packet reaches the server at all, so the backend sits blocked on a read and
    only the server's TCP keepalives reap it. That tail is minutes (bounded by
    the `tcp_keepalives_idle` locks.py asks for) rather than instant, and it is
    the one case that needs a real network to demonstrate.
    """
    gid = uuid.uuid4()
    killed = pg_locks.GameLock(gid)
    assert killed.acquire() is True

    _sever(killed._conn)                   # the worker dies; no unlock, no FIN
    killed.held = False

    # Postgres frees the lock when it reaps the backend, which is prompt but not
    # synchronous with our end of the socket dying — poll rather than assert on
    # the first try.
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


# ── error_message must never be what strands a game (CF-225) ──────────────
#
# The same stranded-in-`processing` symptom as the lock cases above, reached
# from the opposite end: not a lock that outlives its holder, but the failure
# *report* raising while it is written. `sync_set_game_status(…, "failed", …)`
# runs from inside the task's own `except` handler, so a DataError there costs
# the `failed` write and the retry decision — the `finally` still runs and the
# lock is released, but the row never leaves `processing`. The message is
# `str(exc)` from an arbitrary failure; a remote Modal traceback is not a rare
# shape for it.

def test_error_message_is_clamped_to_the_column_width():
    from app.models.game import Game
    from app.workers._sync_db import _fit_error_message

    width = getattr(Game.__table__.c.error_message.type, "length", None)
    if not width:
        pytest.skip("error_message is unbounded (Text) — nothing to clamp")

    fitted = _fit_error_message("HEAD" + "x" * (width * 5) + "TAIL")

    assert len(fitted) == width
    assert fitted.startswith("HEAD") and fitted.endswith("TAIL"), (
        "cut the middle: the type and message lead, and the frame that actually "
        "raised is last — a tail cut keeps the preamble and drops the answer"
    )
    assert "…" in fitted, "a silent cut reads as a complete message"
    assert _fit_error_message("short") == "short", "and nothing else is touched"


def test_the_clamp_reads_the_width_off_the_column():
    """Not a repeated literal. CF-226 widens this column to `Text` (as CF-217
    did for games.upload_id after the same overflow), and that change must not
    need a matching edit here to stay correct — the clamp no-ops on its own.

    `getattr` rather than a plain attribute access, and not only to satisfy
    mypy: `Text` genuinely has no `length`, so the direct form would break at
    exactly the moment that card lands."""
    from app.models.game import Game
    from app.workers import _sync_db

    assert _sync_db._ERROR_MESSAGE_MAX is getattr(
        Game.__table__.c.error_message.type, "length", None
    )


def test_every_error_message_writer_goes_through_the_clamp():
    """Pinned as an invariant over the module, not over today's two call sites.

    The clamp lives in `_sync_db` precisely so a future writer inherits it; a
    test of the two current callers would not notice the third. All three ways
    to write the column are covered — plain assignment, `setattr`, and a Core
    `update().values(...)` — because the point is to catch the writer nobody
    thought to check, and that one will not be an `ast.Assign`.
    """
    source = (pathlib.Path(__file__).resolve().parents[1] / "app" / "workers" / "_sync_db.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))

    def is_guarded(value) -> bool:
        # Clearing it (a run starting fresh) is fine; anything else must be the
        # clamp's return value.
        if isinstance(value, ast.Constant) and value.value is None:
            return True
        return (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_fit_error_message"
        )

    unguarded = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "error_message":
                    if not is_guarded(node.value):
                        unguarded.append(node.lineno)
        elif isinstance(node, ast.Call):
            # setattr(game, "error_message", value)
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
                and len(node.args) == 3
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "error_message"
                and not is_guarded(node.args[2])
            ):
                unguarded.append(node.lineno)
            # anything(..., error_message=value) — .values(), .filter_by(), a
            # future helper. Over-broad on purpose: a false positive here is a
            # one-line fix, a false negative strands a game.
            for kw in node.keywords:
                if kw.arg == "error_message" and not is_guarded(kw.value):
                    unguarded.append(node.lineno)

    assert not unguarded, (
        f"_sync_db.py:{sorted(set(unguarded))} writes error_message without "
        "_fit_error_message — an over-long value raises DataError inside the "
        "task's except handler and strands the game in `processing`"
    )
