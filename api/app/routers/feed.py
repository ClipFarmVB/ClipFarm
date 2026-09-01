import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import Select, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.auth import get_current_user_id
from app.database import get_db
from app.models.clip import Clip
from app.models.post import Post
from app.models.user import User
from app.schemas.feed import FeedPage
from app.schemas.post import PostOut
from app.services import access, cursors, follow_graph, post_view, storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feed", tags=["feed"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]

DEFAULT_PAGE = 20


def feed_query(
    user_id: uuid.UUID, *, cursor: str | None = None, limit: int = DEFAULT_PAGE
) -> Select:
    """The feed page, as one statement.

    Extracted from the endpoint so the tests can assert against **the query the
    router actually runs**. They previously built a stand-in that omitted the
    author filter, the `User` join, the ordering and the limit — so every
    assertion was really exercising `access.apply_post_visibility`, which CF-109
    and CF-110 already own and already cover.

    That gap was not theoretical. `test_only_accepted_follows_widen_the_feed`
    asserted `'accepted'` appeared in the compiled SQL, and it does — inside
    CF-110's visibility EXISTS, which has nothing to do with which authors are
    in the feed. Dropping `status == accepted` from the author filter changed
    real behaviour and all 22 feed tests still passed.

    `apply_post_visibility` owns the clips/games joins and both gates — the
    post's own tier and the clip's effective one. Since CF-110 it also resolves
    `followers` with an EXISTS against `follows`, so a followers-tier post from
    someone you follow appears without a second query and without any
    Python-side filtering after the LIMIT.

    **`user_id` is not optional, and passing `None` is a bug rather than an
    anonymous read.** It compiles to `posts.author_id IS NULL OR posts.author_id
    IN (SELECT ... WHERE follows.follower_id IS NULL)` — a predicate no row can
    satisfy, so the query returns nothing, forever, with no error. Three tests
    used to assert against exactly that dead statement. The route is
    authenticated so the case cannot arise through the API; CF-114's explore
    feed is the anonymous-capable consumer and needs its own query rather than
    this one handed a `None`.
    """
    q = (
        access.apply_post_visibility(select(Post, Clip, User), user_id)
        .join(User, Post.author_id == User.id)
        # Only the columns `PostAuthor` renders. Without this the whole `User`
        # row is materialized per card — `hashed_password` and `email` included,
        # 20 times a page, on the hottest read path in the app, where any
        # `repr()` in an error path or a Sentry breadcrumb can pick them up
        # (`init_sentry` is active in production). `ProfileOut`'s own docstring
        # treats email as a credential that must not leak; the cheapest way to
        # honour that is not to load it.
        .options(
            load_only(
                User.id,
                User.username,
                User.display_name,
                User.avatar_url,
                User.username_is_generated,
            )
        )
        # Authors whose posts may appear: accepted edges, plus self. The rule
        # comes from follow_graph rather than being restated here — see
        # followed_author_ids for why that matters.
        .where(
            or_(
                Post.author_id == user_id,
                Post.author_id.in_(follow_graph.followed_author_ids(user_id)),
            )
        )
        .order_by(Post.created_at.desc(), Post.id.desc())
        # One extra row, discarded by `cursors.split_page`, which is where the
        # +1 and the `has_more` that reads it are explained together.
        .limit(limit + 1)
    )
    if cursor is not None:
        # `is not None`, not truthiness: `?cursor=` used to skip the decoder and
        # silently restart from page 1, so a client emitting an empty template
        # value scrolled forever without advancing. The decoder already rejects
        # "" as malformed and a test pinned that — the router just never asked.
        created_at, post_id = cursors.decode(cursor)
        # Row-value comparison, matching the ORDER BY exactly. Written as a
        # tuple rather than `created_at < x OR (created_at = x AND id < y)` so
        # Postgres can use the composite index (migration 019) rather than an OR
        # of two ranges.
        q = q.where(tuple_(Post.created_at, Post.id) < (created_at, post_id))
    return q


@router.get("", response_model=FeedPage)
async def get_feed(
    db: DB,
    user_id: UserId,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = DEFAULT_PAGE,
):
    """Posts from the accounts you follow, plus your own, newest first.

    **Fan-out-on-read** (epic decision 4): the feed is computed by querying at
    read time rather than maintained as a per-user timeline table. At this scale
    that is the trivially-correct option — a precomputed timeline needs
    backfilling on every new follow, invalidation on every delete or visibility
    change, and a reconciliation story for all three. Revisit when the query,
    not the theory, gets slow.

    **Empty state:** a user who follows nobody still sees their own posts. The
    client falls back to explore (CF-114) when this comes back empty.
    """
    rows = (await db.execute(feed_query(user_id, cursor=cursor, limit=limit))).all()
    page, has_more = cursors.split_page(rows, limit)

    # Signing is pure CPU and this is an `async def`, so doing it inline blocks
    # the event loop for every concurrent request. Measured with the repo's own
    # `_BOTO_CONFIG`: a full page is ~16 ms — two clip URLs plus an avatar per
    # card, 60 signings. It is that cheap only because `storage._client()` is
    # `lru_cache`d; the same page against an uncached client measures ~364 ms,
    # which is what this would cost if that decorator were ever dropped. Off the
    # loop either way, since the handler has nothing else to await meanwhile.
    r2_ready = storage.r2_configured()

    def render() -> list[PostOut]:
        return [
            post_view.serialize(post, clip, author, r2_ready=r2_ready)
            for post, clip, author in page
        ]

    items = await run_in_threadpool(render)

    return FeedPage(
        items=items,
        next_cursor=cursors.encode(page[-1][0].created_at, page[-1][0].id)
        if (has_more and page)
        else None,
    )
