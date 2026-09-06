"""
Per-child process setup for the prefork worker pool (CF-65b).

`--pool=prefork` forks the worker parent into N children *after* the parent has
imported the application. Anything the parent created at import time is inherited
by every child, and three kinds of inherited state are actively unsafe to share:

* **Sockets** — a DB connection used from two processes interleaves bytes on
  one TCP stream. Symptoms are not clean errors; they are responses arriving on
  the wrong side. The per-game lock connection is the sharp case: it *is* the
  lock (CF-184), so sharing it across a fork corrupts mutual exclusion itself.
* **Threads** — `fork()` clones only the calling thread. A background thread
  started before the fork (Sentry's event transport) simply does not exist in the
  child, so anything it was responsible for silently stops happening.
* **Thread pools** — torch and OpenCV size their pools to the whole machine, so
  N children each spawn machine-wide pools and oversubscribe the CPU N-fold.

`reset_after_fork()` runs in each child via Celery's `worker_process_init`
signal and puts all three back into a known state. It is a no-op for the solo
pool (no fork happens), so the same code path is correct at any concurrency.
"""
import logging
import os
import sys

logger = logging.getLogger(__name__)


def _reset_db_pool() -> None:
    """Drop connections inherited from the parent.

    `_sync_db` uses NullPool, so there is normally nothing pooled to inherit —
    this is belt-and-braces in case the pool class is ever changed back. dispose()
    detaches without closing the parent's sockets, which is what we want: closing
    them would take the connection out from under the parent too.
    """
    try:
        from app.workers._sync_db import _engine

        _engine.dispose(close=False)
    except Exception:
        logger.exception("post-fork: failed to dispose the sync DB engine")


def _reset_lock_engine() -> None:
    """Force the per-game advisory lock's engine to be rebuilt in this process.

    Since CF-184 the lock is a session-scoped Postgres advisory lock, held by a
    specific connection rather than by a Redis key with a TTL. That makes the
    inherited-socket problem sharper, not milder: two processes on one
    connection can release or appear to hold each other's lock. `reset_engine`
    disposes without closing, leaving the parent's own connection intact.
    """
    try:
        from app.workers import locks

        locks.reset_engine()
    except Exception:
        logger.exception("post-fork: failed to reset the lock engine")


def _limit_native_threads(concurrency: int, explicit_limit: int) -> None:
    """Stop N children from each grabbing the whole CPU.

    Set before torch/cv2 are imported in the child where possible; the env vars
    are read at import time by the BLAS backends, and cv2's setter works after.
    """
    if explicit_limit > 0:
        threads = explicit_limit
    elif concurrency > 1:
        threads = max(1, (os.cpu_count() or 1) // concurrency)
    else:
        return  # solo/1-way: let the libraries use the machine as before

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
    # every child at startup — repeated on each max_tasks_per_child recycle —
    # when `tasks.py` otherwise keeps that import lazy until the first pose job.
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
    _limit_native_threads(concurrency, thread_limit)
    # Sentry's transport runs on a background thread, which fork() does not
    # clone — without re-initialising here, a child's captured events would be
    # queued to a worker that no longer exists and never leave the process.
    init_monitoring()
