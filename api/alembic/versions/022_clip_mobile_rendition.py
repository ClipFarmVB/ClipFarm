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

**Numbered 022, parented on 023 — and the number is not the order.** The
chain is linear and its order is the `down_revision` links, not the filename
prefixes. `main`'s head is `023_sample_game.py` (revision "023", down_revision
"016"), so this revises 023. 017-021 are claimed by branches that have not
merged (the CF-109 social stack and `021_device_tokens.py`, CF-318, #488); when
this file was written they were expected to land first, and they have not.

The rule, rather than a merge order to hope for: **at merge, `down_revision`
must be whatever revision is then the head of `main`'s chain** — read it from
`api/alembic/versions/` on `main` (the one id no other file names as its
`down_revision`), and change only that line; leave the filename and the id
alone. Never parent two files on the same revision:
`test_the_chain_is_a_single_line` and `test_there_is_exactly_one_head` fail on
that, and `alembic upgrade head` raises `MultipleHeads` and exits 255 with
nothing applied.

This branch predates 023, so until `main` is merged back in, 023 is declared in
`PENDING_UPSTREAM` in `api/tests/test_migration_chain.py` and
`test_the_chain_is_not_currently_bootable_if_anything_is_pending` is red on
purpose: this branch's chain cannot resolve on its own and it must not merge as
it stands. The merge-back brings `023_sample_game.py` in; delete the entry in
the same commit (`test_pending_upstream_entries_are_still_pending` fails until
you do).

Applying this to the shared Supabase database is a deliberate, separate
`alembic upgrade head` by a person (CLAUDE.md, Migrations).

Revision ID: 022
Revises: 023
Create Date: 2026-09-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "023"
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
