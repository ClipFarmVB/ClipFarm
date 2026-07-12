"""Add processing progress columns to games

The worker reports pipeline progress (0.0-1.0 + a stage slug) so the
frontend's existing status poll can render a progress bar while a game
is processing.

Revision ID: 009
Revises: 008
Create Date: 2026-07-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "games",
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column("games", sa.Column("progress_stage", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "progress_stage")
    op.drop_column("games", "progress")
