"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "teams",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "players",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("jersey_number", sa.Integer(), nullable=True),
        sa.Column("team_id", sa.UUID(), nullable=True),
        sa.Column("photo_url", sa.String(2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "games",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "processing", "ready", "failed", name="gamestatus"),
            nullable=False,
        ),
        sa.Column("raw_video_url", sa.String(2048), nullable=True),
        sa.Column("error_message", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_games_owner_id", "games", ["owner_id"])

    op.create_table(
        "clips",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("game_id", sa.UUID(), nullable=False),
        sa.Column("player_id", sa.UUID(), nullable=True),
        sa.Column(
            "action_type",
            sa.Enum("spike", "serve", "dig", "set", "block", "unknown", name="actiontype"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("start_time", sa.Float(), nullable=False),
        sa.Column("end_time", sa.Float(), nullable=False),
        sa.Column("clip_url", sa.String(2048), nullable=False),
        sa.Column("thumbnail_url", sa.String(2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_clips_game_id", "clips", ["game_id"])
    op.create_index("ix_clips_player_id", "clips", ["player_id"])
    op.create_index("ix_clips_action_type", "clips", ["action_type"])

    # Dev seed: create the fixed dev user used by the API stubs
    op.execute(
        "INSERT INTO users (id, email, created_at) VALUES "
        "('00000000-0000-0000-0000-000000000001', 'dev@clipfarm.local', NOW()) "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("clips")
    op.drop_table("games")
    op.drop_table("players")
    op.drop_table("teams")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS gamestatus")
    op.execute("DROP TYPE IF EXISTS actiontype")
