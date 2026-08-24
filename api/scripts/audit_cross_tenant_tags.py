#!/usr/bin/env python
"""CF-234: count clips carrying a player their game's owner does not own.

The IDOR in `tag_clip` is closed, but closing a write path does nothing about
the rows that went through it while it was open. A clip tagged with another
tenant's player keeps that foreign key, and both listing paths build
`player_name` from a bare id lookup with no ownership filter:

    api/app/routers/clips.py       list_clips
    api/app/routers/collections.py list_collection_clips

`list_clips` takes an *optional* viewer id, so that name is reachable without a
credential on any viewable game. This script answers the only question that
decides whether a backfill is needed: how many such rows exist.

READ ONLY. It issues one SELECT and writes nothing — safe to point at the
shared instance, unlike anything under alembic/. Run it before merging #239:

    DATABASE_URL=<pooler-uri> python api/scripts/audit_cross_tenant_tags.py

Exit codes: 0 clean, 1 rows found, 2 could not complete the check. 2 is
deliberately wider than "could not connect" — the handler catches a missing
table, a permissions error and a malformed query too, and a gate should read
all of those as "this did not answer the question", never as clean. The
non-zero exit on findings is so this can gate a deploy step later without
being reworded.

Two shapes count as suspect, matching what `players.get_owned_player` now
rejects on the write side:

* the player's team is owned by someone other than the game's owner — the IDOR
  proper, and the disclosure case.
* the player has no team at all (`team_id IS NULL`), so there is no owner to
  compare against. Not necessarily foreign, but not provably the game owner's
  either, and it is the same row shape the fix now refuses to write.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text  # noqa: E402

from app.database import AsyncSessionLocal, engine  # noqa: E402

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


async def main() -> int:
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

    orphans = [r for r in rows if r["player_team_id"] is None]
    foreign = [r for r in rows if r["player_team_id"] is not None]

    print(f"{len(rows)} suspect clip(s): {len(foreign)} cross-tenant, {len(orphans)} orphan/dangling\n")
    for r in rows:
        kind = "orphan " if r["player_team_id"] is None else "foreign"
        print(
            f"  {kind}  clip={r['clip_id']}  game={r['game_id']}"
            f"  game_owner={r['game_owner_id']}  player={r['player_id']}"
            f"  player_owner={r['player_owner_id']}"
        )
    print(
        "\nEach cross-tenant row discloses that player's name through "
        "GET /games/{id}/clips and the collection listing. See CF-237."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
