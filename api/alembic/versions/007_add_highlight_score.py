"""Add highlight_score column to clips for rally highlight ranking

Revision ID: 007
Revises: 006
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE clips ADD COLUMN highlight_score FLOAT")


def downgrade() -> None:
    op.execute("ALTER TABLE clips DROP COLUMN highlight_score")
