"""Likes and comments (CF-113).

One router for both halves because they share everything that matters: the
gate (you may like or comment on exactly the posts you may read, through
`post_read.load_for_read` — the card says to reuse CF-108's rule, not restate
it), the counter helper, the response idioms, and the `social_enabled` switch.
No prefix, because `DELETE /comments/{id}` is not under `/posts`.

**Every counter write is shaped like `follows.py`'s, and for its reasons.**
A like is an `INSERT ... ON CONFLICT DO NOTHING` whose rowcount says whether
it landed; the counter moves only when it did. An unlike is a `DELETE` gated
the same way. A comment delete is a conditional `UPDATE ... WHERE deleted_at
IS NULL`, so a double-tap matches nothing the second time and decrements
nothing. Decrements are floored with `GREATEST` in SQL so the CHECKs migration
020 added to `posts` fire for a *new* bug and never wedge a revocation on
drift a cascade left behind — `_adjust_counts`' docstring has the full
argument.

**Lock order.** Every mutation here touches at most one `posts` row and locks
it last, after the child row, so there is no id-ordering to do: the cycle
that bit `_adjust_counts` needed two counter rows in one transaction.

**Nothing reads an ORM attribute after `rollback()`.** Ids are captured as
locals before any statement that can fail — the expiry `follows.py` learned
about the hard way.

**Unthrottled.** Comment creation and the anonymous comment list are the two
surfaces this adds; comment spam is the CF-116 vector, recorded here as
`access.py` records the follow one.
"""
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import Select, delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.auth import get_current_user_id, get_optional_user_id
from app.database import get_db
from app.models.post import Post
from app.models.post_comment import PostComment
from app.models.post_like import PostLike
from app.models.user import User
from app.schemas.engagement import CommentCreate, CommentOut, CommentPage, LikeStateOut
from app.schemas.post import PostAuthor
from app.services import cursors, post_read, post_view, profiles, storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["engagement"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]
ViewerId = Annotated[uuid.UUID | None, Depends(get_optional_user_id)]

DEFAULT_PAGE = 50


async def _bump(
    db: AsyncSession, post_id: uuid.UUID, column: InstrumentedAttribute, delta: int
) -> int:
    """Move one `posts` counter by `delta`, in the caller's transaction, and
    return the new value.

    `UPDATE ... SET x = x + n`, never read-modify-write, so two concurrent
    likes cannot both read the same value and lose one. Decrements are floored
    at zero in SQL for the reason `follows._adjust_counts` gives: the CHECK
    stays a backstop for every other writer, but as the only guard it would
    turn a drifted counter into an unlike or a delete that can never commit.
    """
    expr = func.greatest(column + delta, 0) if delta < 0 else column + delta
    result = await db.execute(
        update(Post).where(Post.id == post_id).values({column.key: expr}).returning(column)
    )
    value = result.scalar_one_or_none()
    if value is None:
        # The post went away between the gate and the write.
        raise HTTPException(status_code=404, detail="Post not found")
    return value


# ── likes ────────────────────────────────────────────────────────────────────


@router.post("/posts/{post_id}/like", response_model=LikeStateOut)
async def like_post(post_id: uuid.UUID, user_id: UserId, db: DB):
    """Like, idempotently.

    Liking twice yields one like — the primary key is the arbiter, and the
    counter moves only for the tap that inserted a row. The loser of a race
    (or a double-tap) is answered with the state that exists, not an error.
    """
    post, _clip, _author = await post_read.load_for_read(post_id, user_id, db)
    post_id = post.id  # a plain local; nothing below reads the ORM row

    landed = await db.execute(
        pg_insert(PostLike)
        .values(post_id=post_id, user_id=user_id)
        .on_conflict_do_nothing(index_elements=[PostLike.post_id, PostLike.user_id])
    )
    if landed.rowcount == 1:
        count = await _bump(db, post_id, Post.like_count, +1)
        await db.commit()
        return LikeStateOut(liked=True, like_count=count)

    # Already liked. Answer for the row that exists, and keep the transaction
    # clean — nothing was written.
    count = (
        await db.execute(select(Post.like_count).where(Post.id == post_id))
    ).scalar_one_or_none()
    await db.rollback()
    if count is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return LikeStateOut(liked=True, like_count=count)


@router.delete("/posts/{post_id}/like", response_model=LikeStateOut)
async def unlike_post(post_id: uuid.UUID, user_id: UserId, db: DB):
    """Unlike, idempotently. `200` with the count rather than `204`, so the
    client can reconcile — see `LikeStateOut`.

    Gated on viewability like the like is, on purpose: a 200 carrying a count
    for a post the viewer cannot see would be the existence oracle 404-not-403
    exists to prevent. The cost is that a like on a post you have since lost
    access to is stranded; that is CF-116's reconciliation, recorded here.
    """
    post, _clip, _author = await post_read.load_for_read(post_id, user_id, db)
    post_id = post.id
    current = post.like_count

    removed = await db.execute(
        delete(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == user_id)
    )
    if removed.rowcount == 1:
        current = await _bump(db, post_id, Post.like_count, -1)
    await db.commit()
    return LikeStateOut(liked=False, like_count=current)


# ── comments ─────────────────────────────────────────────────────────────────


def comments_query(
    post_id: uuid.UUID, *, cursor: str | None = None, limit: int = DEFAULT_PAGE
) -> Select:
    """One page of live comments on a post, newest first, as one statement.

    Module-level for the reason `feed.feed_query` is: so the tests assert
    against the query the router runs rather than a stand-in.
    """
    q = (
        select(PostComment, User)
        .join(User, PostComment.author_id == User.id)
        # Only the columns `PostAuthor` renders, and a raise rather than a
        # deferred load for the rest — the page is rendered off the loop.
        .options(post_view.AUTHOR_COLUMNS)
        .where(PostComment.post_id == post_id, PostComment.deleted_at.is_(None))
        .order_by(PostComment.created_at.desc(), PostComment.id.desc())
        # One extra row, discarded by `cursors.split_page`.
        .limit(limit + 1)
    )
    if cursor is not None:
        # `is not None`, not truthiness — `?cursor=` is malformed, and the
        # decoder says so (the CF-111 contract).
        created_at, comment_id = cursors.decode(cursor)
        q = q.where(tuple_(PostComment.created_at, PostComment.id) < (created_at, comment_id))
    return q


def _render_comment(
    comment: PostComment,
    author: User,
    *,
    r2_ready: bool,
    avatar_cache: dict[str, str | None] | None = None,
    failures: list[str] | None = None,
) -> CommentOut:
    # `from_author`, never `model_validate`: a generated handle is withheld and
    # the comment stays. Withholding the *row* would be the edge lists' rule
    # (`_findable`), which is for lists of users; this is authored content,
    # and dropping it would make `comment_count` disagree with the visible
    # list on every post such an account commented on.
    rendered = PostAuthor.from_author(author)
    url = rendered.avatar_url
    if url is not None and avatar_cache is not None:
        if url not in avatar_cache:
            avatar_cache[url] = profiles.presign_avatar(
                url, r2_ready=r2_ready, failures=failures
            )
        signed = avatar_cache[url]
    else:
        signed = profiles.presign_avatar(url, r2_ready=r2_ready, failures=failures)
    return CommentOut(
        id=comment.id,
        post_id=comment.post_id,
        body=comment.body,
        created_at=comment.created_at,
        author=rendered.model_copy(update={"avatar_url": signed}),
    )


@router.get("/posts/{post_id}/comments", response_model=CommentPage)
async def list_comments(
    post_id: uuid.UUID,
    db: DB,
    viewer_id: ViewerId = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = DEFAULT_PAGE,
):
    """Comments on a post the viewer may read. Anonymous allowed, as `get_post`
    is: a public post's comments are as public as the post."""
    await post_read.load_for_read(post_id, viewer_id, db)  # the gate; 404 if not

    rows = (await db.execute(comments_query(post_id, cursor=cursor, limit=limit))).all()
    page, has_more = cursors.split_page(rows, limit)

    # Rendered off the loop, one signature per distinct avatar, one warning
    # per page — the feed's shape, for the feed's reasons.
    r2_ready = storage.r2_configured()
    avatar_cache: dict[str, str | None] = {}
    failures: list[str] = []

    def render() -> list[CommentOut]:
        return [
            _render_comment(
                comment, author, r2_ready=r2_ready, avatar_cache=avatar_cache, failures=failures
            )
            for comment, author in page
        ]

    items = await run_in_threadpool(render)
    if failures:
        logger.warning(
            "Could not presign %d of this comment page's avatars (first: %s)",
            len(failures),
            failures[0],
        )
    return CommentPage(
        items=items,
        next_cursor=cursors.encode(page[-1][0].created_at, page[-1][0].id)
        if (has_more and page)
        else None,
    )


@router.post(
    "/posts/{post_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_comment(post_id: uuid.UUID, body: CommentCreate, user_id: UserId, db: DB):
    post, _clip, _post_author = await post_read.load_for_read(post_id, user_id, db)
    post_id = post.id

    author = await db.get(User, user_id)
    if author is None:
        raise HTTPException(status_code=404, detail="User not found")

    comment = PostComment(post_id=post_id, author_id=user_id, body=body.body)
    db.add(comment)
    try:
        # `_bump`'s UPDATE autoflushes the INSERT, so a post deleted between
        # the gate and here surfaces as the FK violation inside this guard —
        # the same reason `follow_user` keeps its counter update inside its
        # `try`. Everything read after the rollback is a local.
        await _bump(db, post_id, Post.comment_count, +1)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Post not found")
    await db.refresh(comment)
    return _render_comment(comment, author, r2_ready=storage.r2_configured())


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(comment_id: uuid.UUID, user_id: UserId, db: DB):
    """Author **or** post owner — the card's rule, and the cheapest useful
    moderation tool: a post's author can clear their own thread.

    Not gated on viewability, on purpose: an author may remove their own words
    from a post they can no longer see, and the post's author can always see
    it. "Post owner" is `post.author_id`, which the card names; it coincides
    with the game's owner today because `create_post` refuses to publish
    someone else's footage.
    """
    comment = await db.get(PostComment, comment_id)
    if comment is None or comment.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Comment not found")
    post = await db.get(Post, comment.post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    if user_id not in (comment.author_id, post.author_id):
        # 404 not 403, as everywhere: a 403 confirms the id is real.
        raise HTTPException(status_code=404, detail="Comment not found")
    comment_id, post_id = comment.id, post.id

    hidden = await db.execute(
        update(PostComment)
        .where(PostComment.id == comment_id, PostComment.deleted_at.is_(None))
        .values(deleted_at=func.now())
    )
    # A second tap, or a race with the other principal, matches nothing —
    # and decrements nothing.
    if hidden.rowcount == 1:
        await _bump(db, post_id, Post.comment_count, -1)
    await db.commit()
