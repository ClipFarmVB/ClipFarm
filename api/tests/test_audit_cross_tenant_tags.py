"""CF-234: the audit script's target banner.

The script's answer is only readable against the database it actually queried.
`Settings` loads `.env` relative to the working directory, so running from the
repo root instead of api/ silently falls back to the localhost default — and
against a local dev database with this schema that prints "clean", which looks
exactly like a real all-clear. The banner is what makes those two
distinguishable, so it is worth a test that it never leaks the password.

Follows the importlib pattern from test_auto_migrate.py, which loads a script
rather than a package module the same way.
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
