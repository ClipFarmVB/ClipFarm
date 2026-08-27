import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id, get_optional_user_id
from app.database import get_db
from app.models.clip import Clip
from app.models.game import Game
from app.models.post import Post
from app.models.user import User
from app.schemas.post import PostAuthor, PostCreate, PostOut, PostPlayback, PostUpdate
from app.services import access, follow_graph, profiles, storage

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

    **A presigned URL outlives the revocation the read path enforces.** The
    signature is valid for an hour and is bearer authority on the object: once
    it has been handed out, deleting the post, deleting the clip, or narrowing
    either one's visibility stops the *API* serving it and does nothing to the
    URL. So "delete the clip and the post 404s" is a true statement about this
    service and not about the footage, and the honest window is up to an hour.

    Not shortened here, because the tradeoff is real in both directions: the
    feed (CF-111/CF-112) holds a page of these across a scroll session, so a
    short expiry trades a narrower revocation window for playback that dies
    mid-scroll — and re-presigning on failure just moves the same problem. The
    actual fix is the one CF-112's review already named: a stable URL through
    an endpoint that re-checks visibility per request, with the object private.
    That is a storage change, not a posts one.

    Recorded rather than quietly accepted so that whoever writes the takedown
    path (CF-116) knows the guarantee they are inheriting is "within the hour",
    and can decide whether that is good enough for a moderation action.
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

    # One lookup when the two principals coincide, which is every post today —
    # create_post refuses to publish footage you don't own. Resolved separately
    # when they don't, rather than assuming: the author's edge decides the
    # post's tier and the owner's decides the clip's, and answering the second
    # question with the first one's result is how a future ownership transfer
    # would quietly hand someone else's footage to the wrong follower.
    #
    # resolve_follow skips the query entirely unless a tier is `followers`, so
    # the common path still costs nothing.
    clip_level = access.effective(clip, game)
    follows_author = await follow_graph.resolve_follow(
        db, viewer_id, post.author_id, post.visibility, clip_level
    )
    follows_owner = (
        follows_author
        if post.author_id == game.owner_id
        else await follow_graph.resolve_follow(db, viewer_id, game.owner_id, clip_level)
    )
    if not access.can_view_post(
        viewer_id,
        post,
        clip,
        game,
        viewer_follows_author=follows_author,
        viewer_follows_owner=follows_owner,
    ):
        # 404 not 403 — consistent with CF-108; a 403 confirms the id is real.
        raise HTTPException(status_code=404, detail="Post not found")

    return post, clip, author


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
    # Shared resolver: normalizes like every other handle route and 404s a
    # generated handle, so the email-derived backfill can't be probed here.
    author = await profiles.by_handle(username, db)

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
