"""add collections tables

Revision ID: 006
Revises: 005
Create Date: 2026-05-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_collections_owner_id", "collections", ["owner_id"])

    op.create_table(
        "collection_clips",
        sa.Column("collection_id", sa.UUID(), nullable=False),
        sa.Column("clip_id", sa.UUID(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["clip_id"], ["clips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("collection_id", "clip_id"),
        sa.UniqueConstraint("collection_id", "clip_id", name="uq_collection_clip"),
    )


def downgrade() -> None:
    op.drop_table("collection_clips")
    op.drop_index("ix_collections_owner_id", table_name="collections")
    op.drop_table("collections")
