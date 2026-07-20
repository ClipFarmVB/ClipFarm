import csv
import io
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.database import get_db
from app.models.correction import Correction
from app.schemas.correction import CorrectionOut

router = APIRouter(prefix="/corrections", tags=["corrections"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]

# Column order for the CSV export.
_CSV_COLUMNS = [
    "id",
    "clip_id",
    "user_id",
    "original_action",
    "corrected_label_1",
    "corrected_label_2",
    "original_confidence",
    "start_time",
    "end_time",
    "created_at",
]


async def _list_corrections(
    user_id: uuid.UUID, db: AsyncSession, limit: int | None
) -> list[Correction]:
    """Fetch the caller's corrections, newest first."""
    stmt = (
        select(Correction)
        .where(Correction.user_id == user_id)
        .order_by(Correction.created_at.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("", response_model=list[CorrectionOut])
async def list_corrections(
    user_id: UserId,
    db: DB,
    limit: int | None = Query(None, ge=1, le=10000),
):
    """Read the caller's saved label corrections (relabel events kept as ML
    training signal). Scoped to the requesting user, newest first."""
    return await _list_corrections(user_id, db, limit)


@router.get("/export")
async def export_corrections(user_id: UserId, db: DB):
    """Export the caller's corrections as a CSV download (training data)."""
    corrections = await _list_corrections(user_id, db, None)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_COLUMNS)
    writer.writeheader()
    for c in corrections:
        writer.writerow(
            {
                "id": c.id,
                "clip_id": c.clip_id,
                "user_id": c.user_id,
                "original_action": c.original_action.value,
                "corrected_label_1": c.corrected_label_1,
                "corrected_label_2": c.corrected_label_2 or "",
                "original_confidence": c.original_confidence,
                "start_time": c.start_time,
                "end_time": c.end_time,
                "created_at": c.created_at.isoformat(),
            }
        )

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=corrections.csv"},
    )
