"""add dead time tables

Revision ID: 005
Revises: 004
Create Date: 2026-04-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dead_time_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "processing", "ready", "failed", name="deadtimerunstatus"),
            nullable=False,
        ),
        sa.Column("raw_video_url", sa.String(length=2048), nullable=True),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dead_time_runs_owner_id", "dead_time_runs", ["owner_id"])

    op.create_table(
        "dead_time_clips",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("start_time", sa.Float(), nullable=False),
        sa.Column("end_time", sa.Float(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("clip_url", sa.String(length=2048), nullable=False),
        sa.Column("thumbnail_url", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["dead_time_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dead_time_clips_run_id", "dead_time_clips", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_dead_time_clips_run_id", table_name="dead_time_clips")
    op.drop_table("dead_time_clips")

    op.drop_index("ix_dead_time_runs_owner_id", table_name="dead_time_runs")
    op.drop_table("dead_time_runs")

    op.execute("DROP TYPE IF EXISTS deadtimerunstatus")
