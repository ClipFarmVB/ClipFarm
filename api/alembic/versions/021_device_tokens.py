"""Add device_tokens (CF-318)

A registry of push destinations. Nothing sends yet — the send half of CF-318
lands separately — so there is nothing to backfill and no existing row to
migrate.

**Numbered 021 out of order with its parent, and the gap is on purpose.** 017,
018, 019 and 020 are all claimed by the CF-109 social stack on branches that
have not merged: `017_follow_graph.py` and `018_visibility_index_shape.py`
(CF-110, #191), `019_posts_feed_cursor_index.py` (CF-111, #192) and
`020_post_engagement.py` (CF-113, #477). Two of those branches moved the day
before this was written, so they are live work, not archaeology.

Taking 017 here would have been the collision `tests/test_migration_chain.py`
exists to name: the filenames differ, so git merges both without a conflict and
leaves two files claiming 017, and `alembic upgrade head` then dies with
`Multiple head revisions are present` — a message that describes a fork nobody
made. Reserving a number past that stack removes the duplicate outright and
leaves only the ordinary rebase.

**The parent is 023, the head of `main`.** It was 016 when this was written,
and this file said so; `023_sample_game.py` (CF-220, #571) has since landed on
top of 016 there. Left on 016 the two would be siblings, which is the same
outage by the other route: `main`'s own `test_the_chain_is_a_single_line` and
`test_there_is_exactly_one_head` go red on merge, and `alembic upgrade head`
raises `MultipleHeads`, prints `FAILED: Multiple head revisions are present for
given argument 'head'` and exits 255 with nothing applied — on Render's
`preDeployCommand` and ahead of uvicorn in the api container, so it takes every
endpoint with it. So the rebase this file always expected has happened: the
filename and the id stay, only the parent moves. If the social stack lands
before this does, it moves once more, onto whatever head that leaves.

The cost of keeping the id is that `versions/` now lists 016, 021, 023 while
the chain runs 016 -> 023 -> 021, so the listing no longer reads as the order.
`test_the_filename_prefix_matches_the_revision_id` is satisfied either way — it
asks that a file's prefix match its own id, which this one does — and
renumbering to 024 would move an id other files already name by hand
(`app/models/device_token.py`), for a directory listing that the social stack's
own rebase is about to reorder again regardless.

One consequence to expect rather than be surprised by: until `main` is merged
into this branch, 023 is not in `versions/` here, so
`test_every_down_revision_resolves` names it as a parent that is not present
and `test_there_is_exactly_one_head` counts 016 as a second head. That is the
declared state of a branch whose base has moved, and deliberately not a
PENDING_UPSTREAM entry: 023 is merged, not waiting on an open PR, so the
dependency is the base merge rather than a merge *order*, and nothing has to be
remembered and deleted afterwards. Both checks go green the moment the merge
brings the file in.

Revision ID: 021
Revises: 023
Create Date: 2026-09-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "021"
down_revision: Union[str, None] = "023"
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
