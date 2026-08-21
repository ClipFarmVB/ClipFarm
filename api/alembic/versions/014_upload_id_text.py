"""Widen games.upload_id to Text (CF-217)

Migration 012 declared `upload_id` as varchar(255). R2 issues multipart upload
ids of roughly 343 characters, so in production every multipart upload failed:
the `POST ?uploads` to R2 succeeded, the INSERT carrying the id raised
StringDataRightTruncationError, the transaction rolled back, and the upload died.
Small files were unaffected — they go up as a single PUT with no upload id at
all, which is why local testing and CI never exercised the column's width.

Text rather than a larger fixed width: neither AWS nor Cloudflare documents a
maximum upload-id length, so any new number is another guess with the same
failure waiting behind it. In Postgres text and varchar(n) share storage and
performance, so the only thing a bound buys here is the outage.

Revision ID: 014
Revises: 013
Create Date: 2026-08-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "games",
        "upload_id",
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Lossy by nature: any id longer than 255 characters — which is every real
    # R2 multipart id — cannot survive the round trip. Only safe while no
    # upload is in flight, i.e. no games row is in the `uploading` status.
    op.alter_column(
        "games",
        "upload_id",
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
