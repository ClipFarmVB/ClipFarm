"""Post model — a caption plus a reference to an existing clip (CF-109)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.visibility import Visibility


class Post(Base):
    """Publishing copies no video.

    A post is a thin row pointing at a Clip: the clip already lives in R2, and
    duplicating it per post would multiply storage for no benefit. Playback
    resolves through the clip at read time, so trimming a clip updates its post
    for free.
    """

    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # CASCADE, not SET NULL: a post whose clip is gone has nothing to play, so
    # deleting the clip must remove the post rather than leave a dead card.
    clip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    caption: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Never wider than the clip it references — enforced on write in the router,
    # because the check needs the clip's *effective* visibility (which may be
    # inherited from its game) and that isn't expressible as a column default.
    visibility: Mapped[Visibility] = mapped_column(
        SAEnum(Visibility, name="visibility"),
        nullable=False,
        server_default=Visibility.private.value,
        default=Visibility.private,
    )
    # Denormalized (epic decision 6): a feed renders these per card, and a
    # COUNT(*) per row does not survive contact with a feed. CF-113 maintains
    # them in the same transaction as the like/comment write.
    like_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    comment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
