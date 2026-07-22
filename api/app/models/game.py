import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, Float, String, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class GameStatus(str, enum.Enum):
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
    error_message: Mapped[str | None] = mapped_column(String(1024))
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
