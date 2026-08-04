import sys
from pathlib import Path

# Add project root to path so `ml.pipeline` is importable from api/
_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from celery import Celery
from celery.signals import celeryd_init
from app.config import settings
from app.observability import init_sentry

celery_app = Celery(
    "clipfarm",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # One reserved task per worker process — fair distribution once there are
    # multiple workers; actual concurrency is set by the pool, not this (CF-65b).
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    # ── CF-65a: redelivery safety ─────────────────────────────────────────────
    # Ack a task only after it finishes, and requeue it if the worker is killed
    # mid-task. Combined with the per-game lock (app/workers/locks.py) and the
    # idempotent clip refresh (CF-37), a redelivered or duplicated task is safe.
    #
    # Caveat (#149): on a *hard* kill the dead worker's per-game lock outlives
    # the visibility timeout (lock TTL > visibility_timeout by design), so the
    # requeued copy hits a still-held lock and no-ops — the game stays
    # "processing" until a stale-processing reaper clears it. So this
    # requeue-on-kill only fully recovers a hard-killed job once #149 lands,
    # which matters on Render (#98) where every deploy hard-kills in-flight jobs.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Redis default is 3600s: a task longer than that is redelivered while it's
    # still running (CF-45). Raise it above worst-case task time.
    broker_transport_options={"visibility_timeout": settings.celery_visibility_timeout},
    # ── CF-150: don't store task results ──────────────────────────────────────
    # Nothing reads them: progress and terminal state are polled from Postgres
    # (Game.status), and there is no AsyncResult/.get() anywhere in api/. Storing
    # them is pure write amplification — and in production the broker and result
    # backend are the SAME Render Key Value instance (#98), so unread results
    # accumulate in the store whose fullness `noeviction` is meant to surface
    # loudly. Dropping them removes that failure mode instead of managing it.
    #
    # Independent of the CF-65a settings above: acks_late and the retry path
    # re-publish to the BROKER and never touch the result backend, so redelivery
    # safety is unaffected. (task_track_started is inert while this is on; kept
    # so re-enabling results restores the previous behaviour.)
    task_ignore_result=True,
)


@celeryd_init.connect
def _init_worker_monitoring(**_kwargs):
    """Initialize Sentry when a worker boots. Using the signal (not module
    import) keeps the worker's CeleryIntegration out of the api process, which
    imports celery_app only to enqueue tasks."""
    init_sentry("worker")


if settings.debug:

    @celery_app.task(name="debug.trigger_error")
    def debug_trigger_error():
        """Deliberately raises to verify worker exceptions reach Sentry.
        Registered only when debug is enabled (mirrors /debug/sentry-error).
        Trigger with: celery_app.send_task("debug.trigger_error")."""
        raise RuntimeError("CF-89 test error from the Celery worker (debug only)")
