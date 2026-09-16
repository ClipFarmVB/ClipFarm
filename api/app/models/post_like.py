"""A like is a row, and the primary key is the whole rule (CF-113)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PostLike(Base):
    """One row per (post, user).

    No `id` column: nothing pages likes, and the composite primary key is the
    idempotency mechanism the card asks for — a double-tap cannot create a
    second row, so it cannot double-count. The router asks "did this insert
    land" with an `ON CONFLICT DO NOTHING` and reads the rowcount, rather than
    reading first and writing second, which is the shape that lets two
    concurrent taps both increment.
    """

    __tablename__ = "post_likes"

    post_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # `server_default` mirrors the migration's `sa.func.now()` and `nullable`
    # its NOT NULL, so a `create_all`-built database has the same defaults as
    # a migrated one — see `follow.py` for the symptom this prevents.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        # Declared here, not as `index=True` on the column, so the metadata
        # carries the same name migration 020 created — the `follow.py`
        # reasoning. The PK covers lookups by post; this is the other
        # direction, which the cascade from `users` walks.
        Index("ix_post_likes_user_id", "user_id"),
    )
