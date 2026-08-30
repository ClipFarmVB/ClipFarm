"""CF-238: count players that already have no team (`team_id IS NULL`).

#241's fourth acceptance criterion — *existing orphan rows counted and dealt
with, or confirmed to be none* — is the half of CF-238 that the guard in
`update_player` cannot answer. The guard stops the next orphan from being
written; it says nothing about the rows that went through the hole while it was
open, and it is what makes those rows permanent:

    api/app/routers/players.py  _get_owned_player   404s on team_id IS NULL
    api/app/routers/players.py  list_players        filters orphans out
    api/app/routers/players.py                      has no DELETE route

`update_player` calls `_get_owned_player` first, so an already-orphaned player
404s on every PATCH and the team cannot be put back through the API. Nothing
else in `api/app` writes `players.team_id`. So an orphan that exists when this
lands stays orphaned until someone touches the database directly — which is why
the count has to be taken, not assumed.

`audit_cross_tenant_tags.py` (CF-234) reports an `orphan` bucket too, but only
for players that are actually tagged onto a clip: it starts `FROM clips`, so an
orphan nobody tagged is invisible to it. That is the majority shape here — a
player is orphaned by a PATCH, not by tagging — so it does not answer criterion
4 and this script exists alongside it rather than inside it.

READ ONLY. One SELECT, no writes — safe to point at the shared instance, unlike
anything under alembic/. Run it before merging #400, **from the api/
directory**: `Settings` loads `.env` relative to the working directory, so from
the repo root `database_url` falls back to its localhost default (CF-172's
failure shape), and against a local dev database with this schema that prints
"clean" — indistinguishable from a real all-clear. Hence the target banner:
every run says what it queried, so the answer can be read against the right
database rather than assumed.

    cd api && python scripts/count_orphan_players.py   # POSIX
    cd api; py scripts/count_orphan_players.py         # Windows

PowerShell has no inline env-var prefix, so `DATABASE_URL=... python ...` does
not work there; either rely on api/.env as above or set `$env:DATABASE_URL`
first.

**No shebang on purpose**, for the reason spelled out in
`audit_cross_tenant_tags.py`: the `py` launcher dispatches on one, and where
`python` on PATH is the Microsoft Store alias that breaks the documented
Windows command. Nothing here is executed directly.

Exit codes: 0 none found, 1 orphans found, 2 could not complete the check. 2 is
deliberately wider than "could not connect" — a missing table, a permissions
error and a malformed query all mean "this did not answer the question", and a
gate must never read any of them as clean.

What the report gives you is what deciding the remediation needs: which team
each orphan's clips belong to. `deal with` is a re-home, and re-homing needs a
destination team; a player whose clips all sit in one owner's games has an
obvious one, a player with no clips at all may simply be deleted. Picking the
destination is a human's call against real data, so this script stops at
reporting it and writes nothing.
"""
import asyncio
import os
import sys

from sqlalchemy import text

API_ROOT = os.path.join(os.path.dirname(__file__), "..")

# LEFT JOIN from players, not from clips: an orphan that was never tagged onto
# a clip is still an orphan, and it is the common shape — the row is orphaned
# by a PATCH, not by tagging. Starting from clips (what the CF-234 audit does)
# would count only the tagged subset and report a low number as the answer.
#
# The game owners are aggregated rather than joined per-row so one player is
# one line: the question each row has to answer is "whose data is this", and a
# player tagged across forty clips of one owner should not print forty times.
QUERY = text("""
    SELECT p.id                                        AS player_id,
           p.name                                      AS name,
           p.jersey_number                              AS jersey_number,
           p.created_at                                 AS created_at,
           count(c.id)                                  AS clip_count,
           -- FILTER, not array_remove(..., NULL): array_remove compares with
           -- `=`, which is never true for NULL, so it would leave the NULLs
           -- from the LEFT JOIN in and every untagged orphan would come back
           -- with a one-element owner list.
           array_agg(DISTINCT g.owner_id)
               FILTER (WHERE g.owner_id IS NOT NULL)     AS owner_ids
    FROM players p
    LEFT JOIN clips c ON c.player_id = p.id
    LEFT JOIN games g ON g.id = c.game_id
    WHERE p.team_id IS NULL
    GROUP BY p.id, p.name, p.jersey_number, p.created_at
    ORDER BY p.created_at, p.id
""")


def target(url) -> str:
    """Where this run actually pointed, credentials stripped.

    SQLAlchemy's own renderer rather than a hand-parsed host: `hide_password`
    is what makes this safe to print, and splitting on the last `@` drops the
    user info with it. A URL carrying no credentials has no `@` and renders
    whole, which is still the right answer.

    A copy of `audit_cross_tenant_tags.target`, on the same reasoning that one
    gives for not reusing `auto_migrate.database_host`: it only has to be
    legible, and a second caller is not a reason to couple two standalone
    scripts through a sys.path insert.
    """
    return url.render_as_string(hide_password=True).rsplit("@", 1)[-1]


def remediation(row) -> str:
    """What deciding this row's fate hangs on, in one phrase.

    Three cases, and they want different answers, so the report separates them
    rather than printing a count and leaving the reader to open a SQL client:

    * no clips — nothing references the row; deleting it is on the table.
    * one owner — that owner's teams are where it belongs; the re-home is
      unambiguous, which is the case worth spotting from the report alone.
    * several owners — the row was tagged across tenants before CF-234 closed
      that path, so re-homing it to either owner moves the other's data. Needs
      a person, and this is the shape that must not be summarised away.

    Keyed on `clip_count` first, not on the owner list: both an untagged row
    and a tagged row whose games somehow carry no owner arrive here with an
    empty `owner_ids`, and fusing them would tell someone it is safe to delete
    a player that four clips point at.

    A pure function so it can be tested without a database. It is the part of
    this script most likely to be quietly wrong, because a misfiled row still
    prints and still looks like an answer.
    """
    owners = row["owner_ids"] or []
    if not row["clip_count"]:
        return "unreferenced"
    if not owners:
        return f"{row['clip_count']} clip(s) with no game owner — inspect"
    if len(owners) == 1:
        return f"re-home to owner {owners[0]}"
    return f"CONTESTED — clips across {len(owners)} owners"


async def main() -> int:
    # Imported here, not at module scope, so importing this file has no side
    # effects: `app.database` builds `Settings` and the process-wide async
    # engine at import time, and the test module loads this file through
    # importlib to reach the two pure functions above. At module scope any
    # import-time failure in `app.config` would surface as a collection error
    # naming neither. The path insert moves with it — a test that wants a
    # string helper should not permanently reorder sys.path for the suite.
    sys.path.insert(0, API_ROOT)
    from app.database import AsyncSessionLocal, engine

    # Before the query, so it is on the record even when the run fails: an
    # exit 2 that names the host it could not reach is diagnosable, and an
    # exit 0 is only meaningful once you can see it was not localhost.
    # flush: stdout is block-buffered into a pipe while stderr is not, so
    # without this the failure line below overtakes the banner in exactly the
    # case the banner is for — someone capturing output to paste.
    print(f"querying {target(engine.url)}", flush=True)

    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(QUERY)).mappings().all()
    except Exception as exc:  # noqa: BLE001 — the reason matters more than the type
        print(f"could not query: {exc}", file=sys.stderr)
        return 2
    finally:
        await engine.dispose()

    if not rows:
        print("clean: no player has a null team_id — CF-238 criterion 4 is met")
        return 0

    print(f"{len(rows)} orphaned player(s) — team_id IS NULL:\n")
    for r in rows:
        jersey = "--" if r["jersey_number"] is None else f"#{r['jersey_number']}"
        print(
            f"  player={r['player_id']}  {jersey:<5} {r['name']!r}"
            f"  clips={r['clip_count']}  created={r['created_at']}"
            f"  → {remediation(r)}"
        )
    print(
        "\nThese rows are unreachable through the API: _get_owned_player 404s on "
        "team_id IS NULL and runs first, list_players filters them out, and "
        "players.py has no DELETE route. They need a team set directly in the "
        "database — the guard in #400 makes the condition permanent, it does "
        "not create it. See #241 acceptance criterion 4."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
