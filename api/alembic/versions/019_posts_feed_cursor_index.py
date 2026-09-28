"""Index the feed's sort key (CF-111 review)

Revision ID: 019
Revises: 018
Create Date: 2026-08-31

The home feed orders by `(created_at DESC, id DESC)` across every followed
author and pages with a row-value cursor over the same pair. `016` created
`ix_posts_author_created (author_id, created_at)` and nothing else, because at
the time every read of `posts` entered through `author_id` — a profile grid —
and `models/post.py` says so in as many words: *"a bare created_at index has no
reader."*

CF-111 is that reader, and the feed does not filter on `author_id` at all: it
filters on `author_id IN (<followed set>)`, which is not a prefix the composite
can serve for an ordered scan. The router's own comment claimed a composite
index made the row-value cursor efficient — it named an index that did not
exist, and the PR's own EXPLAIN shows the cost: an Incremental Sort rather than
an ordered index scan.

**DESC/DESC rather than the default ascending.** Postgres can walk an ascending
index backwards, so an `(created_at, id)` index would also work for the ORDER
BY. Matching the declared direction exactly is what lets the row-value
comparison `(created_at, id) < (:c, :i)` become a plain index range scan from
the cursor position, which is the whole reason the cursor is written as a tuple
rather than an OR of two ranges.

No `CONCURRENTLY`, for the reason `018` records: alembic runs a revision inside
a transaction, and `CREATE INDEX CONCURRENTLY` cannot. `posts` is small and its
write rate is one row per publish. If it ever grows to where this lock is an
outage, the fix is a separate autocommit revision, not bolting `CONCURRENTLY`
onto a transactional one.

Nothing is dropped here. There is no redundant single-column `created_at` index
to remove — the review that prompted this believed `016` had created one, and
it had not.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_posts_created_at_id "
        "ON posts (created_at DESC, id DESC)"
    )


def downgrade() -> None:
    # IF EXISTS — dev databases drift (the 008 lesson).
    op.execute("DROP INDEX IF EXISTS ix_posts_created_at_id")
