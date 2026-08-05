import csv
import io
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
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

# Leading characters that spreadsheet apps (Excel / Sheets) interpret as the
# start of a formula. Cells beginning with these are prefixed with a quote.
_CSV_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Neutralize CSV formula injection in free-text cells. A value starting
    with =, +, -, @ (or a leading tab/CR) is treated as a formula when the file
    is opened in a spreadsheet; prefixing with ' forces it to a literal string."""
    if value and value[0] in _CSV_INJECTION_PREFIXES:
        return "'" + value
    return value


async def _list_corrections(
    user_id: uuid.UUID,
    db: AsyncSession,
    limit: int | None,
    offset: int = 0,
) -> list[Correction]:
    """Fetch the caller's corrections, newest first."""
    stmt = (
        select(Correction)
        .where(Correction.user_id == user_id)
        .order_by(Correction.created_at.desc())
    )
    if offset:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _iter_csv(corrections: list[Correction]) -> Iterator[str]:
    """Serialize corrections to CSV incrementally (header, then one row at a
    time) so the response is streamed rather than buffered whole."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def _drain() -> str:
        chunk = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return chunk

    writer.writerow(_CSV_COLUMNS)
    yield _drain()
    for c in corrections:
        writer.writerow(
            [
                c.id,
                c.clip_id,
                c.user_id,
                c.original_action.value,
                _csv_safe(c.corrected_label_1),
                _csv_safe(c.corrected_label_2 or ""),
                c.original_confidence,
                c.start_time,
                c.end_time,
                c.created_at.isoformat(),
            ]
        )
        yield _drain()


@router.get("", response_model=list[CorrectionOut])
async def list_corrections(
    user_id: UserId,
    db: DB,
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
):
    """Read the caller's saved label corrections (relabel events kept as ML
    training signal). Scoped to the requesting user, newest first. Paginated —
    defaults to 100 per page; use offset to page through more."""
    return await _list_corrections(user_id, db, limit, offset)


@router.get("/export")
async def export_corrections(user_id: UserId, db: DB):
    """Export all of the caller's corrections as a streamed CSV download."""
    corrections = await _list_corrections(user_id, db, limit=None)
    filename = f"corrections-{datetime.now(timezone.utc):%Y-%m-%d}.csv"
    return StreamingResponse(
        _iter_csv(corrections),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
