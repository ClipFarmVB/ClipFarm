"""
Redis-backed per-game processing lock (CF-65a).

`process_game_task` must never run twice for the same game at once — Celery +
Redis is at-least-once (a task outliving the broker visibility timeout, or a
worker being killed, causes redelivery), and once there is more than one worker
that redelivery can land on a *second* worker while the first is still running.
This lock makes concurrent double-processing impossible; the idempotent clip
refresh (CF-37) separately handles sequential re-runs.

NOTE for CF-65b (prefork): `_client` and the Lua script are created at import
time. That is safe today only because tasks.py imports this module *lazily,
inside the task body* — so the client is created after the worker forks. If that
import is ever hoisted to module scope, forked children would inherit and share
one connection. Keep the import lazy, or move `_client` to a per-process factory.
"""
import logging
import uuid

import redis

from app.config import settings

logger = logging.getLogger(__name__)

_client = redis.Redis.from_url(settings.redis_url)

# Compare-and-delete: only release the lock if we still hold this exact token,
# so we never delete a lock that expired and was re-acquired by another worker.
_RELEASE = _client.register_script(
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('del', KEYS[1]) else return 0 end"
)


class GameLock:
    """Single-holder lock for one game_id, backed by Redis SET NX EX."""

    def __init__(self, game_id: uuid.UUID | str, ttl_seconds: int):
        self.key = f"lock:process_game:{game_id}"
        self.token = uuid.uuid4().hex
        self.ttl = ttl_seconds
        self.held = False

    def acquire(self) -> bool:
        """Try to take the lock. Returns False if another holder has it."""
        self.held = bool(_client.set(self.key, self.token, nx=True, ex=self.ttl))
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
            _RELEASE(keys=[self.key], args=[self.token])
        except Exception:
            logger.warning("Failed to release lock %s — it will expire on its TTL", self.key)
        finally:
            self.held = False
