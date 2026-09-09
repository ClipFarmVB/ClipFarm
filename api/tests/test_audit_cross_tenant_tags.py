"""CF-234: the audit script's target banner.

The script's answer is only readable against the database it actually queried.
`Settings` loads `.env` relative to the working directory, so running from the
repo root instead of api/ silently falls back to the localhost default — and
against a local dev database with this schema that prints "clean", which looks
exactly like a real all-clear. The banner is what makes those two
distinguishable, so it is worth a test that it never leaks the password.

Follows the importlib pattern from test_auto_migrate.py, which loads a script
rather than a package module the same way.

`exec_module` runs the script's module body at *collection* time, which is only
safe because the script keeps `app.database` and its `sys.path` insert inside
`main()`. `target()` is a pure function over a sqlalchemy URL; standing up
`Settings` and the process-wide async engine to reach it would mean any
import-time failure in `app.config` surfaced here as a collection error naming
neither this test nor the real fault. `test_the_module_body_does_not_import_the_app`
pins that so the import cannot drift back up to module scope.
"""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy.engine import make_url  # noqa: E402

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_cross_tenant_tags.py"
_spec = importlib.util.spec_from_file_location("audit_cross_tenant_tags", _SCRIPT)
assert _spec and _spec.loader
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


def test_the_module_body_does_not_import_the_app():
    """Importing the script must not stand up the application.

    Asserted on the module's own namespace rather than on `sys.modules`, which
    would be answering a different question: by the time this runs, some other
    test in the suite has almost certainly imported `app.database` already, so
    a `sys.modules` check passes whether this script imported it or not. What
    `exec_module` binds into *this* module is the thing under test, and it is
    exactly what a module-level `from app.database import ...` would add.
    """
    leaked = [name for name in ("engine", "AsyncSessionLocal") if hasattr(audit, name)]
    assert not leaked, (
        f"{leaked} are bound at module scope, so importing the script now "
        "builds Settings and the async engine. Move the import back inside "
        "main() — this file loads the script to test a pure string helper."
    )

SUPABASE = "postgresql+asyncpg://postgres.abcdef:s3cret@aws-1-us-east-1.pooler.supabase.com:5432/postgres"
LOCAL = "postgresql+asyncpg://postgres:password@localhost:5432/clipfarm"


@pytest.mark.parametrize(
    "url,expected",
    [
        (SUPABASE, "aws-1-us-east-1.pooler.supabase.com:5432/postgres"),
        (LOCAL, "localhost:5432/clipfarm"),
        # No credentials, so no "@" to split on — the whole thing is the answer.
        ("postgresql+asyncpg:///clipfarm", "postgresql+asyncpg:///clipfarm"),
    ],
)
def test_target_names_the_database(url, expected):
    assert audit.target(make_url(url)) == expected


def test_the_two_cases_this_exists_to_separate_do_not_render_alike():
    """The whole point: a localhost fallback must not read like the real run."""
    assert audit.target(make_url(SUPABASE)) != audit.target(make_url(LOCAL))
    assert "localhost" in audit.target(make_url(LOCAL))


@pytest.mark.parametrize("url", [SUPABASE, LOCAL])
def test_the_password_never_reaches_the_banner(url):
    """It is printed to a console and pasted into PRs — it must be safe there.

    Asserting on the literal secret rather than on the presence of "***", so
    this fails if the renderer ever stops masking rather than passing on a
    substring that happens to survive.
    """
    rendered = audit.target(make_url(url))
    assert "s3cret" not in rendered
    assert "password" not in rendered


# ── which shape a suspect row is ─────────────────────────────────────────────
#
# `foreign` is the only shape that is a disclosure, and it is the number CF-237
# gets scoped from, so it must not absorb the two shapes that merely have no
# owner to compare against. Cheap to test and easy to get wrong: a misfiled row
# still prints, and still looks like an answer.

OWNER, OTHER, TEAM = "owner-uuid", "other-uuid", "team-uuid"


def _row(player_team_id, player_owner_id):
    return {
        "clip_id": "clip",
        "game_id": "game",
        "game_owner_id": OWNER,
        "player_id": "player",
        "player_team_id": player_team_id,
        "player_owner_id": player_owner_id,
    }


@pytest.mark.parametrize(
    "row,expected",
    [
        # The IDOR proper: a team that exists and belongs to someone else.
        (_row(TEAM, OTHER), "foreign"),
        # No team at all — nothing to compare against, not proof of a tenant.
        (_row(None, None), "orphan"),
        # team_id set, team row gone. The regression: on a `player_team_id is
        # None` split this fell through to `foreign` and was counted as a
        # confirmed cross-tenant disclosure with no second tenant in it.
        (_row(TEAM, None), "dangle"),
    ],
)
def test_classify_keys_on_the_owner_not_the_team_id(row, expected):
    assert audit.classify(row) == expected


def test_a_missing_team_is_not_counted_as_cross_tenant():
    """The finding this split exists to fix, stated as the property.

    Separate from the parametrized case above because that one would still
    pass against a classifier that returned `dangle` for everything; this
    pins the pair that has to come apart.
    """
    assert audit.classify(_row(TEAM, None)) != audit.classify(_row(TEAM, OTHER))
