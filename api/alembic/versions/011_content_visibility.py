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
from sqlalchemy.dialects import postgresql

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Created explicitly rather than letting the first add_column emit it, so the
# second column can reuse it instead of failing on a duplicate type.
#
# postgresql.ENUM, not sa.Enum: `create_type` is a postgresql-dialect parameter.
# sa.Enum accepts the keyword and silently discards it —
#   sa.Enum(..., create_type=False).dialect_impl(postgresql.dialect()).create_type  -> True
#   postgresql.ENUM(..., create_type=False).create_type                             -> False
# so the protection this comment describes was not actually in effect. It
# happened to apply cleanly because alembic's add_column path doesn't emit
# CREATE TYPE here anyway, which is exactly the kind of accident that breaks
# when the idiom is copied for the next enum column.
_VISIBILITY_VALUES = ("private", "followers", "public")
visibility_enum = postgresql.ENUM(*_VISIBILITY_VALUES, name="visibility")


def _visibility_column_type() -> postgresql.ENUM:
    """The existing type, for a column — never re-creates it."""
    return postgresql.ENUM(*_VISIBILITY_VALUES, name="visibility", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    visibility_enum.create(bind, checkfirst=True)

    op.add_column(
        "games",
        sa.Column(
            "visibility",
            _visibility_column_type(),
            nullable=False,
            server_default="private",
        ),
    )
    op.add_column(
        "clips",
        sa.Column(
            "visibility",
            _visibility_column_type(),
            nullable=True,
        ),
    )

    # PARTIAL indexes, deliberately.
    #
    # A full b-tree over a three-value enum where ~100% of rows are 'private'
    # would not be chosen by the planner — when a predicate matches most of the
    # table, a seq scan wins — while every pipeline-inserted clip (hundreds per
    # game) pays its maintenance cost. The selective half of
    # `visibility = 'public' OR owner_id = :viewer` is owner_id, which is
    # already indexed.
    #
    # What these do cover is the anonymous read path: the rows are the few
    # public ones, so the index stays small and is worth scanning.
    op.create_index(
        "ix_games_visibility_public",
        "games",
        ["visibility"],
        postgresql_where=sa.text("visibility = 'public'"),
    )
    op.create_index(
        "ix_clips_visibility_public",
        "clips",
        ["visibility"],
        postgresql_where=sa.text("visibility = 'public'"),
    )


def downgrade() -> None:
    # IF EXISTS throughout: dev databases drift, and a partial upgrade must not
    # make the downgrade unrunnable (the 008 lesson).
    op.execute("DROP INDEX IF EXISTS ix_clips_visibility_public")
    op.execute("DROP INDEX IF EXISTS ix_games_visibility_public")
    op.execute("ALTER TABLE clips DROP COLUMN IF EXISTS visibility")
    op.execute("ALTER TABLE games DROP COLUMN IF EXISTS visibility")
    # Drop the type last — it can't go while a column still references it.
    op.execute("DROP TYPE IF EXISTS visibility")
