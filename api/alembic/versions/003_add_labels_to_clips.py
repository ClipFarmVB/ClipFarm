"""Add labels array column to clips for multi-action tagging

Revision ID: 003
Revises: 002
Create Date: 2026-03-31
"""
from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE clips
        ADD COLUMN labels VARCHAR(50)[] NOT NULL DEFAULT '{}'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE clips DROP COLUMN labels")
