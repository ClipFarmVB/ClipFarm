import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id, get_optional_user_id
from app.database import get_db
from app.models.clip import Clip
from app.models.game import Game
from app.models.post import Post
from app.models.user import User
from app.schemas.post import PostAuthor, PostCreate, PostOut, PostPlayback, PostUpdate
from app.services import access, handles, storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/posts", tags=["posts"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]
ViewerId = Annotated[uuid.UUID | None, Depends(get_optional_user_id)]

# The ordering ladder lives in services/access (`at_most`), alongside the
# readability one. It used to be a `_RANK` dict here next to its own copy of the
# inherit rule — two copies of the same three-value order, in the one place the
# module's docstring names as the most expensive for them to drift.


def _serialize(post: Post, clip: Clip, author: User) -> PostOut:
    """Build the response, resolving playback from the clip at read time.

    Resolved per request rather than stored on the post so a trim (CF-52) or a
    re-materialized file is reflected without touching post rows.
    """
    if storage.r2_configured():
        clip_url = storage.presign_from_stored_url(clip.clip_url, expires_in=3600)
        thumb = (
            storage.presign_from_stored_url(clip.thumbnail_url, expires_in=3600)
            if clip.thumbnail_url
            else None
        )
    else:
        clip_url, thumb = clip.clip_url, clip.thumbnail_url

    return PostOut(
        id=post.id,
        clip_id=post.clip_id,
        caption=post.caption,
        visibility=post.visibility,
        like_count=post.like_count,
        comment_count=post.comment_count,
        created_at=post.created_at,
        author=PostAuthor.from_author(author),
        playback=PostPlayback(
            clip_url=clip_url,
            thumbnail_url=thumb,
            # CF-48 populates this; until then every post plays from its file.
            proxy_url=None,
            start_time=clip.start_time,
            end_time=clip.end_time,
        ),
    )


async def _load_for_read(
    post_id: uuid.UUID, viewer_id: uuid.UUID | None, db: AsyncSession
) -> tuple[Post, Clip, User]:
    """Fetch a post the viewer may read, or 404.

    Two gates, deliberately both: the post's own visibility, and the underlying
    clip's. A clip that goes private after being posted must take its post with
    it — otherwise the post keeps serving footage the owner has since withdrawn.
    """
    post = await db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    clip = await db.get(Clip, post.clip_id)
    game = await db.get(Game, clip.game_id) if clip else None
    author = await db.get(User, post.author_id)
    if clip is None or game is None or author is None:
        raise HTTPException(status_code=404, detail="Post not found")

    if not access.can_view_post(viewer_id, post, clip, game):
        # 404 not 403 — consistent with CF-108; a 403 confirms the id is real.
        raise HTTPException(status_code=404, detail="Post not found")

    return post, clip, author


async def _findable_author(username: str, db: AsyncSession) -> User:
    """Resolve a **publicly findable** handle, or 404.

    Two rules, both borrowed from CF-107's `get_profile` rather than reinvented:

    * `handles.normalize`, not `.lower()` — it also strips, so `" matt "`
      resolves here exactly as it does at `/u/{handle}`. Two endpoints
      disagreeing about whether a handle exists is its own small bug.
    * a **generated** handle 404s. The CF-107 backfill derives handles from
      email local parts, so answering for them turns this into an existence
      oracle keyed to real addresses — `john.smith@…` becomes `johnsmith` —
      for accounts that never chose to be findable. `get_profile` refuses them
      for that reason and this route reaches the same rows by another door.

    CF-110 lifts this into `services/profiles.py` once a third caller needs it;
    until then the rule is duplicated rather than absent, which is the safer of
    the two failure modes.
    """
    author = (
        await db.execute(
            select(User).where(func.lower(User.username) == handles.normalize(username))
        )
    ).scalar_one_or_none()
    if author is None or author.username_is_generated:
        raise HTTPException(status_code=404, detail="Profile not found")
    return author


@router.post("", response_model=PostOut, status_code=status.HTTP_201_CREATED)
async def create_post(body: PostCreate, user_id: UserId, db: DB):
    """Publish one of your own clips.

    Creates a row and **zero R2 objects** — the clip is already stored, and a
    post is a pointer to it.
    """
    clip = await db.get(Clip, body.clip_id)
    game = await db.get(Game, clip.game_id) if clip else None
    # Posting is an owner action, not a viewer one: you may only publish your
    # own footage, even if you can see someone else's.
    if clip is None or game is None or game.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Clip not found")

    clip_level = access.widest_allowed(clip, game)
    if not access.at_most(body.visibility, clip_level):
        # Refuse rather than silently widening the clip. Raising the clip's
        # visibility exposes the whole game's footage and has to be a separate,
        # deliberate act by the owner (CF-109: "never a silent side effect").
        #
        # **This is a UX guarantee, not the security boundary.** There is a
        # window between this check and the INSERT in which the clip can go
        # private, leaving a stored `visibility` wider than the clip allows.
        # That row is harmless because nothing trusts it: `can_view_post` and
        # `apply_post_visibility` both re-derive the clip's tier on every read,
        # and `test_a_public_post_over_a_private_clip_is_still_unreadable` pins
        # it. Anything that denormalizes `posts.visibility` into a feed query or
        # a cache — rather than joining the clip — breaks that property.
        #
        # The message names the ceiling but no longer prescribes a remedy: no
        # write path for a clip's or a game's visibility exists yet, so telling
        # the user to "change the clip's visibility first" pointed at something
        # the product cannot do. `ClipOut.effective_visibility` carries the same
        # ceiling to clients so the composer can grey out what it cannot offer
        # instead of letting the user find it here.
        raise HTTPException(
            status_code=409,
            detail=(
                f"This clip is {clip_level.value}, so it can only be posted to "
                f"{clip_level.value}. A {body.visibility.value} post would show "
                f"more of the footage than the clip itself does."
            ),
        )

    author = await db.get(User, user_id)
    if author is None:
        raise HTTPException(status_code=404, detail="User not found")

    post = Post(
        author_id=user_id,
        clip_id=clip.id,
        caption=(body.caption or "").strip() or None,
        visibility=body.visibility,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return _serialize(post, clip, author)


@router.get("/{post_id}", response_model=PostOut)
async def get_post(post_id: uuid.UUID, db: DB, viewer_id: ViewerId = None):
    post, clip, author = await _load_for_read(post_id, viewer_id, db)
    return _serialize(post, clip, author)


@router.get("", response_model=list[PostOut])
async def list_user_posts(
    db: DB,
    username: str,
    viewer_id: ViewerId = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    """Posts by one author — what a profile page renders.

    Not a feed: the feed (CF-111) spans everyone you follow and is cursor
    paginated. This is scoped to a single handle.

    Capped rather than paged, deliberately for now — a profile grid shows the
    recent ones and the card scopes it there. When it does need paging it wants
    CF-111's keyset cursor over `(created_at, id)`, not `offset`: OFFSET
    re-counts from the top on every page, so a post published mid-scroll
    duplicates one row at the boundary and skips another.
    """
    author = await _findable_author(username, db)

    # One joined query, not a fetch per post: the clip is needed to resolve
    # playback and the game to resolve inherited visibility, so loading them per
    # row would be 2N round trips for a page of N.
    #
    # Visibility is applied by apply_post_visibility, in SQL, *before* the
    # limit. Filtering after it would mean the limit counted rows this viewer
    # can't see — an author with 60 private posts then 20 public ones would hand
    # a stranger an empty page and no way to page past it.
    rows = (
        await db.execute(
            access.apply_post_visibility(select(Post, Clip), viewer_id)
            .where(Post.author_id == author.id)
            .order_by(Post.created_at.desc(), Post.id.desc())
            .limit(limit)
        )
    ).all()

    return [_serialize(post, clip, author) for post, clip in rows]


@router.patch("/{post_id}", response_model=PostOut)
async def update_post(post_id: uuid.UUID, body: PostUpdate, user_id: UserId, db: DB):
    """Edit the caption. Visibility is not editable here — see PostUpdate."""
    post = await db.get(Post, post_id)
    if post is None or post.author_id != user_id:
        raise HTTPException(status_code=404, detail="Post not found")

    # Load before committing: the previous order wrote the caption and *then*
    # decided whether to 404, so the client saw a failure for a write that had
    # already landed. create_post resolves its author first for the same reason.
    clip = await db.get(Clip, post.clip_id)
    author = await db.get(User, post.author_id)
    if clip is None or author is None:
        raise HTTPException(status_code=404, detail="Post not found")

    if body.caption is not None:
        post.caption = body.caption.strip() or None
    await db.commit()
    await db.refresh(post)
    return _serialize(post, clip, author)


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(post_id: uuid.UUID, user_id: UserId, db: DB):
    """Unpublish. The clip itself is untouched — only the post goes."""
    post = await db.get(Post, post_id)
    if post is None or post.author_id != user_id:
        raise HTTPException(status_code=404, detail="Post not found")
    await db.delete(post)
    await db.commit()
