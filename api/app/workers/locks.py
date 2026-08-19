"""
Postgres advisory-lock per-game processing lock (CF-184).

`process_game_task` must never run twice for the same game at once — Celery +
Redis is at-least-once (a task outliving the broker visibility timeout, or a
worker being killed, causes redelivery), and once there is more than one worker
that redelivery can land on a *second* worker while the first is still running.
This lock makes concurrent double-processing impossible; the idempotent clip
refresh (CF-37) separately handles sequential re-runs.

**Why Postgres and not Redis.** The original lock was `SET NX EX` with a TTL
deliberately longer than the broker visibility timeout — correct against a
*slow* task, but a TTL has no relationship to whether the holder is still alive.
A hard-killed worker (every Render deploy does this) left its lock behind for
the full TTL, so the requeued copy found it held, no-opped, and the game sat in
`processing` for ~3h with nothing working on it. That stranding is what #149's
reaper existed to clean up after.

A **session-scoped** advisory lock is held by a database *connection*, and the
server drops it the moment that connection dies. Kill the worker, the connection
closes, the lock goes with it, and the redelivered task acquires it and runs.
The failure mode stops existing instead of being reaped after the fact — so
there is no TTL here, and nothing to tune against `celery_visibility_timeout`.

Two things this depends on, both handled below:

- **The lock connection is dedicated and lives for the whole task.** It is not a
  pooled session that may be recycled mid-task — a connection returned to a pool
  and handed to someone else would take the lock with it. Hence `NullPool` and a
  connection held open by the `GameLock` instance itself.
- **The connection must be session-mode.** Supabase's *transaction*-mode pooler
  (port 6543) can serve consecutive statements from different backends, which
  silently breaks session-scoped locks: `pg_try_advisory_lock` returns true and
  the lock is on some other backend, or already gone. That is worse than the bug
  this replaces, so `acquire()` verifies the lock is really held on its own
  backend and refuses to proceed if it isn't. Point `LOCK_DATABASE_URL` at the
  session-mode port (5432) when `DATABASE_URL` is a transaction pooler.

NOTE for CF-65b (prefork): the engine is built lazily, per process, on first
acquire — a forked child never inherits a parent's connection. Keep it that way
(and keep tasks.py's import of this module inside the task body).
"""
import hashlib
import logging
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

from app.config import settings

logger = logging.getLogger(__name__)

# Namespaced so the hash can never collide with an advisory key some other part
# of the system picks for a different purpose on the same database.
_KEY_NAMESPACE = b"clipfarm:process_game:"

_UINT64 = 0xFFFFFFFFFFFFFFFF
_UINT32 = 0xFFFFFFFF

_engine: Engine | None = None


class LockNotSessionScoped(RuntimeError):
    """The lock connection cannot hold a session-scoped lock.

    Raised when `pg_try_advisory_lock` succeeded but the lock is not visible on
    our own backend — the signature of a transaction-mode pooler. Loud on
    purpose: a lock that silently doesn't hold is worse than no lock at all.
    """


def lock_key(game_id: uuid.UUID | str) -> int:
    """Stable signed bigint advisory key for a game id.

    Advisory locks are keyed by number, not string, so the uuid is hashed into
    the full signed 64-bit range `pg_advisory_lock(bigint)` takes. blake2b
    rather than `hash()`, which is randomized per process by PYTHONHASHSEED and
    would give two workers different keys for the same game.
    """
    digest = hashlib.blake2b(
        _KEY_NAMESPACE + str(game_id).encode(), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


def _lock_url() -> str:
    """The URL the lock connection uses, as a sync (psycopg2) URL.

    `LOCK_DATABASE_URL` exists so the lock can use the session-mode pooler while
    the rest of the app keeps the transaction-mode one it is better served by.
    """
    url = settings.lock_database_url or settings.database_url
    return url.replace("+asyncpg", "")


def _get_engine() -> Engine:
    """Lazily build the per-process engine that hands out lock connections."""
    global _engine
    if _engine is None:
        # NullPool: every acquire() gets its own connection and closing it
        # really closes it. A pooled connection could be recycled mid-task or
        # reused for another game — either one moves the lock somewhere it
        # does not belong.
        #
        # AUTOCOMMIT: the lock connection then sits idle rather than "idle in
        # transaction" for the length of a job (hours, for a long match), which
        # would pin a snapshot and hold off VACUUM. Session-level advisory locks
        # are not transactional, so nothing is lost by not being in one.
        #
        # TCP keepalives: the connection is idle for the whole task, and an idle
        # connection silently dropped by a pooler or NAT timeout takes the lock
        # with it. Keepalives keep it demonstrably alive (psycopg2/libpq).
        _engine = create_engine(
            _lock_url(),
            poolclass=NullPool,
            isolation_level="AUTOCOMMIT",
            connect_args={
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
                "application_name": "clipfarm-worker-lock",
            },
        )
    return _engine


def _verify_session_scoped(conn: Connection, key: int) -> None:
    """Confirm the advisory lock we just took is held by *this* backend.

    `pg_locks` reports a bigint advisory key split across `classid` (high 32
    bits) and `objid` (low 32), with `objsubid = 1`. Under a transaction-mode
    pooler this second statement can run on a different backend than
    `pg_try_advisory_lock` did, so the row is missing — exactly the silent
    breakage we refuse to run on.
    """
    unsigned = key & _UINT64
    held = conn.execute(
        text(
            "SELECT 1 FROM pg_locks "
            "WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
            "AND classid = :classid AND objid = :objid AND objsubid = 1"
        ),
        {"classid": (unsigned >> 32) & _UINT32, "objid": unsigned & _UINT32},
    ).scalar()
    if not held:
        raise LockNotSessionScoped(
            "Advisory lock is not held on this connection's backend — the lock "
            "database is almost certainly a transaction-mode pooler, which "
            "cannot hold session-scoped locks. Set LOCK_DATABASE_URL to the "
            "session-mode connection (Supabase: port 5432, not 6543)."
        )


class GameLock:
    """Single-holder lock for one game_id, backed by a Postgres advisory lock.

    Held for as long as this instance keeps its connection open, and released by
    the server if the process dies — there is no TTL to outlive the holder.
    """

    def __init__(self, game_id: uuid.UUID | str):
        self.game_id = str(game_id)
        self.key = lock_key(game_id)
        self.held = False
        self._conn: Connection | None = None

    def acquire(self) -> bool:
        """Try to take the lock. Returns False if another holder has it.

        Raises rather than returning True when the lock cannot be trusted (see
        `LockNotSessionScoped`): the task must not run without real mutual
        exclusion. A connection error propagates too — that is a retryable
        failure, not a duplicate delivery to skip.
        """
        conn = _get_engine().connect()
        try:
            got = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": self.key}
                ).scalar()
            )
            if not got:
                conn.close()
                return False
            _verify_session_scoped(conn, self.key)
        except Exception:
            # Closing the connection also drops the lock, if we did take one.
            conn.close()
            raise
        self._conn = conn
        self.held = True
        return True

    def release(self) -> None:
        """
        Release the lock and close its connection.

        Never raises: this runs in a ``finally``, so a database error here must
        not mask the task's real outcome (e.g. a propagating retry). Closing the
        connection releases the lock server-side whether or not the unlock
        statement got through, and a connection we fail to close dies with the
        process.
        """
        conn, self._conn = self._conn, None
        self.held = False
        if conn is None:
            return
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.key})
        except Exception:
            logger.warning(
                "Failed to unlock game %s — closing the connection releases it",
                self.game_id,
            )
        finally:
            try:
                conn.close()
            except Exception:
                logger.warning(
                    "Failed to close the lock connection for game %s", self.game_id
                )
