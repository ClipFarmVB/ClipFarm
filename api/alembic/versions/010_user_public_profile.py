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
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Frozen copy of the handle rules, as of this revision ─────────────────────
#
# Yes, this duplicates app/services/handles.py. That is the point.
#
# Importing the service would couple the whole migration chain to a module path:
# alembic's ScriptDirectory imports *every* file in versions/ just to read
# revision/down_revision and build the graph, so renaming or moving handles.py —
# or adding an import to it that fails in the migration context — would make
# `alembic upgrade head` raise at graph construction, on databases already past
# this revision where 010 would never run. The api container runs that command
# at startup, so a refactor touching no migration would crash-loop production.
#
# It would also make the data non-reproducible: replaying migrations on a fresh
# database after any rule change would generate different handles than the ones
# production actually has.
#
# A migration is a historical record, so a frozen copy cannot drift the way the
# earlier SQL reimplementation did — that version diverged because it was a
# second *live* implementation. If the rules change, this file keeps producing
# what it produced in August 2026, which is correct.
#
# api/tests/test_migration_010_backfill.py covers this copy directly rather than
# assuming it matches the service. That suite runs in CI (ci.yml, working-
# directory: api) but NOT in .hooks/pre-commit — see CLAUDE.md — so a local
# commit that breaks these rules passes the hook and fails on the PR.

_MIN_LENGTH = 3
_MAX_LENGTH = 30
_RESERVED = {
    "auth", "collections", "debug", "games", "login", "signup", "upload",
    "feed", "explore", "search", "settings", "notifications", "u",
    "corrections", "players", "clips", "health", "healthz", "monitoring",
    "api", "www", "static", "assets", "public", "favicon",
    "admin", "administrator", "root", "system", "support", "help", "billing",
    "security", "moderator", "mod", "staff", "team", "official", "clipfarm",
    "about", "terms", "privacy", "legal", "contact",
}


def _suggest(email: str, suffix: int | None = None) -> str:
    stem = re.sub(r"[^a-z0-9_]", "", (email or "").split("@")[0].lower())
    stem = re.sub(r"_+", "_", stem).strip("_")
    if len(stem) < _MIN_LENGTH:
        stem = f"player{stem}" if stem else "player"
    stem = stem[:_MAX_LENGTH].rstrip("_")
    if len(stem) < _MIN_LENGTH:
        stem = "player"

    if suffix is None and stem not in _RESERVED:
        return stem

    tail = str(suffix if suffix is not None else 1)
    return f"{stem[: _MAX_LENGTH - len(tail)].rstrip('_')}{tail}"


def _suggest_unique(email: str, taken: set) -> str:
    """First candidate not already in `taken` — which must include the handles
    assigned earlier in this same pass, not just those already in the table."""
    candidate = _suggest(email)
    if candidate not in taken:
        return candidate
    for n in range(2, 10_000):
        candidate = _suggest(email, n)
        if candidate not in taken:
            return candidate
    raise RuntimeError(f"Could not derive a free handle for {email!r}")


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
    # Marks a handle this backfill invented rather than one the user chose.
    # Without it the two are indistinguishable, and a generated handle would
    # both skip the "pick a username" prompt and burn the free first claim.
    op.add_column(
        "users",
        sa.Column(
            "username_is_generated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Backfill in Python via _suggest_unique() rather than in SQL.
    #
    # The SQL version numbered within a stem partition, which is not the same as
    # being unique: `alice@a.com` and `alice@b.com` produce `alice` and `alice2`,
    # and `alice2@c.com` produces `alice2` as well — all three draw from one
    # namespace. The CREATE UNIQUE INDEX below then aborted the migration, and
    # since the api runs `alembic upgrade head` at startup, that is a deploy-time
    # crash loop against production data.
    #
    # The rewrite also restores rules the SQL had dropped — reserved names
    # (`admin@…` must not become `/u/admin`), underscore normalization, and the
    # short-stem fallback.
    conn = op.get_bind()
    taken = {
        row[0]
        for row in conn.execute(
            sa.text("SELECT LOWER(username) FROM users WHERE username IS NOT NULL")
        )
    }
    rows = conn.execute(
        sa.text(
            "SELECT id, email FROM users WHERE username IS NULL ORDER BY created_at, id"
        )
    ).fetchall()

    for user_id, email in rows:
        handle = _suggest_unique(email or "", taken)
        taken.add(handle)
        conn.execute(
            sa.text(
                "UPDATE users SET username = :handle, username_is_generated = true "
                "WHERE id = :id"
            ),
            {"handle": handle, "id": user_id},
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
    op.drop_column("users", "username_is_generated")
    op.drop_column("users", "username_changed_at")
    op.drop_column("users", "is_private")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "bio")
    op.drop_column("users", "display_name")
    op.drop_column("users", "username")
