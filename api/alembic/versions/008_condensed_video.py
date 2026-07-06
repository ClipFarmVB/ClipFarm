"""Drop standalone dead-time tables; add condensed-video columns to games

The dead-time prototype (runs/clips tables, /deadtime flow) is replaced by
an opt-in condense stage inside the main game pipeline.

Revision ID: 008
Revises: 007
Create Date: 2026-07-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF EXISTS throughout: dev databases predating migration 005 (or created
    # via create_all) hold these tables without the declared indexes.
    op.execute("DROP INDEX IF EXISTS ix_dead_time_clips_run_id")
    op.execute("DROP TABLE IF EXISTS dead_time_clips")
    op.execute("DROP INDEX IF EXISTS ix_dead_time_runs_owner_id")
    op.execute("DROP TABLE IF EXISTS dead_time_runs")
    op.execute("DROP TYPE IF EXISTS deadtimerunstatus")

    op.add_column(
        "games",
        sa.Column("condense_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("games", sa.Column("condensed_video_url", sa.String(length=2048), nullable=True))
    op.add_column("games", sa.Column("original_duration", sa.Float(), nullable=True))
    op.add_column("games", sa.Column("condensed_duration", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "condensed_duration")
    op.drop_column("games", "original_duration")
    op.drop_column("games", "condensed_video_url")
    op.drop_column("games", "condense_requested")

    # Recreate the prototype tables per migration 005 (data is not restored).
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
