import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DeviceToken(Base):
    """One row per app install that has asked to be notified (CF-318).

    **The token is the identity, not the (user, token) pair.** A push token
    names a device, and a device has exactly one current owner: it is handed
    down, resold, or simply signed into by somebody else. So `token` is unique
    across the whole table and registering re-owns it, rather than adding a
    second row. CF-343 makes sign-out unregister the token for the same reason,
    but sign-out is a request that can fail or never be sent — an uninstall
    sends nothing at all — and this constraint is what holds when it does.
    Two rows for one device is the shape where a stranger's phone keeps
    receiving "your game is ready" for footage that is not theirs.

    `platform` is a plain string checked at the edge by a pydantic `Literal`
    rather than a Postgres enum. The set will grow — web push is the obvious
    next one — and every addition to a real enum type is an `ALTER TYPE` in a
    migration, for a value only ever written by our own routes.

    `token` is `Text`, not a width. CF-217 and CF-226 both widened a guessed
    varchar after production traffic overflowed it, and both argued that the
    next number is another guess. FCM registration tokens are not documented as
    bounded, so there is no number to pick honestly. The bound that does exist
    lives in `schemas/device_token.py`, where an over-long token is a 422 the
    caller can read instead of a truncation error — and it keeps the value far
    under the ~2704-byte ceiling a btree unique index imposes.
    """

    __tablename__ = "device_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    # `server_default` alongside the Python default, following post.py: without
    # it the column is filled only by this ORM, so a backfill or a psql insert
    # violates NOT NULL, and autogenerate keeps proposing to drop the default
    # the database actually has.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )
    # Moved on every re-register. The send path (the second half of CF-318)
    # prunes what the provider rejects; this is what lets a token that simply
    # stopped being re-registered be aged out separately.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )

    # Both declared so they reach `Base.metadata` — otherwise autogenerate sees
    # a constraint and an index the models do not know about and emits drops for
    # them. Named explicitly, and matching migration 021 by name: `Base` sets no
    # `naming_convention`, so a bare `unique=True` on the column would be
    # `device_tokens_token_key` here and `uq_device_tokens_token` there, which
    # is the same drift by a quieter route.
    #
    # The unique constraint is load-bearing rather than hygiene: `ON CONFLICT
    # (token)` in the register route has nothing to conflict against without it.
    # The index serves the send fan-out ("every device for this owner") and the
    # ON DELETE CASCADE, neither of which Postgres indexes for you.
    __table_args__ = (
        UniqueConstraint("token", name="uq_device_tokens_token"),
        Index("ix_device_tokens_owner_id", "owner_id"),
    )
