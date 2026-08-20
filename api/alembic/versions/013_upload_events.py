"""Add upload_events — the per-user quota ledger (CF-91)

The quota needs to count what a user has *consumed*, which is not the same as
what they currently own. Counting live `games` rows made the cap refundable:
`DELETE /games/{id}` is a hard delete, so uploading to the cap and deleting
freed the slots again while the GPU spend had already happened.

This table is append-only. `game_id` is ON DELETE SET NULL so deleting a game
detaches its accounting row rather than removing it.

Revision ID: 013
Revises: 012
Create Date: 2026-08-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "upload_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL, not CASCADE: a deleted game must not take its accounting
        # row with it, or the quota becomes refundable again.
        sa.Column(
            "game_id",
            sa.Uuid(),
            sa.ForeignKey("games.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("charged_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # The only query this table serves: one owner, since a window start.
    op.create_index(
        "ix_upload_events_owner_created", "upload_events", ["owner_id", "created_at"]
    )

    # The client's declared length, carried from presign to completion so the
    # charge is taken from what was declared *before* the upload rather than
    # from whatever the completion call claims. Deliberately separate from
    # games.original_duration, which holds only probed values and is served to
    # every reader of GET /games.
    op.add_column("games", sa.Column("declared_duration", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "declared_duration")
    op.drop_index("ix_upload_events_owner_created", table_name="upload_events")
    op.drop_table("upload_events")
