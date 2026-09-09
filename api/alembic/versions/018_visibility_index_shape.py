"""Reshape the CF-108 partial visibility indexes (CF-110 review).

Revision ID: 018
Revises: 017
Create Date: 2026-08-14

`011` created two partial indexes keyed on the column its own WHERE clause
already pins:

    CREATE INDEX ix_games_visibility_public ON games (visibility)
      WHERE visibility = 'public'

Inside a partial index whose predicate is `visibility = 'public'`, `visibility`
is constant — every entry stores the same value, so the key carries no
information and can't narrow anything. The index still works as a filtered row
set, but the payload is wasted and it can't answer anything more than existence.

Keying on a column the queries actually use makes them useful rather than merely
present: `games.id` covers the "is this public game visible" lookup, and
`clips.game_id` is the join key every clip listing goes through, so
`GET /games/{id}/clips` for an anonymous viewer can walk the small public-only
index instead of taking the row set and re-joining.

**The clips predicate has to cover the inherit case, or it matches nothing.**
`clips.visibility` is NULL for every clip the pipeline produces — NULL means
"inherit from the game", which `models/clip.py` documents as the deliberate
default and `test_access.py` names as the path that actually runs in production.
A partial index `WHERE visibility = 'public'` is therefore true for
approximately zero production rows, and the anonymous listing it was meant to
serve resolves through `_clips_predicate`'s
`clips.visibility IS NULL AND games.visibility = 'public'` branch, which such an
index cannot answer. `IS NULL OR = 'public'` is the set that branch actually
reads. Same mistake as `011`'s, reached from the opposite direction: a predicate
chosen for what the column *could* say rather than what it does say.

**Not `CONCURRENTLY`, decided rather than defaulted into.** Alembic runs a
revision in a transaction and `CREATE INDEX CONCURRENTLY` cannot execute inside
one, so using it here means an autocommit block and giving up the atomicity of
the rest of the revision. These statements do take a write-blocking lock on
`clips` for their duration; that is affordable at this table's size and is the
reason it is acceptable, not an oversight. If `clips` grows to where the lock is
a real outage, the answer is to split these two statements into their own
autocommit revision — not to bolt `CONCURRENTLY` onto a transactional one.

**Why a new revision rather than editing `011`.** `011` merged in #188 and has
already run wherever the app is deployed, so its row sits in `alembic_version`
and the file will never execute again. Editing it would leave the repository
claiming one shape while every existing database has the other, with no
migration that ever reconciles them — the CF-110 review caught exactly that.
A spent migration is history; changing your mind needs a forward step.

Idempotent throughout (IF EXISTS / IF NOT EXISTS): the indexes may already be
either shape depending on when a given database was built, and the 008 lesson
is that a partial upgrade must not make the next one unrunnable.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_games_visibility_public")
    op.execute("DROP INDEX IF EXISTS ix_clips_visibility_public")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_games_visibility_public ON games (id) "
        "WHERE visibility = 'public'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_clips_visibility_public ON clips (game_id) "
        "WHERE visibility IS NULL OR visibility = 'public'"
    )


def downgrade() -> None:
    """Back to the `011` shape, so a downgrade past this leaves the database
    matching what `011` would have built."""
    op.execute("DROP INDEX IF EXISTS ix_games_visibility_public")
    op.execute("DROP INDEX IF EXISTS ix_clips_visibility_public")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_games_visibility_public ON games (visibility) "
        "WHERE visibility = 'public'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_clips_visibility_public ON clips (visibility) "
        "WHERE visibility = 'public'"
    )
