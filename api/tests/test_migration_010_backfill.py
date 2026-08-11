"""The frozen handle rules inside migration 010.

The migration carries its own copy of the suggestion logic rather than importing
`app.services.handles` — see the long comment in the revision for why (alembic
imports every file in versions/ to build the graph, so importing app code
couples the whole chain to a module path, and it would make replayed migrations
generate different data than production has).

A frozen copy needs its own tests: `test_handles.py` exercises the service, and
nothing there touches this. The properties asserted are the ones whose absence
aborted the migration or produced handles the app would reject.

Loaded by path — `versions/` isn't a package and the filename isn't importable.
"""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("alembic")
pytest.importorskip("sqlalchemy")

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "010_user_public_profile.py"
)


def _load():
    """Import the revision module by path — versions/ isn't a package."""
    spec = importlib.util.spec_from_file_location("_migration_010", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = _load()


def _assign(emails):
    """Exactly what upgrade() does over the rows it selects."""
    taken: set = set()
    out = []
    for email in emails:
        handle = m._suggest_unique(email, taken)
        taken.add(handle)
        out.append(handle)
    return out


def test_shared_stem_is_disambiguated():
    assert _assign(["alice@a.com", "alice@b.com"]) == ["alice", "alice2"]


def test_a_stem_that_looks_like_a_suffix_does_not_collide():
    """The bug that aborted CREATE UNIQUE INDEX: per-stem numbering can't see
    that `alice2@c.com` wants the name generated for the second `alice`."""
    assigned = _assign(["alice@a.com", "alice@b.com", "alice2@c.com"])
    assert len(set(assigned)) == 3, f"duplicate handles: {assigned}"


def test_the_generic_fallback_does_not_collide_either():
    assigned = _assign(["a@x.com", "!!!@y.com", "player2@z.com"])
    assert len(set(assigned)) == 3, f"duplicate handles: {assigned}"


def test_handles_already_in_the_table_are_respected():
    assert m._suggest_unique("alice@a.com", {"alice"}) != "alice"


def test_reserved_names_are_never_assigned():
    """`admin@yourgym.com` must not end up owning /u/admin."""
    for handle in _assign([f"{name}@x.com" for name in sorted(m._RESERVED)]):
        assert handle not in m._RESERVED


def test_generated_handles_satisfy_the_apps_own_validator():
    """The frozen copy and the live validator must still agree on what is a
    legal handle — a user holding a name `validate()` rejects cannot re-save
    their own profile. This is the one cross-check worth keeping: the rules may
    diverge over time, but not in a way that makes existing data illegal."""
    handles = pytest.importorskip("app.services.handles")
    emails = [
        "alice@a.com", "alice@b.com", "alice2@c.com", "admin@x.com",
        "_matt_@x.com", "m@x.com", "!!!@y.com", "player2@z.com",
        ("z" * 60) + "@example.com", "UPPER.Case+tag@x.com", "a__b@x.com",
        ("q" * 29 + "_") + "@x.com", "", "@x.com",
    ]
    for handle in _assign(emails):
        assert handles.validate(handle) == handle, f"invalid generated handle: {handle!r}"
