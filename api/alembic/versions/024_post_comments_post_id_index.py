"""Plain index on post_comments.post_id (CF-113)

Revision ID: 024
Revises: 020
Create Date: 2026-09-28

The foreign-key index `020` left out. The RI cascade from `posts` deletes EVERY
comment, soft-deleted ones included, so it cannot use `020`'s partial index on
live rows — measured, not assumed: with only that index a cascade-shaped
`DELETE FROM ONLY post_comments WHERE post_id = $1` plans a Seq Scan, and with
this one it plans an Index Scan.

Its own revision rather than an edit to `020`, because another branch
(`web/CF-112-feed-ui`, #214) carries a copy of `020` without it. A database
already stamped at `020` never re-runs `020`, so an in-place edit would reach
only databases that had not upgraded yet; a new revision reaches them all,
whichever branch lands first.

`IF NOT EXISTS`, because a dev database that ran this branch's earlier `020`
already has the index. No `CONCURRENTLY`, for the reason `018` gives.

Numbered `024`, not `021`: `021`, `022` and `023` are claimed by open PRs
(#488, #500, #571), and a second file claiming one of those ids would fail
`test_no_revision_id_is_claimed_twice` when either lands.

Downgrade is `IF EXISTS` — dev databases drift (the 008 lesson).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "024"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_post_comments_post_id "
        "ON post_comments (post_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_post_comments_post_id")
