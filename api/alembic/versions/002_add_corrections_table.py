"""Add corrections table for ML training data

Revision ID: 002
Revises: 001
Create Date: 2026-03-31
"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL to avoid sa.Enum trying to CREATE TYPE actiontype again
    op.execute("""
        CREATE TABLE corrections (
            id UUID NOT NULL PRIMARY KEY,
            clip_id UUID NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            original_action actiontype NOT NULL,
            corrected_action VARCHAR(50) NOT NULL,
            original_confidence FLOAT NOT NULL,
            start_time FLOAT NOT NULL,
            end_time FLOAT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.create_index("ix_corrections_clip_id", "corrections", ["clip_id"])


def downgrade() -> None:
    op.drop_table("corrections")
