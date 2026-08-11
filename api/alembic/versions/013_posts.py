"""Add posts (CF-109)

A post is a caption plus a reference to an existing clip — no video is copied,
so there is nothing to backfill and nothing in R2 to migrate.

Numbered 013 (not 012) so the chain stays linear: 012 is taken by #185
(CF-163 presigned uploads), which opened first and has already renumbered
once. This PR is third in a stack behind CF-107 and CF-108, so it merges
last by construction — which means it also needs #185 merged first.

Revision ID: 013
Revises: 012
Create Date: 2026-08-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "posts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "author_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # CASCADE: a post whose clip is gone has nothing to play.
        sa.Column(
            "clip_id",
            sa.UUID(),
            sa.ForeignKey("clips.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("caption", sa.String(length=500), nullable=True),
        # Reuses the enum type created by 011. Must be postgresql.ENUM, not
        # sa.Enum: `create_type` is a postgresql-dialect option, and the generic
        # type silently ignores it and re-emits CREATE TYPE inside create_table,
        # which fails because 011 already made it.
        sa.Column(
            "visibility",
            postgresql.ENUM(
                "private", "followers", "public", name="visibility", create_type=False
            ),
            nullable=False,
            server_default="private",
        ),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_posts_author_id", "posts", ["author_id"])
    op.create_index("ix_posts_clip_id", "posts", ["clip_id"])
    # The feed's ordering (CF-111) is (created_at DESC, id DESC) scoped by
    # author. Added here so the feed query has its index the day it lands.
    op.create_index("ix_posts_author_created", "posts", ["author_id", "created_at"])
    op.create_index("ix_posts_created_at", "posts", ["created_at"])


def downgrade() -> None:
    # IF EXISTS: dev databases drift and a partial upgrade must stay reversible
    # (the 008 lesson). The visibility type belongs to 011 — leave it alone.
    op.execute("DROP TABLE IF EXISTS posts")
