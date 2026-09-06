"""CF-65b: prefork pool config + fork-safety of inherited process state.

Guarded with importorskip like the other api tests. Every target here must be in
api/requirements-dev.txt — test_dev_dependencies.py fails the suite otherwise
(CF-276), which is what stops a guard from skipping itself into a green run.
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


def test_lock_engine_is_rebuilt_after_a_fork(monkeypatch):
    """The lock engine must not be reused across a fork.

    Since CF-184 the per-game lock is a *session-scoped Postgres advisory lock*,
    held by one connection. A prefork child inheriting the parent's engine would
    share that socket — so a child could release, or appear to hold, the parent's
    lock. This is sharper than the Redis TTL lock this test originally covered,
    where sharing a client only risked interleaved bytes.
    """
    from app.workers import locks

    disposed = {}

    class _FakeEngine:
        def dispose(self, close=True):
            disposed["close"] = close

    monkeypatch.setattr(locks, "_engine", _FakeEngine())

    locks.reset_engine()

    assert locks._engine is None, "the inherited engine must be dropped"
    assert disposed == {"close": False}, (
        "dispose(close=False): closing would take the parent's live lock "
        "connection down with it"
    )


def test_reset_engine_is_a_noop_when_nothing_was_built(monkeypatch):
    """Solo pool, or a child that has not acquired yet — nothing to dispose."""
    from app.workers import locks

    monkeypatch.setattr(locks, "_engine", None)
    locks.reset_engine()
    assert locks._engine is None


def test_the_post_fork_hook_resets_the_lock_engine(monkeypatch):
    """The wiring, not just the reset: forksafe must actually call it."""
    from app.workers import forksafe, locks

    called = []
    monkeypatch.setattr(locks, "reset_engine", lambda: called.append(True))

    forksafe._reset_lock_engine()

    assert called == [True]


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
