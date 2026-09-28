"""Add clips.mobile_url (CF-321)

The phone-sized rendition's URL. Nullable with no backfill, and both halves of
that are deliberate.

Nullable because NULL is a *normal* value here, not a missing one: a clip whose
source was already at or under the 720px short-side bound gets no second file at
all, and one whose rendition failed to encode is left NULL rather than failing
the clip. Clients fall back to `clip_url`, which for those clips is already the
phone rendition. See `api/app/models/clip.py`.

No backfill because there is nothing to backfill *to* — the renditions do not
exist yet. Every clip already in the table keeps NULL and keeps being served at
source resolution; only clips cut after this deploy get one. Re-encoding the
back catalogue is a separate, expensive job (it means pulling every clip out of
R2), and it is not what this card asked for.

**Numbered 022 while revising 016, and the gap is on purpose.** `main` is at
016. 017-020 are claimed by the CF-109 social stack (`017_follow_graph.py` and
`018_visibility_index_shape.py` for CF-110 (#191), `019_posts_feed_cursor_index`
for CF-111 (#192), `020_post_engagement.py` for CF-113 (#477)) and 021 by
`021_device_tokens.py` (CF-318, #488) — all on branches that have not merged.

This follows exactly what 021 did: reserve a number past the unmerged stack, but
parent onto main's real head, because `api/tests/test_migration_chain.py`
requires every `down_revision` to resolve to a file that is actually present and
`PENDING_UPSTREAM` is itself asserted empty. Pointing this at "021" would fail
`test_every_down_revision_resolves` and `test_there_is_exactly_one_head` on this
branch, with no green configuration available.

So this file expects to be rebased, not renumbered. **When #488 merges, change
`down_revision` to "021" and leave the filename and the id alone**; if the
social stack merges first, re-parent onto whatever is then at the tip. If this
and 021 both land unrebased, `test_the_chain_is_a_single_line` fires and names
016 as the parent of two — which is the accurate complaint, and the point of
parenting onto a real revision rather than a hoped-for one.

Revision ID: 022
Revises: 016
Create Date: 2026-09-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 2048 to match clip_url and thumbnail_url — same bucket, same key shape,
    # and a rendition URL is if anything shorter than the clip's.
    op.add_column(
        "clips",
        sa.Column("mobile_url", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    # Drops the column and, with it, every reference to the rendition objects
    # in R2. The objects themselves survive under the `clips-mobile/` prefix and
    # are not reachable by any sweep (the retention sweep lists `raw/` only), so
    # a downgrade that is not followed by an upgrade strands them. Deliberate:
    # a downgrade should not delete data it cannot put back, and the prefix
    # makes them trivial to find and remove by hand.
    op.drop_column("clips", "mobile_url")
