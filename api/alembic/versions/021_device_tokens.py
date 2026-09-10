"""Add device_tokens (CF-318)

A registry of push destinations. Nothing sends yet — the send half of CF-318
lands separately — so there is nothing to backfill and no existing row to
migrate.

**Numbered 021 while revising 016, and the gap is on purpose.** `main` is at
016, but 017, 018, 019 and 020 are all claimed by the CF-109 social stack on
branches that have not merged: `017_follow_graph.py` and
`018_visibility_index_shape.py` (CF-110, #191), `019_posts_feed_cursor_index.py`
(CF-111, #192) and `020_post_engagement.py` (CF-113, #477). Two of those
branches moved the day before this was written, so they are live work, not
archaeology.

Taking 017 here would have been the collision `tests/test_migration_chain.py`
exists to name: the filenames differ, so git merges both without a conflict and
leaves two files claiming 017, and `alembic upgrade head` then dies with
`Multiple head revisions are present` — a message that describes a fork nobody
made. Reserving a number past that stack removes the duplicate outright and
leaves only the ordinary rebase.

So this file expects to be rebased, not renumbered. **When the social stack
merges, change `down_revision` to "020" and leave the filename and the id
alone.** If it merges after this one, the stack rebases instead and this file
does not move at all. Either way `test_the_chain_is_a_single_line` is what
fires if both land unrebased, naming 016 as the parent of two — which is the
accurate complaint, and the reason this is not deferred to a PENDING_UPSTREAM
entry (that dict is a merge blocker, and nothing here is blocked on the social
stack).

Revision ID: 021
Revises: 016
Create Date: 2026-09-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "021"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_tokens",
        sa.Column("id", sa.UUID(), primary_key=True),
        # CASCADE: a deleted account has no devices worth notifying.
        sa.Column(
            "owner_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Not a Postgres enum: the set grows (web push next), and every
        # addition to a real enum type is another ALTER TYPE migration for a
        # value only our own routes write. The check is a pydantic Literal at
        # the edge — see app/schemas/device_token.py.
        sa.Column("platform", sa.String(length=16), nullable=False),
        # Text, not a width. 014 and 015 both widened a guessed varchar after
        # production overflowed it; FCM registration tokens are not documented
        # as bounded, so there is no honest number to pick. The bound lives in
        # the schema, where it is a readable 422.
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # The whole registration protocol rests on this. `POST /devices/tokens`
    # upserts with `ON CONFLICT (token)`, which requires a unique index on the
    # column, and one row per device is what stops a resold phone from being
    # notified for two owners at once.
    op.create_unique_constraint("uq_device_tokens_token", "device_tokens", ["token"])
    # The send fan-out is always "every device for this owner", and the ON
    # DELETE CASCADE above has to find these rows by owner_id. Postgres does
    # not index a foreign key for you.
    op.create_index("ix_device_tokens_owner_id", "device_tokens", ["owner_id"])


def downgrade() -> None:
    # IF EXISTS: dev databases drift and a partial upgrade must stay reversible
    # (the 008 lesson). Dropping the table takes its index and constraint with
    # it, so neither needs its own statement.
    op.execute("DROP TABLE IF EXISTS device_tokens")
