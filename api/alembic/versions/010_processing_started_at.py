"""Add processing start timestamp to games

Anchors the frontend's time-remaining estimate: ETA is extrapolated from
elapsed time since processing began, so it must survive page reloads.

Revision ID: 010
Revises: 009
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "games",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("games", "processing_started_at")
