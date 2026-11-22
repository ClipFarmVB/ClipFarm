"""
Per-child process setup for the prefork worker pool (CF-65b).

`--pool=prefork` forks the worker parent into N children *after* the parent has
imported the application. Anything the parent had already built is inherited by
every child. Three kinds of inherited state would be unsafe to share:

* **Sockets** — a DB connection or HTTP pool used from two processes interleaves
  bytes on one TCP stream. Symptoms are not clean errors; they are responses
  arriving on the wrong side. Today the parent builds none of these: the lock
  engine, the sync DB engine and the storage client are all created lazily inside
  task bodies (see the NOTE in `locks.py`), so a child starts with nothing
  inherited. The resets below are a backstop for the day something in the parent
  builds one early — each is a no-op unless the owning module is already
  imported in this process.
* **Threads** — `fork()` clones only the calling thread. Sentry's transport runs
  on a background thread, but the pinned sentry-sdk notices the pid change and
  restarts it on first use, so events are not lost without help. Re-initialising
  Sentry per child is still done, so each child's client is its own rather than
  a copy of the parent's.
* **Thread pools** — OpenCV and the BLAS backends size their pools to the whole
  machine, so N children each spawn machine-wide pools and oversubscribe the CPU
  N-fold. This is the one reset that does real work on every fork.

`reset_after_fork()` runs in each child via Celery's `worker_process_init`
signal. That signal never fires under `--pool=solo`, where no fork happens.
"""
import logging
import math
import os
import sys

logger = logging.getLogger(__name__)

# cgroup files that bound the CPU a container may use. v2 first (Render and
# current Docker), then v1. Module-level so tests can point them elsewhere.
_CGROUP_V2_CPU_MAX = "/sys/fs/cgroup/cpu.max"
_CGROUP_V1_QUOTA = "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"
_CGROUP_V1_PERIOD = "/sys/fs/cgroup/cpu/cpu.cfs_period_us"


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _cgroup_cpu_limit() -> int | None:
    """CPUs the cgroup quota allows, rounded up; None when there is no quota."""
    quota: float | None = None
    period: float | None = None
    v2 = _read(_CGROUP_V2_CPU_MAX)
    if v2:
        parts = v2.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                quota, period = float(parts[0]), float(parts[1])
            except ValueError:
                quota = period = None
    else:
        q, p = _read(_CGROUP_V1_QUOTA), _read(_CGROUP_V1_PERIOD)
        if q and p:
            try:
                quota, period = float(q), float(p)
            except ValueError:
                quota = period = None
    if quota is None or period is None or quota <= 0 or period <= 0:
        return None  # unlimited (v2 "max", v1 -1) or unreadable
    return max(1, math.ceil(quota / period))


def _available_cpus() -> int:
    """CPUs this process may actually use, not the host's core count.

    `os.cpu_count()` on 3.11 reports the host's cores and ignores both CPU
    affinity and the container's cgroup quota — the same trap render.yaml
    documents for ffmpeg (16 visible cores under a 0.5-CPU quota). Take the
    smallest of the three, never below 1.
    """
    try:
        count = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        count = os.cpu_count() or 1
    limit = _cgroup_cpu_limit()
    if limit is not None:
        count = min(count, limit)
    return max(1, count)


def _reset_db_pool() -> None:
    """Drop sync-DB connections inherited from the parent, if it built any.

    `_sync_db` is imported lazily inside task bodies, so in a fresh child it is
    normally not imported at all and there is nothing to do — importing it here
    would only pull in `app.database` and the models to dispose an engine that
    was never used. It also uses NullPool, so even when imported nothing is
    pooled. dispose(close=False) detaches without closing the parent's sockets:
    closing them would take the connection out from under the parent too.
    """
    module = sys.modules.get("app.workers._sync_db")
    if module is None:
        return
    try:
        module._engine.dispose(close=False)
    except Exception:
        logger.exception("post-fork: failed to dispose the sync DB engine")


def _reset_lock_engine() -> None:
    """Backstop: drop a lock engine the parent built, if it ever builds one.

    On every current path this does nothing. The parent never takes a game lock,
    and `tasks.py` imports `locks` inside the task body, so `locks._engine` is
    still None at fork (the NOTE in `locks.py`). The engine is NullPool, and the
    connection that holds a lock is `GameLock._conn`, which only a task creates —
    so there is no parent lock a child could share. This only matters if
    something in the parent starts building the engine before the fork.
    """
    module = sys.modules.get("app.workers.locks")
    if module is None:
        return
    try:
        module.reset_engine()
    except Exception:
        logger.exception("post-fork: failed to reset the lock engine")


def _reset_storage_client() -> None:
    """Backstop: drop a boto3 client the parent built (see `storage._client`).

    botocore clients are not fork-safe — an inherited one shares its connection
    pool across processes. The parent only builds one if something presigns or
    uploads before the fork, which nothing does today; clearing the cache makes
    the child build its own on first use either way.
    """
    module = sys.modules.get("app.services.storage")
    if module is None:
        return
    try:
        module._client.cache_clear()
    except Exception:
        logger.exception("post-fork: failed to clear the storage client")


def _limit_native_threads(concurrency: int, explicit_limit: int) -> None:
    """Stop N children from each grabbing the whole CPU.

    Set before the BLAS backends are imported in the child where possible; the
    env vars are read at import time, and cv2's setter works after.
    """
    if explicit_limit > 0:
        threads = explicit_limit
    elif concurrency > 1:
        threads = max(1, _available_cpus() // concurrency)
    else:
        return  # 1-way: let the libraries use the machine as before

    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[var] = str(threads)
    try:
        import cv2

        cv2.setNumThreads(threads)
    except Exception:
        pass  # cv2 not importable here is fine — the env vars still apply
    # Only adjust torch if something already imported it. The env vars above are
    # read by torch at import time and do the same job, so importing it here just
    # to call the setter would front-load a multi-second import and its RSS into
    # every child at startup — repeated on each max_tasks_per_child recycle.
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            torch.set_num_threads(threads)
        except Exception:
            pass
    logger.info("post-fork: capped native thread pools at %d", threads)


def reset_after_fork(concurrency: int, thread_limit: int, init_monitoring) -> None:
    """Re-establish per-process state in a freshly forked pool child."""
    _reset_db_pool()
    _reset_lock_engine()
    _reset_storage_client()
    _limit_native_threads(concurrency, thread_limit)
    # The pinned sentry-sdk restarts its transport thread after a fork on its
    # own (BackgroundWorker checks the pid), so this is not what keeps events
    # flowing. It gives each child its own client instead of the parent's copy.
    # Wrapped: monitoring setup must never stop a child from taking work.
    try:
        init_monitoring()
    except Exception:
        logger.exception("post-fork: failed to re-initialise monitoring")
