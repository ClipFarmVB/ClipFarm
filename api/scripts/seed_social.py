"""Populate a LOCAL database with enough to exercise the social stack.

Local only. Refuses to run against anything that isn't a local host, for the
same reason `api/scripts/auto_migrate.py` does — this writes fabricated users
and posts, and doing that to the shared Supabase instance would be difficult to
undo and confusing for everyone else.

    docker compose exec api python scripts/seed_social.py

Lives beside auto_migrate.py in api/scripts/ because that directory is
bind-mounted into the container — the repo-root scripts/ is COPYed at build
time, so edits there need a rebuild to take effect.

Creates three accounts, a game each, and clips at all three visibility tiers,
then posts and follows between them so the feed has something in it:

    alice   public account, posts public + followers
    bob     public account, posts public + followers, follows alice
    carol   PRIVATE account, posts public — following her needs approval

**You cannot sign in as these accounts, and trying is worse than a no-op.**
The api keys off the JWT `sub`, which for a real Supabase user is a random
UUID; the ids here are `uuid5` over the email so they stay stable across runs.
Those will never coincide. And because `users.email` is UNIQUE while
`_ensure_user_exists` upserts on the *id* index only, the first authenticated
request as `alice@local.test` hits a unique violation on `email` and 500s —
and keeps 500ing until the seeded row is deleted by hand, with an error that
points at auth rather than at this script.

So these accounts are for **anonymous reads and DEBUG-mode curl**, which is
what the visibility matrix actually needs. Exercise them with the post ids the
script prints. Seeding a signed-in account means taking the real Supabase user
id, which is a different job than this script does.
"""
import asyncio
import os
import sys
import uuid
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db", "postgres"}

# A short clip that is definitely reachable from a browser. Swap for one of your
# own R2 keys if you would rather look at real volleyball.
SAMPLE_VIDEO = (
    "https://commondatastorage.googleapis.com/gtv-videos-bucket/"
    "sample/ForBiggerBlazes.mp4"
)

ACCOUNTS = [
    ("alice", "Alice Ace", False),
    ("bob", "Bob Block", False),
    ("carol", "Carol Cross", True),   # private: follows need approval
]
TIERS = ("public", "followers", "private")
ACTIONS = ("spike", "serve", "dig", "set", "block")


def stable_id(email: str) -> uuid.UUID:
    """Same id every run, so re-seeding doesn't orphan the previous rows."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"clipfarm-seed:{email}")


def require_local(url: str) -> None:
    host = urlparse(url.replace("+asyncpg", "")).hostname or ""
    if host not in LOCAL_HOSTS:
        sys.exit(
            f"refusing to seed a non-local database (host: {host!r}).\n"
            "This writes fabricated accounts and posts. Point DATABASE_URL at "
            "the local db container first, for example "
            "postgresql+asyncpg://postgres:postgres@db:5432/clipfarm"
        )


async def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    require_local(url)

    engine = create_async_engine(url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        exists = (await db.execute(text("SELECT to_regclass('public.posts')"))).scalar()
        if not exists:
            sys.exit(
                "the `posts` table is missing — the api migrates on boot only "
                "against a local database. Bring the stack up first, or run "
                "`alembic upgrade head`."
            )

        ids: dict[str, uuid.UUID] = {}
        for handle, display, private in ACCOUNTS:
            email = f"{handle}@local.test"
            uid = ids[handle] = stable_id(email)
            await db.execute(
                text(
                    "INSERT INTO users (id,email,username,display_name,is_private,"
                    "username_is_generated,created_at) "
                    "VALUES (:i,:e,:u,:d,:p,false,now()) "
                    "ON CONFLICT (id) DO UPDATE SET username=EXCLUDED.username, "
                    "display_name=EXCLUDED.display_name, is_private=EXCLUDED.is_private"
                ),
                {"i": uid, "e": email, "u": handle, "d": display, "p": private},
            )

        # Start from a clean slate for the content so re-running doesn't stack
        # duplicate posts up. Users survive, because a Supabase login is keyed
        # to them.
        await db.execute(
            text("DELETE FROM games WHERE owner_id = ANY(:ids)"),
            {"ids": list(ids.values())},
        )

        posts = 0
        for handle, _display, _private in ACCOUNTS:
            uid = ids[handle]
            for tier in TIERS:
                gid = uuid.uuid4()
                await db.execute(
                    text(
                        "INSERT INTO games (id,owner_id,title,status,visibility,"
                        "condense_requested,progress,created_at) VALUES "
                        "(:i,:o,:t,'ready',CAST(:v AS visibility),false,1,now())"
                    ),
                    {"i": gid, "o": uid, "t": f"{handle}'s {tier} game", "v": tier},
                )
                # Several clips per game so the feed has enough to scroll.
                for n in range(4):
                    cid = uuid.uuid4()
                    await db.execute(
                        text(
                            "INSERT INTO clips (id,game_id,action_type,confidence,"
                            "highlight_score,clip_url,start_time,end_time,created_at) "
                            "VALUES (:i,:g,CAST(:a AS actiontype),:c,:h,:u,0,6,now())"
                        ),
                        {"i": cid, "g": gid, "a": ACTIONS[n % len(ACTIONS)],
                         "c": 0.7 + n / 20, "h": 0.5 + n / 10, "u": SAMPLE_VIDEO},
                    )
                    # A post can never be wider than its clip, so the tier here
                    # matches the game's — the same rule create_post enforces.
                    await db.execute(
                        text(
                            "INSERT INTO posts (id,author_id,clip_id,caption,visibility,"
                            "like_count,comment_count,created_at) VALUES "
                            "(:i,:a,:c,:cap,CAST(:v AS visibility),:l,:m,"
                            "now() - make_interval(mins => :n))"
                        ),
                        {"i": uuid.uuid4(), "a": uid, "c": cid,
                         "cap": f"{handle} — {ACTIONS[n % len(ACTIONS)]} ({tier})",
                         "v": tier, "l": n * 3, "m": n, "n": posts},
                    )
                    posts += 1

        # bob follows alice so his feed isn't just his own posts. Nobody follows
        # carol — her account is private, so that is a request to approve in the
        # UI, which is the more interesting thing to look at.
        #
        # Skipped entirely before CF-110's migration has run. This script lives
        # on the first PR of the stack and the follow graph arrives on the
        # third, so at 016 there is no `follows` table and no counter columns —
        # writing them unconditionally killed the whole seed on the first
        # INSERT, which is the one path this PR documents for local testing.
        has_follows = (
            await db.execute(text("SELECT to_regclass('public.follows')"))
        ).scalar() is not None
        if not has_follows:
            print(
                "note: no `follows` table yet (CF-110 is unmerged), so the "
                "follow edge is skipped. Profiles and posts are seeded; the "
                "feed needs the follow graph to have anything in it."
            )
        if has_follows:
            await db.execute(
                text("DELETE FROM follows WHERE follower_id = ANY(:ids)"),
                {"ids": list(ids.values())},
            )
            await db.execute(
                text(
                    "INSERT INTO follows (id,follower_id,followee_id,status,created_at) "
                    "VALUES (:i,:f,:t,'accepted',now())"
                ),
                {"i": uuid.uuid4(), "f": ids["bob"], "t": ids["alice"]},
            )
            # The counters arrive with the same migration as the table.
            await db.execute(
                text("UPDATE users SET follower_count=1 WHERE id=:i"), {"i": ids["alice"]}
            )
            await db.execute(
                text("UPDATE users SET following_count=1 WHERE id=:i"), {"i": ids["bob"]}
            )
        await db.commit()

    edge = "1 follow edge" if has_follows else "no follow edge (CF-110 unmerged)"
    print(f"seeded {len(ACCOUNTS)} accounts, {posts} posts, {edge}")
    print()
    for handle, display, private in ACCOUNTS:
        print(f"  {handle:<6} {display:<12} {'PRIVATE' if private else 'public':<8} "
              f"{handle}@local.test   id={ids[handle]}")
    if has_follows:
        print("\nbob follows alice. Nobody follows carol - following her creates "
              "a request she has to approve.")
    else:
        print("\nNo follow edges yet - the follow graph arrives with CF-110, so "
              "the feed has nothing in it until #191 merges.")
    # Says what the docstring says. These two disagreed: the header was
    # corrected to "you cannot sign in as these accounts" and this line, which
    # is the half an operator actually reads, still told them to create a
    # Supabase user with the same email. Following it produces a 500 loop —
    # `users.email` is UNIQUE and the upsert is on the id index, so the first
    # authenticated request collides — and the error points at auth rather than
    # at this script.
    print(
        "\nYou cannot sign in as these accounts: the ids are uuid5 over the "
        "email and a real Supabase `sub` is random, so they will never match. "
        "Creating a Supabase user with the same email makes it worse, not "
        "better (users.email is UNIQUE; the first request 500s until the "
        "seeded row is deleted by hand). Use them anonymously, or curl the api "
        "with DEBUG on — the post ids above are what you need."
    )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
