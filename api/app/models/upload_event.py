import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UploadEvent(Base):
    """One row per accepted upload — the record the quota is counted from (CF-91).

    Deliberately **not** derived from `games`. Consumption has to be counted
    from something the user cannot retract: `DELETE /games/{id}` is a hard
    delete, so counting live game rows made the quota refundable — upload to
    the cap, delete, repeat, with the GPU spend already incurred.

    So rows are never removed by the app: inserted once, and only their
    `charged_seconds` updated afterwards (settlement, below). `game_id` is
    `ON DELETE SET NULL` rather than cascade: deleting a game detaches its
    accounting row, it never removes it. Nothing in the app deletes from this
    table — `app.services.quota` deliberately has no release or refund helper —
    so a row leaves only through the `owner_id` `ON DELETE CASCADE`, when the
    user row itself is deleted.

    `charged_seconds` is what this upload costs the minute quota. It starts as
    the client's declared duration, unless that is missing or shorter than the
    file's verified size physically allows; then it is an estimate derived from
    the size, capped at the per-video maximum (or the maximum outright when
    there is no size to go on). So omitting the field is not a free pass — see
    `quota.charge_for`. The worker settles it to the probed truth once it has
    the file.
    """

    __tablename__ = "upload_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("games.id", ondelete="SET NULL"), nullable=True
    )
    charged_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    # The quota window query is always "this owner, since this instant".
    __table_args__ = (
        Index("ix_upload_events_owner_created", "owner_id", "created_at"),
    )
