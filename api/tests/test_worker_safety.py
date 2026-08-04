"""CF-65a: worker redelivery/concurrency safety — broker config + per-game lock.

The `importorskip` guard is now belt-and-braces: the api CI job installs
api/requirements-dev.txt (including fakeredis), so these execute in CI as well as
locally. Run from the api/ dir: `cd api && pytest tests/test_worker_safety.py`.
"""
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


def test_lock_ttl_exceeds_visibility_timeout():
    """The lock must outlive the visibility timeout, or a redelivery during an
    over-running task acquires it and runs concurrently (CF-65a review #2)."""
    from app.config import settings

    assert settings.process_lock_ttl_seconds > settings.celery_visibility_timeout


def _fake_redis_or_skip():
    """A FakeStrictRedis with Lua support, or skip (release() uses a Lua CAS)."""
    fakeredis = pytest.importorskip("fakeredis")
    fake = fakeredis.FakeStrictRedis()
    try:
        fake.eval("return 1", 0)
    except Exception:
        pytest.skip("fakeredis without Lua support (install fakeredis[lua])")
    return fake


def _patch_lock_client(monkeypatch, fake):
    pytest.importorskip("redis")
    from app.workers import locks

    monkeypatch.setattr(locks, "_client", fake)
    monkeypatch.setattr(
        locks,
        "_RELEASE",
        fake.register_script(
            "if redis.call('get', KEYS[1]) == ARGV[1] "
            "then return redis.call('del', KEYS[1]) else return 0 end"
        ),
    )
    return locks


def test_lock_is_mutually_exclusive(monkeypatch):
    fake = _fake_redis_or_skip()
    locks = _patch_lock_client(monkeypatch, fake)

    a = locks.GameLock("game-1", ttl_seconds=60)
    b = locks.GameLock("game-1", ttl_seconds=60)
    assert a.acquire() is True
    assert b.acquire() is False        # a holds it
    a.release()
    assert b.acquire() is True         # freed


def test_release_is_token_scoped(monkeypatch):
    """A stale holder must not delete a lock another worker re-acquired."""
    fake = _fake_redis_or_skip()
    locks = _patch_lock_client(monkeypatch, fake)

    a = locks.GameLock("game-2", ttl_seconds=60)
    assert a.acquire()
    fake.delete(a.key)                 # simulate a's lock expiring
    b = locks.GameLock("game-2", ttl_seconds=60)
    assert b.acquire()                 # b now owns it
    a.release()                        # a's stale release must be a no-op
    assert fake.get(b.key) == b.token.encode()
