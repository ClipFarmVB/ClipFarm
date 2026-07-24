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
    worker_prefetch_multiplier=1,  # Process one job at a time (GPU workloads)
    broker_connection_retry_on_startup=True,
)


@celeryd_init.connect
def _init_worker_monitoring(**_kwargs):
    """Initialize Sentry when a worker boots. Using the signal (not module
    import) keeps the worker's CeleryIntegration out of the api process, which
    imports celery_app only to enqueue tasks."""
    init_sentry("worker")


@celery_app.task(name="debug.trigger_error")
def debug_trigger_error():
    """Deliberately raises to verify worker exceptions reach Sentry.
    Trigger with: celery_app.send_task("debug.trigger_error")."""
    raise RuntimeError("CF-89 test error from the Celery worker (debug only)")
