"""Follow graph (CF-110)."""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, UniqueConstraint, Enum as SAEnum
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
    follower_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    followee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
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
    )
