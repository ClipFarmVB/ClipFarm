import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255))  # null = SSO-only
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # ── Public identity (CF-107) ──────────────────────────────────────────────
    # Nullable: existing rows predate this and Supabase Auth remains the account
    # source of truth. A user without a username has no public presence yet —
    # the frontend hides the Post button until one is claimed (ClipModal, via
    # `needsHandle`), which is what keeps this comment true: `create_post` does
    # not check, and a *generated* handle is withheld from every post response
    # (`PostAuthor.from_author`), so a handle-less author would otherwise
    # publish a card that names nobody and links nowhere.
    #
    # Stored lower-cased; uniqueness is enforced by a functional index on
    # lower(username) so "Matt" and "matt" can't both exist (see migration 010).
    username: Mapped[str | None] = mapped_column(String(30), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bio: Mapped[str | None] = mapped_column(String(280), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Private by default — epic decision 1. This footage is youth sports, so
    # nothing becomes visible to non-followers without a deliberate opt-in.
    is_private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    # Rename rate limiting: a freed handle shouldn't be instantly re-claimable
    # by someone impersonating the previous holder.
    username_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # True for handles migration 010 invented from the email local part. Such a
    # user has a handle but has never made a choice, so they still get the claim
    # prompt and the free first claim — otherwise the backfill silently spends
    # both on a name they never picked.
    username_is_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )

    # Denormalized follow counters (CF-110, epic decision 6). A profile renders
    # both on every view; COUNT(*) over a growing edge table per page load is
    # what this avoids. Written in the same transaction as the edge; the CF-116
    # reconciliation job is what catches drift.
    follower_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    following_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )

    games: Mapped[list["Game"]] = relationship(back_populates="owner")  # type: ignore[name-defined]
