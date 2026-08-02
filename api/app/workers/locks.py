"""
Redis-backed per-game processing lock (CF-65a).

`process_game_task` must never run twice for the same game at once — Celery +
Redis is at-least-once (a task outliving the broker visibility timeout, or a
worker being killed, causes redelivery), and once there is more than one worker
that redelivery can land on a *second* worker while the first is still running.
This lock makes concurrent double-processing impossible; the idempotent clip
refresh (CF-37) separately handles sequential re-runs.

Fork safety (CF-65b): the client is built lazily, per process, and tagged with
the pid that created it. A prefork child that inherits the parent's client
rebuilds its own on first use instead of sharing one socket across processes —
which previously depended on `tasks.py` keeping its import inside the task body.
`reset_client()` (called from the `worker_process_init` hook) makes that explicit
rather than incidental.
"""
import logging
import os
import uuid

import redis

from app.config import settings

logger = logging.getLogger(__name__)

# Compare-and-delete: only release the lock if we still hold this exact token,
# so we never delete a lock that expired and was re-acquired by another worker.
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('del', KEYS[1]) else return 0 end"
)

_client: redis.Redis | None = None
_release = None
_owner_pid: int | None = None


def reset_client() -> None:
    """Discard this process's cached client (called after a fork)."""
    global _client, _release, _owner_pid
    _client = None
    _release = None
    _owner_pid = None


def _get_client():
    """Return a Redis client owned by the current process, building it if the
    cached one was inherited across a fork."""
    global _client, _release, _owner_pid
    if _client is None or _owner_pid != os.getpid():
        _client = redis.Redis.from_url(settings.redis_url)
        _release = _client.register_script(_RELEASE_LUA)
        _owner_pid = os.getpid()
    return _client, _release


class GameLock:
    """Single-holder lock for one game_id, backed by Redis SET NX EX."""

    def __init__(self, game_id: uuid.UUID | str, ttl_seconds: int):
        self.key = f"lock:process_game:{game_id}"
        self.token = uuid.uuid4().hex
        self.ttl = ttl_seconds
        self.held = False

    def acquire(self) -> bool:
        """Try to take the lock. Returns False if another holder has it."""
        client, _ = _get_client()
        self.held = bool(client.set(self.key, self.token, nx=True, ex=self.ttl))
        return self.held

    def release(self) -> None:
        """
        Release the lock, but only if we still hold this token.

        Never raises: this runs in a ``finally``, so a Redis error here must not
        mask the task's real outcome (e.g. a propagating retry). A lock we fail
        to release just expires on its TTL.
        """
        if not self.held:
            return
        try:
            _, release = _get_client()
            release(keys=[self.key], args=[self.token])
        except Exception:
            logger.warning("Failed to release lock %s — it will expire on its TTL", self.key)
        finally:
            self.held = False
