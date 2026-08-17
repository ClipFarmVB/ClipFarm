#!/usr/bin/env python
"""CF-189: migrate on container boot only when DATABASE_URL points somewhere local.

The api container's start command used to run `alembic upgrade head`
unconditionally, so `docker compose up` on a branch carrying a new revision
advanced whatever DATABASE_URL happened to name — including the shared Supabase
instance. No deliberate action, no confirmation, no record of who did it.

This guard keeps the frictionless local flow (the `db` container still migrates
itself on boot) and puts the shared database back behind a deliberate step:

    DATABASE_URL=<pooler-uri> alembic upgrade head    # run once, on purpose

Escape hatch: set ALEMBIC_ALLOW_REMOTE=1 to let a boot migrate a non-local host
anyway. Nothing in this repo sets it — Render applies migrations once per deploy
via `preDeployCommand` (see render.yaml), and the VPS path in DEPLOY.md runs
them by hand.

Exits 0 when it skips, so the `&& uvicorn …` chain still starts the API.
"""
import os
import subprocess
import sys
from urllib.parse import urlsplit

# Hosts that mean "a database on this machine": the compose `db` service, the
# usual loopback spellings, and the Docker bridge back to the host.
LOCAL_HOSTS = frozenset({"db", "localhost", "127.0.0.1", "::1", "host.docker.internal"})

TRUTHY = frozenset({"1", "true", "yes", "on"})


def database_host(database_url: str) -> str:
    """Host portion of a SQLAlchemy URL, lowercased. Empty when there is none
    (a unix-socket or sqlite URL — both local by construction)."""
    try:
        return (urlsplit(database_url).hostname or "").lower()
    except ValueError:
        # Unparseable netloc (an unescaped credential, usually). Treat it as
        # remote: refusing to migrate is the safe side of the guess.
        return "<unparseable>"


def is_local(host: str) -> bool:
    return host == "" or host in LOCAL_HOSTS


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("auto_migrate: DATABASE_URL is unset - skipping migrations.", file=sys.stderr)
        return 0

    host = database_host(database_url)  # never print the URL itself — it carries the password
    allow_remote = os.environ.get("ALEMBIC_ALLOW_REMOTE", "").strip().lower() in TRUTHY

    if not is_local(host) and not allow_remote:
        print(
            f"auto_migrate: DATABASE_URL points at '{host}', which is not local - "
            "NOT running `alembic upgrade head` (CF-189).\n"
            "auto_migrate: applying a migration to a shared database is a deliberate step. "
            "Run `alembic upgrade head` yourself once, or set ALEMBIC_ALLOW_REMOTE=1 to "
            "migrate on boot anyway.",
            file=sys.stderr,
        )
        return 0

    why = "local host" if is_local(host) else "ALEMBIC_ALLOW_REMOTE is set"
    print(f"auto_migrate: running `alembic upgrade head` against '{host}' ({why}).", file=sys.stderr)
    return subprocess.call(["alembic", "upgrade", "head"])


if __name__ == "__main__":
    sys.exit(main())
