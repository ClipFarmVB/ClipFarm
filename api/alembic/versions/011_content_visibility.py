"""Add visibility to games and clips (CF-108)

Introduces the `visibility` enum and puts it on both tables so read access can
be decided centrally (app/services/access.py) instead of by each router's own
`owner_id == user_id` check.

Defaults are chosen so **nothing becomes newly readable when this lands**:

* `games.visibility` is NOT NULL DEFAULT 'private' — every existing game stays
  owner-only, exactly as before.
* `clips.visibility` is NULLABLE with no default. NULL means "inherit from the
  parent game" rather than a copied value, so changing a game's visibility can
  never leave stale clips behind at the old level.

Revision ID: 011
Revises: 010
Create Date: 2026-08-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Created explicitly rather than letting the first add_column emit it, so the
# second column can reuse it (create_type=False) instead of failing on a
# duplicate type.
visibility_enum = sa.Enum("private", "followers", "public", name="visibility")


def upgrade() -> None:
    bind = op.get_bind()
    visibility_enum.create(bind, checkfirst=True)

    op.add_column(
        "games",
        sa.Column(
            "visibility",
            sa.Enum("private", "followers", "public", name="visibility", create_type=False),
            nullable=False,
            server_default="private",
        ),
    )
    op.add_column(
        "clips",
        sa.Column(
            "visibility",
            sa.Enum("private", "followers", "public", name="visibility", create_type=False),
            nullable=True,
        ),
    )

    # List endpoints filter on these; without the index every visibility-scoped
    # query on a large library is a seq scan.
    op.create_index("ix_games_visibility", "games", ["visibility"])
    op.create_index("ix_clips_visibility", "clips", ["visibility"])


def downgrade() -> None:
    # IF EXISTS throughout: dev databases drift, and a partial upgrade must not
    # make the downgrade unrunnable (the 008 lesson).
    op.execute("DROP INDEX IF EXISTS ix_clips_visibility")
    op.execute("DROP INDEX IF EXISTS ix_games_visibility")
    op.execute("ALTER TABLE clips DROP COLUMN IF EXISTS visibility")
    op.execute("ALTER TABLE games DROP COLUMN IF EXISTS visibility")
    # Drop the type last — it can't go while a column still references it.
    op.execute("DROP TYPE IF EXISTS visibility")
