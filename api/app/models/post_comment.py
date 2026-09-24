"""Flat comments on a post, soft-deleted (CF-113)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PostComment(Base):
    """One comment. Flat in v1 — no `parent_id`, per the card.

    **Soft-deleted.** Removing a comment sets `deleted_at` rather than deleting
    the row, so a future reply (threading is explicitly out of v1) is not
    orphaned by its parent vanishing, and so a delete is a conditional
    `UPDATE ... WHERE deleted_at IS NULL` whose rowcount gates the counter
    decrement — the shape `follows` uses so a second tap cannot decrement
    twice. Every reader filters `deleted_at IS NULL`; the partial index below
    is what makes that filter free.
    """

    __tablename__ = "post_comments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # No `index=True` on either FK — the shapes migration 020 built are
    # declared in `__table_args__` below, so the metadata and the database
    # agree (the `follow.py` reasoning; `post.py` declines the same shortcut).
    post_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # In the database, not only the schema: the 500 cap is the card's and a
        # blank comment is not a comment. Same name and text as migration 020.
        CheckConstraint(
            "char_length(body) BETWEEN 1 AND 500", name="ck_post_comments_body_length"
        ),
        # The list's sort key, newest first, keyset on `(created_at, id)` —
        # declared DESC to match the ORDER BY and the row-value cursor exactly
        # (migration 019's reasoning), and partial on live rows so a
        # soft-deleted comment never sits in the hot index. An expression index
        # is invisible to autogenerate either way (`post.py`'s note); it is
        # declared so `create_all` builds it for the Postgres fixtures.
        Index(
            "ix_post_comments_post_created",
            "post_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # The cascade from `users` walks this direction.
        Index("ix_post_comments_author_id", "author_id"),
    )
