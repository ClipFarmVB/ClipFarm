import base64
import binascii
import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.database import get_db
from app.models.clip import Clip
from app.models.post import Post
from app.models.user import User
from app.schemas.feed import FeedPage
from app.schemas.post import PostAuthor, PostOut, PostPlayback
from app.services import access, follow_graph, storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feed", tags=["feed"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]

DEFAULT_PAGE = 20


def _encode_cursor(post: Post) -> str:
    """Opaque cursor over the sort key.

    Keyset, not OFFSET (epic decision 5): OFFSET re-counts from the top on every
    page, so a post inserted while someone is scrolling shifts every subsequent
    page by one — duplicating a row at the boundary and skipping another. The
    key is the full `(created_at, id)` pair because `created_at` alone is not
    unique: two posts in the same millisecond would let one hide behind the
    other forever.

    Base64 so it reads as an opaque token rather than an invitation to
    hand-craft one; it is not a security boundary, and the query is
    visibility-filtered regardless of what a caller puts here.
    """
    raw = f"{post.created_at.isoformat()}|{post.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Opaque token back to the sort key, or 400.

    The timestamp must carry an offset. asyncpg does **not** raise when a naive
    datetime meets a `timestamptz` column — measured, it returns the same rows
    as the aware equivalent, because it reinterprets naive input as UTC. So an
    unchecked naive cursor doesn't fail, it silently pages from the wrong
    instant whenever the client meant a local time.

    Matches `follows._decode_cursor`, which had the check while this didn't —
    two sibling cursors disagreeing about what counts as malformed is its own
    small bug.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, _, post_id = raw.partition("|")
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            raise ValueError("cursor timestamp must be timezone-aware")
        return parsed, uuid.UUID(post_id)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid cursor")


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
    """
    q = (
        access.apply_post_visibility(select(Post, Clip, User), user_id)
        .join(User, Post.author_id == User.id)
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
        # One extra row, discarded before serializing — that's how has_more is
        # known without a second COUNT query over the same filtered set.
        .limit(limit + 1)
    )
    if cursor:
        created_at, post_id = _decode_cursor(cursor)
        # Row-value comparison, matching the ORDER BY exactly. Written as a
        # tuple rather than `created_at < x OR (created_at = x AND id < y)` so
        # Postgres can use the composite index rather than an OR of two ranges.
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
    has_more = len(rows) > limit
    rows = rows[:limit]

    items: list[PostOut] = []
    for post, clip, author in rows:
        if storage.r2_configured():
            clip_url = storage.presign_from_stored_url(clip.clip_url, expires_in=3600)
            thumb = (
                storage.presign_from_stored_url(clip.thumbnail_url, expires_in=3600)
                if clip.thumbnail_url
                else None
            )
        else:
            clip_url, thumb = clip.clip_url, clip.thumbnail_url

        items.append(
            PostOut(
                id=post.id,
                clip_id=post.clip_id,
                caption=post.caption,
                visibility=post.visibility,
                like_count=post.like_count,
                comment_count=post.comment_count,
                created_at=post.created_at,
                author=PostAuthor.model_validate(author),
                playback=PostPlayback(
                    clip_url=clip_url,
                    thumbnail_url=thumb,
                    proxy_url=None,  # CF-48 populates this
                    start_time=clip.start_time,
                    end_time=clip.end_time,
                    action_type=clip.action_type.value,
                    highlight_score=clip.highlight_score,
                ),
                # False until CF-113 adds likes. The field ships now so the
                # response shape doesn't change under the client later, and so
                # the feed never becomes a per-post like lookup — CF-113 fills
                # it with one extra query for the whole page, not one per card.
                viewer_has_liked=False,
            )
        )

    return FeedPage(
        items=items,
        next_cursor=_encode_cursor(rows[-1][0]) if (has_more and rows) else None,
    )
