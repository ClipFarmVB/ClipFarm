"""CF-189: the boot guard that keeps `docker compose up` off a shared database.

Pure stdlib — no importorskip needed (the script imports nothing from `app`).
Run from the api/ dir: `cd api && pytest tests/test_auto_migrate.py`.
"""
import importlib.util
import sys
import os
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "auto_migrate.py"
_spec = importlib.util.spec_from_file_location("auto_migrate", _SCRIPT)
assert _spec and _spec.loader
auto_migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auto_migrate)

SUPABASE_HOST = "aws-1-us-east-1.pooler.supabase.com"
SUPABASE = f"postgresql+asyncpg://postgres:pw@{SUPABASE_HOST}:5432/postgres"
LOCAL_DB = "postgresql+asyncpg://postgres:postgres@db:5432/clipfarm"


@pytest.mark.parametrize(
    "url,expected",
    [
        (LOCAL_DB, "db"),
        (SUPABASE, "aws-1-us-east-1.pooler.supabase.com"),
        ("postgresql+asyncpg://u:p@LOCALHOST:5432/clipfarm", "localhost"),
        ("postgresql+asyncpg:///clipfarm", ""),  # unix socket — no host
        # libpq `?host=` overrides an empty netloc host (asyncpg/psycopg honour it)
        ("postgresql+asyncpg://u:p@/db?host=" + SUPABASE_HOST, SUPABASE_HOST),
        ("postgresql+asyncpg://u:p@/db?host=/var/run/postgresql", "/var/run/postgresql"),
    ],
)
def test_database_host(url, expected):
    assert auto_migrate.database_host(url) == expected


def test_host_query_param_pointing_remote_is_not_local():
    """`?host=<remote>` with an empty netloc must not read as a local socket."""
    url = "postgresql+asyncpg://u:p@/db?host=aws-1-us-east-1.pooler.supabase.com"
    assert not auto_migrate.is_local(auto_migrate.database_host(url))


def test_unix_socket_directory_host_is_local():
    """`?host=/var/run/postgresql` is a local socket dir, not a remote host."""
    assert auto_migrate.is_local("/var/run/postgresql")


def test_local_hosts_are_local():
    for host in ("db", "localhost", "127.0.0.1", "::1", ""):
        assert auto_migrate.is_local(host), host


def test_host_docker_internal_is_not_local():
    """It resolves to whatever the host has on that port — an `ssh -L` tunnel to
    Supabase would otherwise be migrated while the guard reported "local"."""
    assert not auto_migrate.is_local("host.docker.internal")


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
    # Pin the URL to the env var: resolve_database_url() prefers app.config,
    # whose Settings has a localhost default, so an unset DATABASE_URL would
    # otherwise resolve to that default instead of to "no URL configured".
    monkeypatch.setattr(
        auto_migrate, "resolve_database_url",
        lambda: os.environ.get("DATABASE_URL", ""),
    )
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
    monkeypatch.delenv("ALEMBIC_ALLOW_REMOTE", raising=False)
    monkeypatch.setattr(auto_migrate, "resolve_database_url", lambda: LOCAL_DB)
    monkeypatch.setattr(auto_migrate.subprocess, "call", lambda cmd: 1)
    assert auto_migrate.main() == 1


def test_guard_reads_the_same_url_alembic_will_use(monkeypatch):
    """alembic/env.py migrates `settings.database_url`. If the guard decided
    from a different value, it could permit a migration against a host it never
    inspected — so resolve_database_url must go through app.config."""
    settings = pytest.importorskip("app.config").settings
    monkeypatch.setattr(settings, "database_url", SUPABASE)
    assert auto_migrate.resolve_database_url() == SUPABASE
    assert not auto_migrate.is_local(auto_migrate.database_host(SUPABASE))


def test_container_invocation_reads_app_config_not_environ(tmp_path):
    """Reproduce the real container invocation and prove the app.config read is
    live, not dead.

    The container runs `python scripts/auto_migrate.py`, which seeds sys.path
    with scripts/ -- not the api root -- so `from app.config import settings`
    would raise and fall back to os.environ unless the script fixes its own
    path. That fallback is invisible when the two sources agree, so this forces
    them to DISAGREE and reads which one won off the log line:

      .env names a remote host, DATABASE_URL is unset in the environment.
        app.config live       -> reads .env  -> "not local", names the host
        dead, environ fallback -> reads ""    -> "no database URL configured"

    Both exit 0 (nothing is migrated either way), so no alembic stub is needed
    and the test runs on every platform.
    """
    import subprocess

    script = Path(auto_migrate.__file__).resolve()  # absolute, so cwd can differ
    remote = "aws-1-us-east-1.pooler.supabase.com"
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql+asyncpg://u:p@" + remote + ":5432/postgres" + chr(10))

    env = {k: v for k, v in os.environ.items()
           if k not in ("DATABASE_URL", "ALEMBIC_ALLOW_REMOTE")}

    result = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, env=env,
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr, result.stderr
    # The remote host in stderr proves app.config (the .env) was the source;
    # the os.environ fallback would have seen no URL and said so instead.
    assert remote in result.stderr, (
        "app.config path was not read -- the script fell back to os.environ."
        + chr(10) + result.stderr
    )
