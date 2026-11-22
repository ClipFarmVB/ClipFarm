"""CF-65b: prefork pool config + fork-safety of inherited process state.

Guarded with importorskip like the other api tests. Every target here must be in
api/requirements-dev.txt — test_dev_dependencies.py fails the suite otherwise
(CF-276), which is what stops a guard from skipping itself into a green run.
"""
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("celery")
pytest.importorskip("sqlalchemy")

_API_DIR = Path(__file__).resolve().parent.parent


def test_pool_settings_are_wired():
    """Non-default values, so a hard-coded conf that happens to equal the
    default cannot pass. conf is built at import time, hence the subprocess."""
    env = {
        **os.environ,
        "CELERY_WORKER_CONCURRENCY": "3",
        "CELERY_MAX_TASKS_PER_CHILD": "5",
    }
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.workers.celery_app import celery_app as a; "
            "print(a.conf.worker_concurrency, a.conf.worker_max_tasks_per_child)",
        ],
        cwd=_API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.split()[-2:] == ["3", "5"]


def test_recycling_zero_reaches_celery_as_none():
    """0 disables recycling; Celery spells that None, not 0."""
    env = {**os.environ, "CELERY_MAX_TASKS_PER_CHILD": "0"}
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.workers.celery_app import celery_app as a; "
            "print(a.conf.worker_max_tasks_per_child)",
        ],
        cwd=_API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.split()[-1] == "None"


def test_default_concurrency_is_conservative():
    """The declared default, not the live settings — those read the
    environment, and the compose worker sets CELERY_WORKER_CONCURRENCY=2."""
    from app.config import Settings

    assert Settings.model_fields["celery_worker_concurrency"].default == 1


@pytest.mark.parametrize(
    "var, value",
    [
        ("CELERY_WORKER_CONCURRENCY", "0"),
        ("CELERY_WORKER_CONCURRENCY", "-1"),
        ("CELERY_MAX_TASKS_PER_CHILD", "-1"),
        ("CELERY_CHILD_THREAD_LIMIT", "-1"),
    ],
)
def test_pool_settings_refuse_values_celery_would_misread(var, value, monkeypatch):
    """CELERY_WORKER_CONCURRENCY=0 makes Celery fork one child per CPU, with
    thread capping skipped — the most children on a box sized by memory."""
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_sync_engine_keeps_mains_connection_pool():
    """The sync engine's pool is the production connection pattern against
    Supabase, which a person owns. CF-65b leaves it as main has it: fork safety
    comes from the lazy import plus `_reset_db_pool`'s dispose(close=False)."""
    # Engine construction resolves the DBAPI, and importing _sync_db pulls in
    # app.database, which builds the *async* engine — so both drivers must exist.
    pytest.importorskip("psycopg2")
    pytest.importorskip("asyncpg")
    from sqlalchemy.pool import QueuePool

    from app.workers import _sync_db

    assert isinstance(_sync_db._engine.pool, QueuePool)


def test_reset_engine_drops_a_built_engine(monkeypatch):
    """Backstop only: the parent never builds `_engine` today (the NOTE in
    locks.py), so no child inherits one. If that changes, the child must drop
    it, and detach rather than close so the parent's connections survive."""
    from app.workers import locks

    disposed = {}

    class _FakeEngine:
        def dispose(self, close=True):
            disposed["close"] = close

    monkeypatch.setattr(locks, "_engine", _FakeEngine())

    locks.reset_engine()

    assert locks._engine is None, "the inherited engine must be dropped"
    assert disposed == {"close": False}


def test_reset_engine_is_a_noop_when_nothing_was_built(monkeypatch):
    """Every real path today — nothing to dispose."""
    from app.workers import locks

    monkeypatch.setattr(locks, "_engine", None)
    locks.reset_engine()
    assert locks._engine is None


# ── reset_after_fork wiring ──────────────────────────────────────────────────


def test_worker_process_init_runs_the_post_fork_hook(monkeypatch):
    """The receiver must actually be connected, not just defined."""
    from celery.signals import worker_process_init

    from app.workers import celery_app as ca

    # As a forked child sees it: the worker's pid is its parent's, not its own.
    monkeypatch.setattr(ca, "_worker_pid", os.getpid() + 1)
    calls = []
    monkeypatch.setattr(ca, "reset_after_fork", lambda **kw: calls.append(kw))

    worker_process_init.send(sender=None)

    assert len(calls) == 1, "worker_process_init must reach _init_pool_child"


def test_reset_after_fork_runs_every_reset(monkeypatch):
    from app.workers import forksafe

    calls = []
    for name in ("_reset_db_pool", "_reset_lock_engine", "_reset_storage_client"):
        monkeypatch.setattr(forksafe, name, lambda name=name: calls.append(name))
    monkeypatch.setattr(
        forksafe,
        "_limit_native_threads",
        lambda c, t: calls.append(("_limit_native_threads", c, t)),
    )

    forksafe.reset_after_fork(
        concurrency=3, thread_limit=2, init_monitoring=lambda: calls.append("monitoring")
    )

    assert calls == [
        "_reset_db_pool",
        "_reset_lock_engine",
        "_reset_storage_client",
        ("_limit_native_threads", 3, 2),
        "monitoring",
    ]


def test_a_monitoring_failure_does_not_stop_the_child(monkeypatch):
    """Reporting must never break processing."""
    from app.workers import forksafe

    for name in ("_reset_db_pool", "_reset_lock_engine", "_reset_storage_client"):
        monkeypatch.setattr(forksafe, name, lambda: None)
    monkeypatch.setattr(forksafe, "_limit_native_threads", lambda c, t: None)

    def _boom():
        raise RuntimeError("sentry down")

    forksafe.reset_after_fork(concurrency=1, thread_limit=0, init_monitoring=_boom)


def _fake_module(monkeypatch, name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def test_db_reset_detaches_an_imported_sync_engine(monkeypatch):
    from app.workers import forksafe

    disposed = []
    engine = types.SimpleNamespace(dispose=lambda close=True: disposed.append(close))
    _fake_module(monkeypatch, "app.workers._sync_db", _engine=engine)

    forksafe._reset_db_pool()

    assert disposed == [False]


def test_lock_reset_calls_reset_engine_when_imported(monkeypatch):
    from app.workers import forksafe

    called = []
    _fake_module(
        monkeypatch, "app.workers.locks", reset_engine=lambda: called.append(True)
    )

    forksafe._reset_lock_engine()

    assert called == [True]


def test_storage_reset_clears_the_cached_client_when_imported(monkeypatch):
    from app.workers import forksafe

    cleared = []
    client = types.SimpleNamespace(cache_clear=lambda: cleared.append(True))
    _fake_module(monkeypatch, "app.services.storage", _client=client)

    forksafe._reset_storage_client()

    assert cleared == [True]


@pytest.mark.parametrize(
    "module", ["app.workers._sync_db", "app.workers.locks", "app.services.storage"]
)
def test_resets_do_not_import_what_the_parent_never_imported(module, monkeypatch):
    """The resets exist for state the parent built. A module that was never
    imported built nothing, and importing it in every child at startup (again
    on every recycle) would only front-load its imports."""
    from app.workers import forksafe

    monkeypatch.delitem(sys.modules, module, raising=False)

    forksafe._reset_db_pool()
    forksafe._reset_lock_engine()
    forksafe._reset_storage_client()

    assert module not in sys.modules


# ── native thread capping ────────────────────────────────────────────────────

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

    thread_env.setattr(forksafe, "_available_cpus", lambda: 8)

    forksafe._limit_native_threads(concurrency=4, explicit_limit=0)
    for var in _THREAD_VARS:
        assert os.environ[var] == "2", f"{var} should be capped too"


def test_thread_limit_is_a_noop_at_concurrency_one(thread_env):
    """1-way keeps today's behaviour — the libraries use the whole box."""
    from app.workers import forksafe

    forksafe._limit_native_threads(concurrency=1, explicit_limit=0)
    assert not any(var in os.environ for var in _THREAD_VARS)


def test_explicit_thread_limit_wins(thread_env):
    from app.workers import forksafe

    thread_env.setattr(forksafe, "_available_cpus", lambda: 8)
    forksafe._limit_native_threads(concurrency=4, explicit_limit=3)
    assert os.environ["OMP_NUM_THREADS"] == "3"


def test_thread_capping_follows_a_cli_concurrency_override(thread_env):
    """Review #1: `--concurrency` reaches neither settings nor conf, so the
    child must read the value captured pre-fork or capping silently no-ops."""
    from app.workers import celery_app as ca

    thread_env.setattr(ca, "_effective_concurrency", 4)
    thread_env.setattr(ca, "_worker_pid", os.getpid() + 1)  # a forked child
    captured = {}
    thread_env.setattr(
        ca, "reset_after_fork", lambda **kw: captured.update(kw)
    )

    ca._init_pool_child()
    assert captured["concurrency"] == 4, "CLI override must reach the capping logic"


def test_celeryd_init_captures_the_cli_concurrency(thread_env):
    from app.workers import celery_app as ca

    thread_env.setattr(ca, "_effective_concurrency", None)
    thread_env.setattr(ca, "_worker_pid", -1)
    thread_env.setattr(ca, "init_sentry", lambda *_a, **_k: None)

    ca._init_worker_monitoring(options={"concurrency": 6})
    assert ca._effective_concurrency == 6
    assert ca._worker_pid == os.getpid(), "celeryd_init must record the worker pid"


def test_the_solo_pool_does_not_run_the_post_fork_resets(monkeypatch):
    """Celery 5.4's solo TaskPool sends worker_process_init in the worker
    process itself. Running the hook there would initialise Sentry a second
    time and apply child-only resets to the one production process."""
    from celery.concurrency.solo import TaskPool

    from app.workers import celery_app as ca

    monkeypatch.setattr(ca, "_worker_pid", -1)  # restored after the test
    monkeypatch.setattr(ca, "_effective_concurrency", None)
    monkeypatch.setattr(ca, "init_sentry", lambda *_a, **_k: None)
    calls = []
    monkeypatch.setattr(ca, "reset_after_fork", lambda **kw: calls.append(kw))

    ca._init_worker_monitoring(options={"concurrency": 1})  # celeryd_init
    TaskPool()

    assert calls == [], "the post-fork hook ran under --pool=solo"


def test_a_forked_child_runs_the_post_fork_resets(monkeypatch):
    """The other side of the solo guard: a real fork must still run the hook."""
    from celery.signals import worker_process_init

    from app.workers import celery_app as ca

    read_end, write_end = os.pipe()
    monkeypatch.setattr(ca, "_worker_pid", -1)  # restored after the test
    monkeypatch.setattr(ca, "_effective_concurrency", None)
    monkeypatch.setattr(ca, "init_sentry", lambda *_a, **_k: None)
    monkeypatch.setattr(
        ca, "reset_after_fork", lambda **kw: os.write(write_end, b"ran")
    )
    ca._init_worker_monitoring(options={"concurrency": 2})  # in the parent

    pid = os.fork()
    if pid == 0:  # child: never return into pytest
        try:
            worker_process_init.send(sender=None)
        finally:
            os._exit(0)
    os.close(write_end)
    os.waitpid(pid, 0)
    with os.fdopen(read_end, "rb") as f:
        assert f.read() == b"ran", "the post-fork hook did not run in a forked child"


# ── CPU budget: affinity and cgroup quota, not the host's core count ─────────


@pytest.fixture
def cgroup(tmp_path, monkeypatch):
    """Point forksafe's cgroup paths at files the test writes."""
    from app.workers import forksafe

    paths = {
        "v2": tmp_path / "cpu.max",
        "quota": tmp_path / "cpu.cfs_quota_us",
        "period": tmp_path / "cpu.cfs_period_us",
    }
    monkeypatch.setattr(forksafe, "_CGROUP_V2_CPU_MAX", str(paths["v2"]))
    monkeypatch.setattr(forksafe, "_CGROUP_V1_QUOTA", str(paths["quota"]))
    monkeypatch.setattr(forksafe, "_CGROUP_V1_PERIOD", str(paths["period"]))
    monkeypatch.setattr(forksafe.os, "sched_getaffinity", lambda _pid: set(range(16)), raising=False)
    monkeypatch.setattr(forksafe.os, "cpu_count", lambda: 16)
    return paths


def test_cpus_follow_a_v2_quota(cgroup):
    """The render.yaml trap: 16 visible cores, a fractional-CPU quota."""
    from app.workers import forksafe

    cgroup["v2"].write_text("50000 100000\n")
    assert forksafe._available_cpus() == 1

    cgroup["v2"].write_text("200000 100000\n")
    assert forksafe._available_cpus() == 2


def test_cpus_follow_a_v1_quota(cgroup):
    from app.workers import forksafe

    cgroup["quota"].write_text("300000\n")
    cgroup["period"].write_text("100000\n")
    assert forksafe._available_cpus() == 3


def test_a_v1_unlimited_quota_falls_back_to_affinity(cgroup):
    """v1 spells "no limit" as -1, which must not round down to a 1-CPU cap."""
    from app.workers import forksafe

    cgroup["quota"].write_text("-1\n")
    cgroup["period"].write_text("100000\n")
    assert forksafe._available_cpus() == 16


def test_a_fractional_quota_above_one_cpu_rounds_up(cgroup):
    from app.workers import forksafe

    cgroup["v2"].write_text("150000 100000\n")  # 1.5 CPUs
    assert forksafe._available_cpus() == 2


def test_an_unlimited_quota_falls_back_to_affinity(cgroup, monkeypatch):
    from app.workers import forksafe

    cgroup["v2"].write_text("max 100000\n")
    monkeypatch.setattr(forksafe.os, "sched_getaffinity", lambda _pid: {0, 1, 2, 3})
    assert forksafe._available_cpus() == 4


def test_no_cgroup_files_and_no_affinity_falls_back_to_cpu_count(cgroup, monkeypatch):
    from app.workers import forksafe

    monkeypatch.delattr(forksafe.os, "sched_getaffinity")
    assert forksafe._available_cpus() == 16


def test_auto_thread_cap_uses_the_quota_not_the_host(cgroup, thread_env):
    from app.workers import forksafe

    cgroup["v2"].write_text("100000 100000\n")  # 1 CPU on a 16-core host
    forksafe._limit_native_threads(concurrency=2, explicit_limit=0)
    assert os.environ["OMP_NUM_THREADS"] == "1"


# ── which pool each start path runs ──────────────────────────────────────────

_REPO_ROOT = _API_DIR.parent


@pytest.mark.parametrize(
    "value, pool",
    [(None, "solo"), ("1", "solo"), ("2", "prefork"), ("8", "prefork"), ("abc", "solo")],
)
def test_render_start_keeps_solo_until_concurrency_is_raised(value, pool, tmp_path):
    """Prefork at one child is not the solo pool (OOM-kill requeue with no
    attempt cap, child recycling, a resident parent), so N = 1 stays solo."""
    stub = tmp_path / "celery"
    stub.write_text('#!/bin/sh\necho "$@"\n')
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "CELERY_WORKER_CONCURRENCY"}
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    if value is not None:
        env["CELERY_WORKER_CONCURRENCY"] = value

    out = subprocess.run(
        ["sh", str(_REPO_ROOT / "scripts" / "render-start-worker.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.split()[-1] == f"--pool={pool}"


def _compose_worker(name):
    yaml = pytest.importorskip("yaml")

    class _Loader(yaml.SafeLoader):
        pass

    def _passthrough(loader, _suffix, node):
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node, deep=True)
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node, deep=True)
        return loader.construct_scalar(node)

    _Loader.add_multi_constructor("!", _passthrough)
    with open(_REPO_ROOT / name) as f:
        return yaml.load(f, Loader=_Loader)["services"]["worker"]


def test_dev_compose_actually_forks():
    """Solo ignores CELERY_WORKER_CONCURRENCY and forks nothing, so the
    post-fork hook returns early and a solo dev stack exercises none of
    forksafe."""
    worker = _compose_worker("docker-compose.yml")
    assert "--pool=prefork" in worker["command"]
    assert int(worker["environment"]["CELERY_WORKER_CONCURRENCY"]) > 1


def test_vps_overlay_keeps_the_solo_pool():
    """The VPS layers docker-compose.prod.yml over the dev file, so the dev
    worker's prefork setting must not reach it by inheritance."""
    worker = _compose_worker("docker-compose.prod.yml")
    assert "--pool=solo" in worker["command"]
    assert worker["environment"]["CELERY_WORKER_CONCURRENCY"] == "1"


def test_repro_overlay_keeps_the_solo_pool():
    """docker-compose.repro.yml (CF-241) must die the way production did. Under
    the base file's prefork pool an OOM kills one child and its game is requeued
    at once, so the repro would loop instead of failing."""
    worker = _compose_worker("docker-compose.repro.yml")
    assert "--pool=solo" in worker["command"]
    assert worker["environment"]["CELERY_WORKER_CONCURRENCY"] == "1"
