"""Likes and comments on posts (CF-113)

Revision ID: 020
Revises: 019
Create Date: 2026-09-09

Two new tables and two constraints on an existing one.

`post_likes` has no `id`: the primary key is `(post_id, user_id)`, and that is
the idempotency mechanism rather than a detail — a double-tap cannot create a
second row, so it cannot double-count, and the router's "did the insert land"
question is answered by the rowcount of an `ON CONFLICT DO NOTHING` rather
than by a read-then-write it would have to serialize itself. Nothing pages
likes, so nothing needs a sortable id.

`post_comments` soft-deletes. `deleted_at` rather than a `DELETE` so a future
reply (threading is out of v1, per the card) is not orphaned by its parent
vanishing, and so a deletion is a conditional `UPDATE ... WHERE deleted_at IS
NULL` whose rowcount gates the counter — the same shape `follows` uses for
accept and reject, and for the same reason: a second tap on Delete must not
decrement twice.

**The two counter CHECKs on `posts`.** `016` created `like_count` and
`comment_count` without them because no writer existed yet; `017` gave the
`users` counters theirs. Now that something writes these, they get the same
backstop — and, as with `users`, the router floors its decrements with
`GREATEST` in SQL so the CHECK only ever fires for a *new* bug, never for the
ordinary drift a cascade leaves behind. `models/post.py` declares both so
`Base.metadata` agrees with this file; every `*_pg.py` fixture is built by
`create_all`, and a constraint that lives only here would be absent from every
database the tests run against.

The comments index is partial on the live rows and declared `DESC, DESC` to
match the list's `ORDER BY` and its row-value cursor exactly, for the reason
`019` gives. No `CONCURRENTLY`, for the reason `018` gives: alembic runs a
revision in a transaction, both tables are empty at creation, and `posts` is
small.

Downgrade is `IF EXISTS` throughout — dev databases drift (the 008 lesson).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "post_likes",
        sa.Column(
            "post_id",
            sa.UUID(),
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Postgres does not index a foreign key for you. The PK serves lookups by
    # post; the cascade from `users` (and any "posts I liked" reader) needs the
    # other direction.
    op.create_index("ix_post_likes_user_id", "post_likes", ["user_id"])

    op.create_table(
        "post_comments",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column(
            "post_id",
            sa.UUID(),
            sa.ForeignKey("posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        # Enforced in the database, not only the schema: a blank comment is not
        # a comment, and the 500 cap is the card's, not pydantic's.
        sa.CheckConstraint(
            "char_length(body) BETWEEN 1 AND 500", name="ck_post_comments_body_length"
        ),
    )
    # The list's sort key, newest first, keyset on `(created_at, id)`. Partial
    # on live rows so soft-deleted comments never sit in the hot index.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_post_comments_post_created "
        "ON post_comments (post_id, created_at DESC, id DESC) "
        "WHERE deleted_at IS NULL"
    )
    op.create_index("ix_post_comments_author_id", "post_comments", ["author_id"])

    op.create_check_constraint(
        "ck_posts_like_count_non_negative", "posts", "like_count >= 0"
    )
    op.create_check_constraint(
        "ck_posts_comment_count_non_negative", "posts", "comment_count >= 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE posts DROP CONSTRAINT IF EXISTS ck_posts_comment_count_non_negative")
    op.execute("ALTER TABLE posts DROP CONSTRAINT IF EXISTS ck_posts_like_count_non_negative")
    op.execute("DROP INDEX IF EXISTS ix_post_comments_author_id")
    op.execute("DROP INDEX IF EXISTS ix_post_comments_post_created")
    op.execute("DROP TABLE IF EXISTS post_comments")
    op.execute("DROP INDEX IF EXISTS ix_post_likes_user_id")
    op.execute("DROP TABLE IF EXISTS post_likes")
