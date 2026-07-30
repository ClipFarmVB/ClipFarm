import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import String, Float, DateTime, ForeignKey, Enum as SAEnum, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ActionType(str, enum.Enum):
    spike = "spike"
    serve = "serve"
    dig = "dig"
    set = "set"
    block = "block"
    unknown = "unknown"


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    action_type: Mapped[ActionType] = mapped_column(
        SAEnum(ActionType), nullable=False, default=ActionType.unknown
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    highlight_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    clip_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048))
    labels: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    game: Mapped["Game"] = relationship(back_populates="clips")  # type: ignore[name-defined]
    player: Mapped["Player | None"] = relationship(back_populates="clips")  # type: ignore[name-defined]
