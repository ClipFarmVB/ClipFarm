"""Add the 'uploading' game status and games.upload_id

CF-163: the browser now PUTs video straight to R2 with a presigned URL. A game
row is created before the bytes exist, so it needs a status that means "not yet
uploaded" — distinct from `queued`, which means the object is confirmed in R2
and the worker may pick it up. `upload_id` holds the S3 multipart upload id
while that is true, so a delete can abort the upload instead of leaving billable
parts behind.

Revision ID: 010
Revises: 009
Create Date: 2026-08-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PG 12+ allows ALTER TYPE ... ADD VALUE inside a transaction block (which
    # is what alembic runs migrations in); the restriction is that the new value
    # cannot be *used* in the same transaction. Nothing below writes it, so this
    # is safe. IF NOT EXISTS keeps a re-run idempotent.
    op.execute("ALTER TYPE gamestatus ADD VALUE IF NOT EXISTS 'uploading'")

    op.add_column("games", sa.Column("upload_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "upload_id")
    # The 'uploading' enum value is deliberately left in place: Postgres has no
    # ALTER TYPE ... DROP VALUE, and recreating the type would require rewriting
    # every games.status value behind an exclusive lock. A surplus enum member
    # is inert — the same lossy-reversal trade-off documented in 008.
