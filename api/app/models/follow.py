"""Follow graph (CF-110)."""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FollowStatus(str, enum.Enum):
    """`pending` is what makes "private by default" mean something.

    Following a public account is `accepted` immediately. Following a private
    one creates a request the target has to approve — until then the follower
    sees nothing beyond what any stranger sees.
    """

    pending = "pending"
    accepted = "accepted"


class Follow(Base):
    __tablename__ = "follows"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # No `index=True` on either FK. Migration 017 indexes each direction *with*
    # `status`, so a single-column declaration here would put a different shape
    # in the metadata than the one in the database: the next `--autogenerate`
    # emits creates for the pair declared on the columns and drops for the
    # composite pair 017 actually built, silently reverting a deliberate choice.
    # That is the same repository-says-one-thing-database-says-another problem
    # migration 018 exists to repair, and the reason `post.py` declines the same
    # shortcut. The composites are declared in `__table_args__` instead.
    follower_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    followee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[FollowStatus] = mapped_column(
        SAEnum(FollowStatus, name="follow_status"),
        nullable=False,
        server_default=FollowStatus.pending.value,
        default=FollowStatus.pending,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        # One row per pair. This is also the idempotency mechanism: a double-tap
        # on Follow can't create two edges, and the counter stays honest.
        UniqueConstraint("follower_id", "followee_id", name="uq_follow_pair"),
        # Enforced in the database, not only the router — the card asks for
        # self-follow to be impossible, and a CHECK survives any code path.
        CheckConstraint("follower_id <> followee_id", name="ck_follow_not_self"),
        # The two directions the lists page, plus the EXISTS the visibility
        # filters run per row — all three want `status` in the key so an
        # accepted-only lookup never touches the table. Names and column order
        # match migration 017 exactly; agreeing with the database is the whole
        # point of declaring them here rather than leaving them implicit.
        Index("ix_follows_follower", "follower_id", "status"),
        Index("ix_follows_followee", "followee_id", "status"),
    )
