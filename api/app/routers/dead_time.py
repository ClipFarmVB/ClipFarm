import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.config import settings
from app.database import get_db
from app.models.dead_time import DeadTimeClip, DeadTimeRun, DeadTimeRunStatus
from app.schemas.dead_time import DeadTimeClipOut, DeadTimeRunOut
from app.services import storage
from app.workers.tasks import process_dead_time_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/deadtime", tags=["deadtime"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]


def _rewrite_urls(clip: DeadTimeClip) -> dict[str, str | None]:
    return {
        "clip_url": storage.presign_from_stored_url(clip.clip_url, expires_in=3600),
        "thumbnail_url": (
            storage.presign_from_stored_url(clip.thumbnail_url, expires_in=3600)
            if clip.thumbnail_url
            else None
        ),
    }


@router.get("/runs", response_model=list[DeadTimeRunOut])
async def list_dead_time_runs(user_id: UserId, db: DB):
    result = await db.execute(
        select(DeadTimeRun)
        .where(DeadTimeRun.owner_id == user_id)
        .order_by(DeadTimeRun.created_at.desc())
    )
    runs = result.scalars().all()

    clip_counts_q = await db.execute(
        select(DeadTimeClip.run_id, func.count(DeadTimeClip.id).label("n"))
        .where(DeadTimeClip.run_id.in_([r.id for r in runs]))
        .group_by(DeadTimeClip.run_id)
    )
    counts = {row.run_id: row.n for row in clip_counts_q}

    out = []
    for run in runs:
        d = DeadTimeRunOut.model_validate(run)
        d.clip_count = counts.get(run.id, 0)
        out.append(d)
    return out


@router.get("/runs/{run_id}", response_model=DeadTimeRunOut)
async def get_dead_time_run(run_id: uuid.UUID, user_id: UserId, db: DB):
    run = await db.get(DeadTimeRun, run_id)
    if not run or run.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Dead-time run not found")

    clip_count_q = await db.execute(
        select(func.count(DeadTimeClip.id)).where(DeadTimeClip.run_id == run_id)
    )
    count = clip_count_q.scalar_one()

    out = DeadTimeRunOut.model_validate(run)
    out.clip_count = count
    return out


@router.post("/runs", response_model=DeadTimeRunOut, status_code=status.HTTP_201_CREATED)
async def create_dead_time_run(
    user_id: UserId,
    file: Annotated[UploadFile, File(description="Game video file")],
    title: Annotated[str, Form(max_length=255)],
    db: DB,
):
    content_type = (file.content_type or "").lower()
    if content_type not in settings.allowed_content_types_set:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type. Allowed: {sorted(settings.allowed_content_types_set)}",
        )

    if file.size is not None and file.size > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max {settings.max_upload_bytes // (1024 * 1024)} MB",
        )

    safe_filename = (file.filename or "upload.mp4").replace("/", "_").replace("\\", "_").replace("..", "_")

    run_id = uuid.uuid4()
    key = storage.dead_time_raw_key(run_id, safe_filename)

    try:
        limited = storage.LimitedReader(file.file, settings.max_upload_bytes)
        raw_url = storage.upload_fileobj(limited, key, content_type=content_type)
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except Exception:
        logger.exception("Storage upload failed for dead-time run %s", run_id)
        raise HTTPException(status_code=500, detail="Storage upload failed")

    run = DeadTimeRun(
        id=run_id,
        owner_id=user_id,
        title=title,
        status=DeadTimeRunStatus.queued,
        raw_video_url=raw_url,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    process_dead_time_task.delay(str(run_id), raw_url)

    return DeadTimeRunOut.model_validate(run)


@router.get("/runs/{run_id}/clips", response_model=list[DeadTimeClipOut])
async def list_dead_time_clips(
    run_id: uuid.UUID,
    db: DB,
    user_id: UserId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
):
    run = await db.get(DeadTimeRun, run_id)
    if not run or run.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Dead-time run not found")

    q = (
        select(DeadTimeClip)
        .where(DeadTimeClip.run_id == run_id)
        .order_by(DeadTimeClip.start_time)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(q)
    clips = result.scalars().all()

    out = []
    for clip in clips:
        d = DeadTimeClipOut.model_validate(clip)
        urls = _rewrite_urls(clip)
        d.clip_url = urls["clip_url"]  # type: ignore[assignment]
        d.thumbnail_url = urls["thumbnail_url"]
        out.append(d)
    return out
