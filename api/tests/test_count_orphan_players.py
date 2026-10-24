"""CF-238: the orphan-count script's banner and its remediation classifier.

Same shape as `test_audit_cross_tenant_tags.py`, for the same reasons: the
script's answer is only readable against the database it actually queried
(`Settings` loads `.env` relative to the working directory, so a run from the
repo root falls back to localhost and prints "clean"), and the classifier is
the part a database cannot check here — a misfiled row still prints and still
looks like an answer.

`exec_module` runs the script's module body at *collection* time, which is only
safe because the script keeps `app.database` and its `sys.path` insert inside
`main()`. `test_the_module_body_does_not_import_the_app` pins that so the
import cannot drift back up to module scope.
"""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy.engine import make_url  # noqa: E402

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "count_orphan_players.py"
_spec = importlib.util.spec_from_file_location("count_orphan_players", _SCRIPT)
assert _spec and _spec.loader
count_orphans = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(count_orphans)


def test_the_module_body_does_not_import_the_app():
    """Importing the script must not stand up the application.

    Asserted on the module's own namespace rather than on `sys.modules`, which
    answers a different question: by the time this runs some other test has
    almost certainly imported `app.database` already, so a `sys.modules` check
    passes whether this script imported it or not.
    """
    leaked = [name for name in ("engine", "AsyncSessionLocal") if hasattr(count_orphans, name)]
    assert not leaked, (
        f"{leaked} are bound at module scope, so importing the script now "
        "builds Settings and the async engine. Move the import back inside "
        "main() — this file loads the script to test two pure functions."
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
    assert count_orphans.target(make_url(url)) == expected


def test_the_two_cases_this_exists_to_separate_do_not_render_alike():
    """A localhost fallback must not read like the real run.

    This is the whole reason the banner exists: an empty result set against a
    fresh local database prints the same "clean" as a real all-clear on the
    shared instance, and criterion 4 is answered by exactly that line.
    """
    assert count_orphans.target(make_url(SUPABASE)) != count_orphans.target(make_url(LOCAL))
    assert "localhost" in count_orphans.target(make_url(LOCAL))


@pytest.mark.parametrize("url", [SUPABASE, LOCAL])
def test_the_password_never_reaches_the_banner(url):
    """It is printed to a console and pasted into PRs — it must be safe there.

    Asserting on the literal secret rather than on the presence of "***", so
    this fails if the renderer ever stops masking rather than passing on a
    substring that happens to survive.
    """
    rendered = count_orphans.target(make_url(url))
    assert "s3cret" not in rendered
    assert "password" not in rendered


# ── what to do with each orphan ──────────────────────────────────────────────

OWNER, OTHER = "owner-uuid", "other-uuid"


def _row(clip_count, owner_ids):
    return {
        "player_id": "player",
        "name": "Sam",
        "jersey_number": 7,
        "created_at": "2026-01-01",
        "clip_count": clip_count,
        "owner_ids": owner_ids,
    }


def test_an_untagged_orphan_is_unreferenced():
    """Nothing points at the row, so deleting it is on the table.

    The common shape: a player is orphaned by a PATCH, not by tagging, so most
    orphans have no clips at all — and this is the case CF-234's audit cannot
    see, since it starts FROM clips.
    """
    assert count_orphans.remediation(_row(0, [])) == "unreferenced"


def test_a_single_owner_names_the_destination():
    assert count_orphans.remediation(_row(4, [OWNER])) == f"re-home to owner {OWNER}"


def test_clips_across_two_owners_are_flagged_not_re_homed():
    """The shape that must not be summarised away.

    A player tagged across two tenants (reachable before CF-234 closed the
    IDOR) has no correct destination — re-homing it to either owner moves the
    other's data. Asserted as the pair coming apart rather than on the string
    alone, because a classifier that returned "CONTESTED" for everything would
    still satisfy a single-case check.
    """
    contested = count_orphans.remediation(_row(4, [OWNER, OTHER]))
    assert "CONTESTED" in contested
    assert contested != count_orphans.remediation(_row(4, [OWNER]))


def test_the_clip_count_decides_unreferenced_not_the_owner_list():
    """`owner_ids` is empty for an untagged row too, so the two must not fuse.

    A row with clips whose games somehow carry no owner is a data fault worth
    seeing, not an unreferenced player — the report should not tell someone it
    is safe to delete a row that four clips point at.
    """
    assert count_orphans.remediation(_row(4, [])) != "unreferenced"
