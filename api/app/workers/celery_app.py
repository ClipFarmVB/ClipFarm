import sys
from pathlib import Path

# Add project root to path so `ml.pipeline` is importable from api/
_project_root = str(Path(__file__).resolve().parents[3])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from celery import Celery
from app.config import settings

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
