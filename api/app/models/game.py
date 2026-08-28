import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, Float, String, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.visibility import Visibility


class GameStatus(str, enum.Enum):
    # The row exists but the video hasn't landed in R2 yet: the browser is
    # PUTting it directly (CF-163). Nothing is queued until the object is
    # confirmed, so an upload that fails or is abandoned never becomes work.
    uploading = "uploading"
    queued = "queued"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class Game(Base):
    __tablename__ = "games"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[GameStatus] = mapped_column(
        SAEnum(GameStatus), default=GameStatus.queued, nullable=False
    )
    raw_video_url: Mapped[str | None] = mapped_column(String(2048))
    # Text, not String(1024): the value is `str(exc)` from an arbitrary failure,
    # and a Modal remote traceback reliably exceeds any width worth naming. The
    # overflow does not just lose the message — `sync_set_game_status(…,
    # "failed", …)` runs from inside the task's own `except` handler, so a
    # DataError raised there costs the `failed` write and the retry decision and
    # strands the row in `processing` (CF-226, the same overflow as `upload_id`
    # below, reached from the code that exists to explain a failure).
    error_message: Mapped[str | None] = mapped_column(Text)
    # S3 multipart upload id, held only while status == uploading. Stored so a
    # delete (or the abandoned-upload sweep) can abort the upload and stop
    # paying for its parts, rather than waiting on a lifecycle rule.
    #
    # Text, not String(n): this was varchar(255) and every multipart upload in
    # production failed on it (CF-217) because R2 issues ids of ~343 chars.
    # Neither AWS nor Cloudflare documents a maximum, so any fixed width is a
    # guess with the same bug waiting behind it; in Postgres text and varchar(n)
    # are identical in storage and performance.
    upload_id: Mapped[str | None] = mapped_column(Text)
    # What the client said the video's length was at presign time (CF-91).
    # Carried from presign to completion so the quota charge is taken from the
    # figure declared before the transfer, not from whatever the completion
    # call asserts. A claim, never a measurement — kept apart from
    # original_duration below for exactly that reason.
    declared_duration: Mapped[float | None] = mapped_column(Float)
    # Pipeline progress while status == processing: fraction 0.0-1.0 plus a
    # machine-readable stage slug (e.g. "tracking_ball") for the frontend bar.
    progress: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    progress_stage: Mapped[str | None] = mapped_column(String(64))
    # Who may read this game and its clips (CF-108). Private by default —
    # nothing becomes visible to a non-owner without a deliberate change.
    visibility: Mapped[Visibility] = mapped_column(
        SAEnum(Visibility, name="visibility"),
        nullable=False,
        server_default=Visibility.private.value,
        default=Visibility.private,
    )
    # Opt-in dead-time removal: one condensed video with only the rally
    # windows kept, produced alongside the highlight clips.
    condense_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    condensed_video_url: Mapped[str | None] = mapped_column(String(2048))
    original_duration: Mapped[float | None] = mapped_column(Float)
    condensed_duration: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped["User"] = relationship(back_populates="games")  # type: ignore[name-defined]
    clips: Mapped[list["Clip"]] = relationship(back_populates="game", cascade="all, delete-orphan")  # type: ignore[name-defined]
