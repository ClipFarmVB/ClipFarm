"""Add the follow graph (CF-110)

`follows` plus denormalized counters on `users`.

Two constraints carry real weight and are enforced here rather than only in the
router: the unique pair (which is also what makes following idempotent) and the
self-follow CHECK.

Revision ID: 017
Revises: 016
Create Date: 2026-08-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# postgresql.ENUM with create_type=False and an explicit .create() below — see
# the note in 011: sa.Enum silently ignores create_type, so a create_table using
# it would re-emit CREATE TYPE and fail on re-entry.
follow_status = postgresql.ENUM(
    "pending", "accepted", name="follow_status", create_type=False
)


def upgrade() -> None:
    follow_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "follows",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "follower_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "followee_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", follow_status, nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("follower_id", "followee_id", name="uq_follow_pair"),
        sa.CheckConstraint("follower_id <> followee_id", name="ck_follow_not_self"),
    )

    # The two directions the lists page, plus the EXISTS the visibility filters
    # run per row — all three want status in the index so accepted-only lookups
    # never touch the table.
    op.create_index("ix_follows_follower", "follows", ["follower_id", "status"])
    op.create_index("ix_follows_followee", "follows", ["followee_id", "status"])

    # Denormalized counters (epic decision 6). A profile renders these on every
    # view; COUNT(*) over a growing edge table per page load is the thing this
    # avoids. Maintained in the same transaction as the edge, and reconciled by
    # the CF-116 drift job.
    op.add_column(
        "users",
        sa.Column("follower_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "users",
        sa.Column("following_count", sa.Integer(), nullable=False, server_default="0"),
    )


    # Counters can only ever be wrong downward through a bug, and the review
    # that found one showed the failure is otherwise silent — SQLAlchemy warned
    # and committed. A CHECK makes the next variant an error at the write that
    # causes it rather than a number nobody audits. Cheap: two constraints on a
    # table written once per follow.
    #
    # It is a backstop, not the mechanism. Left as the only guard, this CHECK
    # converts a wrong number into a stuck authorization edge — a decrement from
    # 0 aborts the whole unfollow transaction *including its DELETE*, forever,
    # leaving a follower who cannot be revoked. `_adjust_counts` therefore
    # floors its decrements in SQL, so the routine path can't reach the CHECK
    # and the CHECK still catches everything else.
    op.create_check_constraint(
        "ck_users_follower_count_non_negative", "users", "follower_count >= 0"
    )
    op.create_check_constraint(
        "ck_users_following_count_non_negative", "users", "following_count >= 0"
    )


def downgrade() -> None:
    # IF EXISTS throughout — dev databases drift (the 008 lesson). The two
    # constraint drops went through `op.drop_constraint`, which emits no
    # IF EXISTS and so was the one pair of statements this comment did not
    # actually cover: a database that never got them (or lost them to a partial
    # run) failed the downgrade on its first statement, which is exactly the
    # 008 failure being guarded against.
    op.execute(
        "ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_following_count_non_negative"
    )
    op.execute(
        "ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_follower_count_non_negative"
    )
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS following_count")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS follower_count")
    op.execute("DROP TABLE IF EXISTS follows")
    op.execute("DROP TYPE IF EXISTS follow_status")
