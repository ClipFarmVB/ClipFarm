"""Add public profile fields to users (CF-107)

Gives every account a public identity — handle, display name, bio, avatar —
so posts and follows (CF-109/CF-110) have something to attach to.

Two things worth knowing:

* Uniqueness is a functional index on ``lower(username)``, not a plain unique
  constraint. The app stores handles lower-cased, but the index is what makes
  "Matt" and "matt" impossible to both exist even if some future code path
  forgets to normalize.
* Existing rows are backfilled from the email local part. The column stays
  nullable (Supabase Auth, not this table, is the account source of truth, and
  a user who hasn't chosen a handle simply has no public presence yet) — but
  leaving existing accounts without one would make them unreachable the moment
  profiles ship, so they get a generated handle they can change.

Revision ID: 010
Revises: 009
Create Date: 2026-08-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=30), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("bio", sa.String(length=280), nullable=True))
    op.add_column("users", sa.Column("avatar_url", sa.String(length=2048), nullable=True))
    # Private by default (epic decision 1): youth-sports footage must not become
    # visible to non-followers without a deliberate opt-in.
    op.add_column(
        "users",
        sa.Column("is_private", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "users",
        sa.Column("username_changed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Backfill from the email local part, sanitized to the handle charset.
    # row_number() disambiguates collisions (alice@a.com and alice@b.com), and
    # the length guard keeps a short/empty local part from failing validation.
    op.execute(
        """
        WITH candidate AS (
            SELECT
                id,
                LEFT(
                    CASE
                        WHEN LENGTH(REGEXP_REPLACE(LOWER(SPLIT_PART(email, '@', 1)),
                                                   '[^a-z0-9_]', '', 'g')) >= 3
                        THEN REGEXP_REPLACE(LOWER(SPLIT_PART(email, '@', 1)),
                                            '[^a-z0-9_]', '', 'g')
                        ELSE 'player'
                    END,
                    24
                ) AS stem,
                ROW_NUMBER() OVER (
                    PARTITION BY LEFT(
                        CASE
                            WHEN LENGTH(REGEXP_REPLACE(LOWER(SPLIT_PART(email, '@', 1)),
                                                       '[^a-z0-9_]', '', 'g')) >= 3
                            THEN REGEXP_REPLACE(LOWER(SPLIT_PART(email, '@', 1)),
                                                '[^a-z0-9_]', '', 'g')
                            ELSE 'player'
                        END,
                        24
                    )
                    ORDER BY created_at, id
                ) AS n
            FROM users
            WHERE username IS NULL
        )
        UPDATE users u
        SET username = CASE WHEN c.n = 1 THEN c.stem ELSE c.stem || c.n::text END
        FROM candidate c
        WHERE u.id = c.id
        """
    )

    # Case-insensitive uniqueness. Created after the backfill so it validates
    # the generated handles rather than being defeated by them.
    op.execute(
        "CREATE UNIQUE INDEX uq_users_username_lower ON users (LOWER(username))"
    )


def downgrade() -> None:
    # IF EXISTS: dev databases drift, and a failed partial upgrade shouldn't
    # make the downgrade unrunnable (the 008 lesson).
    op.execute("DROP INDEX IF EXISTS uq_users_username_lower")
    op.drop_column("users", "username_changed_at")
    op.drop_column("users", "is_private")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "bio")
    op.drop_column("users", "display_name")
    op.drop_column("users", "username")
