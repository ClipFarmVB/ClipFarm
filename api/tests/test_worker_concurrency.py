"""CF-65b: prefork pool config + fork-safety of inherited process state.

Guarded with importorskip like the other api tests — the api CI job installs only
ruff/mypy/pytest today (CF-102/#113 wires the rest).
"""
import os

import pytest

pytest.importorskip("celery")
pytest.importorskip("sqlalchemy")


def test_pool_settings_are_wired():
    from app.workers.celery_app import celery_app
    from app.config import settings

    conf = celery_app.conf
    assert conf.worker_concurrency == settings.celery_worker_concurrency
    # 0 disables recycling; anything else must reach Celery as a positive int.
    if settings.celery_max_tasks_per_child:
        assert conf.worker_max_tasks_per_child == settings.celery_max_tasks_per_child
    else:
        assert conf.worker_max_tasks_per_child is None


def test_default_concurrency_is_conservative():
    """Default must match the old solo-pool throughput, so merging this PR
    changes no deployed behaviour until someone raises it deliberately."""
    from app.config import settings

    assert settings.celery_worker_concurrency == 1


def test_sync_engine_uses_nullpool():
    """A pooled connection created pre-fork would be shared by every child."""
    # Engine construction resolves the DBAPI, and importing _sync_db pulls in
    # app.database, which builds the *async* engine — so both drivers must exist.
    pytest.importorskip("psycopg2")
    pytest.importorskip("asyncpg")
    from sqlalchemy.pool import NullPool

    from app.workers import _sync_db

    assert isinstance(_sync_db._engine.pool, NullPool)


def test_lock_client_is_rebuilt_in_a_new_process(monkeypatch):
    """The lock client must not be reused across a fork.

    Simulated by changing the observed pid: a client cached under a different pid
    is discarded and rebuilt rather than shared.
    """
    pytest.importorskip("redis")
    fakeredis = pytest.importorskip("fakeredis")
    from app.workers import locks

    built = []

    def _fake_from_url(*_a, **_kw):
        client = fakeredis.FakeStrictRedis()
        built.append(client)
        return client

    monkeypatch.setattr(locks.redis.Redis, "from_url", staticmethod(_fake_from_url))
    locks.reset_client()

    first, _ = locks._get_client()
    assert locks._get_client()[0] is first, "same process should reuse its client"

    forked_pid = os.getpid() + 1  # resolve now — a lambda calling os.getpid()
    monkeypatch.setattr(locks.os, "getpid", lambda: forked_pid)  # would recurse
    second, _ = locks._get_client()

    assert second is not first, "a forked child must not inherit the parent's client"
    assert len(built) == 2


def test_lock_still_works_through_the_factory(monkeypatch):
    """Behaviour from CF-65a must be unchanged by the per-process indirection."""
    fakeredis = pytest.importorskip("fakeredis")
    from app.workers import locks

    fake = fakeredis.FakeStrictRedis()
    try:
        fake.eval("return 1", 0)
    except Exception:
        pytest.skip("fakeredis without Lua support (install fakeredis[lua])")

    monkeypatch.setattr(locks.redis.Redis, "from_url", staticmethod(lambda *a, **k: fake))
    locks.reset_client()

    a = locks.GameLock("game-cf65b", ttl_seconds=60)
    b = locks.GameLock("game-cf65b", ttl_seconds=60)
    assert a.acquire() is True
    assert b.acquire() is False
    a.release()
    assert b.acquire() is True


_THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


@pytest.fixture
def thread_env(monkeypatch):
    """Contain `_limit_native_threads`'s env writes.

    It assigns `os.environ` directly, which monkeypatch can't track — so without
    registering an undo for every variable it sets, values leak into the rest of
    the session and anything importing torch or cv2 later silently inherits them.
    `delenv(raising=False)` registers that undo even when the var is unset.
    """
    for var in _THREAD_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_thread_limit_divides_cpus_across_children(thread_env):
    from app.workers import forksafe

    thread_env.setattr(forksafe.os, "cpu_count", lambda: 8)

    forksafe._limit_native_threads(concurrency=4, explicit_limit=0)
    for var in _THREAD_VARS:
        assert os.environ[var] == "2", f"{var} should be capped too"


def test_thread_limit_is_a_noop_at_concurrency_one(thread_env):
    """Solo/1-way keeps today's behaviour — the libraries use the whole box."""
    from app.workers import forksafe

    forksafe._limit_native_threads(concurrency=1, explicit_limit=0)
    assert not any(var in os.environ for var in _THREAD_VARS)


def test_explicit_thread_limit_wins(thread_env):
    from app.workers import forksafe

    thread_env.setattr(forksafe.os, "cpu_count", lambda: 8)
    forksafe._limit_native_threads(concurrency=4, explicit_limit=3)
    assert os.environ["OMP_NUM_THREADS"] == "3"


def test_thread_capping_follows_a_cli_concurrency_override(thread_env):
    """Review #1: `--concurrency` reaches neither settings nor conf, so the
    child must read the value captured pre-fork or capping silently no-ops."""
    from app.workers import celery_app as ca

    thread_env.setattr(ca, "_effective_concurrency", 4)
    captured = {}
    thread_env.setattr(
        ca, "reset_after_fork", lambda **kw: captured.update(kw)
    )

    ca._init_pool_child()
    assert captured["concurrency"] == 4, "CLI override must reach the capping logic"


def test_celeryd_init_captures_the_cli_concurrency(thread_env):
    from app.workers import celery_app as ca

    thread_env.setattr(ca, "_effective_concurrency", None)
    thread_env.setattr(ca, "init_sentry", lambda *_a, **_k: None)

    ca._init_worker_monitoring(options={"concurrency": 6})
    assert ca._effective_concurrency == 6
