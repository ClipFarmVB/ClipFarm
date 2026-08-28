"""CF-234: count clips carrying a player their game's owner does not own.

The IDOR in `tag_clip` is closed, but closing a write path does nothing about
the rows that went through it while it was open. A clip tagged with another
tenant's player keeps that foreign key, and every path that renders a name off
that id resolves it with a bare lookup and no ownership filter:

    api/app/routers/clips.py       list_clips
    api/app/routers/collections.py list_collection_clips
    api/app/routers/clips.py       download_clip

`list_clips` takes an *optional* viewer id, so that name is reachable without a
credential on any viewable game.

`download_clip` is the one worth stating separately, because it is not a
listing and was missed on the first sweep. It resolves the player at
clips.py:419 and hands the name to `clip_download_filename`, which
`presign_from_stored_url` writes into the URL's ResponseContentDisposition **in
cleartext** — so the name travels in a link that outlives the request. Its only
gate is `access.can_identify`, which asks whether the *game* is viewable and
says nothing about whether the player belongs to that game's owner: precisely
the assumption a cross-tenant tag breaks. Reachable by the game's owner today,
and by everyone the game is visible to once CF-109's visibility setter lands.

This script answers the only question that decides whether a backfill is
needed: how many such rows exist.

READ ONLY. It issues one SELECT and writes nothing — safe to point at the
shared instance, unlike anything under alembic/. Run it before merging #239,
**from the api/ directory** — `Settings` loads `.env` relative to the working
directory, so from the repo root `database_url` falls back to its localhost
default (CF-172's failure shape). Against a local dev database with this schema
that returns "clean", which is indistinguishable from a real all-clear. Hence
the target banner below: every run says what it queried, so the answer can be
read against the right database rather than assumed.

    cd api && python scripts/audit_cross_tenant_tags.py   # POSIX
    cd api; py scripts/audit_cross_tenant_tags.py         # Windows

PowerShell has no inline env-var prefix, so `DATABASE_URL=... python ...` does
not work there; either rely on api/.env as above or set `$env:DATABASE_URL`
first.

**No shebang on purpose.** The `py` launcher reads one and dispatches on it, so
`#!/usr/bin/env python` sends it hunting for a `python` on PATH — and where that
is the Microsoft Store alias rather than a real interpreter, `py <script>` dies
with "Python was not found" while `py -c` works fine. Nothing here is executed
directly: docker-compose.yml calls `python scripts/auto_migrate.py` with an
explicit interpreter, and so does every documented invocation of this script.
The line bought nothing and cost the documented Windows command.

Exit codes: 0 clean, 1 rows found, 2 could not complete the check. 2 is
deliberately wider than "could not connect" — the handler catches a missing
table, a permissions error and a malformed query too, and a gate should read
all of those as "this did not answer the question", never as clean. The
non-zero exit on findings is so this can gate a deploy step later without
being reworded.

Three shapes count as suspect, matching what `players.get_owned_player` now
rejects on the write side. Only the first is a disclosure; the other two are
reported apart from it so the cross-tenant count stays the number CF-237 can
be scoped from:

* `foreign` — the player's team is owned by someone other than the game's
  owner. The IDOR proper, and the disclosure case.
* `orphan` — the player has no team at all (`team_id IS NULL`), so there is no
  owner to compare against. Not necessarily foreign, but not provably the game
  owner's either, and it is the same row shape the fix now refuses to write.
* `dangle` — `team_id` is set and the team row is gone. Indistinguishable from
  `foreign` on a `team_id IS NULL` split, which is why the buckets below key on
  `player_owner_id` instead. It should not be reachable: `players.team_id`
  carries no ON DELETE clause and no route deletes a team, so a row here means
  something wrote around the schema.
"""
import asyncio
import os
import sys

from sqlalchemy import text

API_ROOT = os.path.join(os.path.dirname(__file__), "..")

# LEFT JOIN on teams, not INNER: an inner join would silently drop the orphan
# and dangling-team rows, which are half of what this is looking for.
QUERY = text("""
    SELECT c.id            AS clip_id,
           c.game_id       AS game_id,
           g.owner_id      AS game_owner_id,
           p.id            AS player_id,
           p.team_id       AS player_team_id,
           t.owner_id      AS player_owner_id
    FROM clips c
    JOIN games g   ON g.id = c.game_id
    JOIN players p ON p.id = c.player_id
    LEFT JOIN teams t ON t.id = p.team_id
    WHERE p.team_id IS NULL
       OR t.owner_id IS DISTINCT FROM g.owner_id
    ORDER BY c.game_id, c.id
""")


def target(url) -> str:
    """Where this run actually pointed, credentials stripped.

    SQLAlchemy's own renderer rather than a hand-parsed host: `hide_password`
    is what makes this safe to print, and splitting on the last `@` drops the
    user info with it. A URL carrying no credentials has no `@` and renders
    whole, which is still the right answer. Deliberately not reusing
    auto_migrate.database_host — that one backs a safety gate and has to
    resolve libpq's `?host=` override; this one only has to be legible, and a
    second caller is not a reason to couple two scripts.
    """
    return url.render_as_string(hide_password=True).rsplit("@", 1)[-1]


def classify(row) -> str:
    """Which of the three suspect shapes this row is.

    Keyed on `player_owner_id`, not on `player_team_id`. Only `foreign` has a
    second tenant in it, and only `foreign` is a disclosure — so it is the one
    number CF-237 gets scoped from, and it must not absorb rows that merely
    have nothing to compare against. A `team_id` pointing at a team row that is
    not there survives the LEFT JOIN with a NULL owner, so a `team_id IS NULL`
    split counted it as cross-tenant. The owner is what both unownable shapes
    share, and it is the same question `players.get_owned_player` asks on the
    write side.

    A separate function so the classification can be tested without a database;
    it is the part of this script most likely to be quietly wrong, because a
    misfiled row still prints and still looks like an answer.
    """
    if row["player_owner_id"] is not None:
        return "foreign"
    if row["player_team_id"] is None:
        return "orphan"
    # team_id set, team row absent. Named apart from `orphan` because the
    # remediation differs — a team that went away, not a player that never had
    # one — and because it should not be reachable at all: players.team_id
    # carries no ON DELETE clause and no route deletes a team, so a row here
    # means something wrote around the schema and is worth seeing on its own.
    return "dangle"


async def main() -> int:
    # Imported here, not at module scope, so that importing this file has no
    # side effects. `app.database` builds `Settings` and the process-wide async
    # engine at import time, and `test_audit_cross_tenant_tags.py` loads this
    # module through importlib to reach `target()` — a pure function over a
    # sqlalchemy URL that needs none of it. At module scope that test could not
    # be collected without standing up the application's configuration, so any
    # import-time failure in app.config (it already raises under
    # ENVIRONMENT=production with a malformed CORS_ORIGINS) turned a string
    # test into a collection error naming neither. Nothing is deferred for the
    # actual run: main() is the only caller, and it needs them on its first line.
    # The path insert moves with it for the same reason — a test that only wants
    # a string helper should not permanently reorder sys.path for the suite.
    sys.path.insert(0, API_ROOT)
    from app.database import AsyncSessionLocal, engine

    # Before the query, so it is on the record even when the run fails: an
    # exit 2 that names the host it could not reach is diagnosable, and an
    # exit 0 is only meaningful once you can see it was not localhost.
    # flush: stdout is block-buffered into a pipe while stderr is not, so
    # without this the "could not query" line below overtakes the banner in
    # exactly the case the banner is for — someone capturing output to paste.
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
        print("clean: no clip carries a player outside its game owner's teams")
        return 0

    kinds = [classify(r) for r in rows]
    foreign = kinds.count("foreign")

    # One count per shape rather than "N cross-tenant, M orphan/dangling": that
    # phrasing named three shapes in two numbers, so whichever of orphan and
    # dangle the reader cared about had to be worked out from the rows.
    print(
        f"{len(rows)} suspect clip(s): {foreign} cross-tenant, "
        f"{kinds.count('orphan')} orphan, {kinds.count('dangle')} dangling\n"
    )
    for kind, r in zip(kinds, rows):
        print(
            f"  {kind:<7}  clip={r['clip_id']}  game={r['game_id']}"
            f"  game_owner={r['game_owner_id']}  player={r['player_id']}"
            f"  player_owner={r['player_owner_id']}"
        )
    print(
        "\nEach cross-tenant row discloses that player's name through "
        "GET /games/{id}/clips, the collection listing, and the download "
        "filename on GET /clips/{id}/download — the last in cleartext inside a "
        "presigned URL that outlives the request. See CF-237."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
