"""Post model — a caption plus a reference to an existing clip (CF-109)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Enum as SAEnum, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.visibility import Visibility


class Post(Base):
    """Publishing copies no video.

    A post is a thin row pointing at a Clip: the clip already lives in R2, and
    duplicating it per post would multiply storage for no benefit. Playback
    resolves through the clip at read time, so trimming a clip updates its post
    for free.

    **No UNIQUE on (author_id, clip_id), deliberately.** Posting the same clip
    twice is a legitimate act — a better caption months later, a repost after
    deleting the first — and a unique index makes that permanently impossible,
    including after the original was deleted, in a way that's expensive to
    reverse once the table has rows. The failure it would prevent is a
    double-tapped Post button, which is a client concern: the composer disables
    its button on submit, and an idempotency key on the write is the right fix
    if duplicates ever show up in practice. Noting the tradeoff because the
    constraint is much cheaper now than later, so choosing not to add it should
    be a decision on the record rather than an omission.
    """

    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # No index=True: the composite (author_id, created_at) in the posts migration
    # covers author lookups, and declaring one here would have autogenerate
    # keep proposing the redundant single-column index back.
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
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
    # No index=True: a bare `created_at` index still has no reader of its own.
    # The profile grid enters through author_id and takes the first composite
    # below; the feed (CF-111) sorts and pages on `(created_at, id)` and takes
    # the second. A single-column index would be a prefix of that one, serving
    # nothing either composite does not already serve.
    # server_default mirrors the migration's `sa.func.now()`. Without it the
    # column is only ever filled by the Python default, so a row written by
    # anything that isn't this ORM — a backfill, a psql insert, a future COPY —
    # would violate NOT NULL, and autogenerate would keep proposing to drop the
    # default the database actually has.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )

    # The composite the migration creates, declared so it reaches
    # Base.metadata. Without it `alembic revision --autogenerate` sees an index
    # in the database that the models don't know about and emits drop_index —
    # the same drift class test_models_registered.py was added here to catch,
    # one level down. upload_event.py sets the precedent for declaring it.
    __table_args__ = (
        Index("ix_posts_author_created", "author_id", "created_at"),
        # The feed's sort key (migration 019). Declared DESC to match both the
        # ORDER BY and the row-value cursor, so the keyset comparison is a plain
        # range scan from the cursor position rather than an Incremental Sort.
        #
        # Declared here so `Base.metadata.create_all` builds it for the Postgres
        # test fixtures — *not* for the autogenerate drift protection the
        # comment above claims for its neighbour. That reasoning holds for
        # `ix_posts_author_created`, which is a plain column index; alembic
        # cannot compare **expression** indexes and skips them in both
        # directions with a warning, so this one is invisible to autogenerate
        # either way.
        Index("ix_posts_created_at_id", text("created_at DESC"), text("id DESC")),
    )
