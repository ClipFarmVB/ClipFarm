"""Add games.is_sample and games.sample_credit (CF-220)

`is_sample` marks a game as a copy of the configured demo game, made at signup
(`app/services/sample_game.py`). Every existing game is a real upload, so the
server default of false is the correct value for all of them and there is
nothing to backfill.

`sample_credit` is the footage credit frozen onto each copy when it is made,
from `SAMPLE_GAME_CREDIT`. Stored rather than read live, so a copy keeps the
credit of the footage it actually plays after the setting moves to another
source. Null on every game that is not a copy.

Numbered 023 and parented on 016, the head of main when this was written.
017-022 are claimed by open PRs, several of which also parent on 016, so
whichever of them merges first leaves this with a sibling head: re-parent it
onto the chain's head at merge, as #488 and #500 expect to be.

Revision ID: 023
Revises: 016
Create Date: 2026-09-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "games",
        sa.Column("is_sample", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("games", sa.Column("sample_credit", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "sample_credit")
    op.drop_column("games", "is_sample")
