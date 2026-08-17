"""CF-189: the boot guard that keeps `docker compose up` off a shared database.

Pure stdlib — no importorskip needed (the script imports nothing from `app`).
Run from the api/ dir: `cd api && pytest tests/test_auto_migrate.py`.
"""
import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "auto_migrate.py"
_spec = importlib.util.spec_from_file_location("auto_migrate", _SCRIPT)
assert _spec and _spec.loader
auto_migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auto_migrate)

SUPABASE = "postgresql+asyncpg://postgres:pw@aws-1-us-east-1.pooler.supabase.com:5432/postgres"
LOCAL_DB = "postgresql+asyncpg://postgres:postgres@db:5432/clipfarm"


@pytest.mark.parametrize(
    "url,expected",
    [
        (LOCAL_DB, "db"),
        (SUPABASE, "aws-1-us-east-1.pooler.supabase.com"),
        ("postgresql+asyncpg://u:p@LOCALHOST:5432/clipfarm", "localhost"),
        ("postgresql+asyncpg:///clipfarm", ""),  # unix socket — no host
    ],
)
def test_database_host(url, expected):
    assert auto_migrate.database_host(url) == expected


def test_local_hosts_are_local():
    for host in ("db", "localhost", "127.0.0.1", "::1", "host.docker.internal", ""):
        assert auto_migrate.is_local(host), host


def test_remote_host_is_not_local():
    assert not auto_migrate.is_local("aws-1-us-east-1.pooler.supabase.com")


def test_unparseable_url_is_treated_as_remote():
    """A URL we can't parse must not be assumed local — refusing is the safe side."""
    assert not auto_migrate.is_local(auto_migrate.database_host("postgresql+asyncpg://u:p@[bad:5432/db"))


def _run(monkeypatch, env, called):
    for key in ("DATABASE_URL", "ALEMBIC_ALLOW_REMOTE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(auto_migrate.subprocess, "call", lambda cmd: called.append(cmd) or 0)
    return auto_migrate.main()


def test_shared_db_is_not_migrated_on_boot(monkeypatch):
    """The acceptance criterion: a compose boot against Supabase runs no migration."""
    called = []
    assert _run(monkeypatch, {"DATABASE_URL": SUPABASE}, called) == 0
    assert called == []


def test_local_db_is_migrated_on_boot(monkeypatch):
    called = []
    assert _run(monkeypatch, {"DATABASE_URL": LOCAL_DB}, called) == 0
    assert called == [["alembic", "upgrade", "head"]]


def test_allow_remote_opts_back_in(monkeypatch):
    called = []
    env = {"DATABASE_URL": SUPABASE, "ALEMBIC_ALLOW_REMOTE": "1"}
    assert _run(monkeypatch, env, called) == 0
    assert called == [["alembic", "upgrade", "head"]]


def test_missing_database_url_skips(monkeypatch):
    called = []
    assert _run(monkeypatch, {}, called) == 0
    assert called == []


def test_alembic_failure_propagates(monkeypatch):
    """A failed local migration must still fail the boot, as `&&` always did."""
    monkeypatch.setenv("DATABASE_URL", LOCAL_DB)
    monkeypatch.delenv("ALEMBIC_ALLOW_REMOTE", raising=False)
    monkeypatch.setattr(auto_migrate.subprocess, "call", lambda cmd: 1)
    assert auto_migrate.main() == 1
