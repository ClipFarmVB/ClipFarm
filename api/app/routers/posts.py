import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id, get_optional_user_id
from app.database import get_db
from app.models.clip import Clip
from app.models.game import Game
from app.models.post import Post
from app.models.user import User
from app.schemas.post import PostCreate, PostOut, PostUpdate
from app.services import access, engagement, post_read, post_view, profiles, storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/posts", tags=["posts"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]
ViewerId = Annotated[uuid.UUID | None, Depends(get_optional_user_id)]

# The ordering ladder lives in services/access (`at_most`), alongside the
# readability one. It used to be a `_RANK` dict here next to its own copy of the
# inherit rule — two copies of the same three-value order, in the one place the
# module's docstring names as the most expensive for them to drift.


def _serialize(
    post: Post,
    clip: Clip,
    author: User,
    *,
    r2_ready: bool | None = None,
    avatar_cache: dict[str, str | None] | None = None,
    failures: list[str] | None = None,
    viewer_has_liked: bool = False,
) -> PostOut:
    """Thin wrapper over the shared renderer.

    The body used to live here and a verbatim copy of it lived in
    `routers/feed.py`. `services/post_view.py` is now the single copy — see its
    module docstring for the three defects that only ever existed in whichever
    copy the tests did not reach.

    `r2_ready` is optional so the single-post handlers read like they always
    did; `list_user_posts` passes it once for the whole page rather than
    re-probing process-wide config per row.
    """
    return post_view.serialize(
        post,
        clip,
        author,
        r2_ready=storage.r2_configured() if r2_ready is None else r2_ready,
        avatar_cache=avatar_cache,
        failures=failures,
        viewer_has_liked=viewer_has_liked,
    )


# `_load_for_read` lived here until CF-113 needed it from a second router. It
# is `services/post_read.load_for_read` now — one copy of "may this viewer
# read this post", for the reason `services/profiles.py` gives about rules
# enforced in one router and not its neighbour.


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
    post, clip, author = await post_read.load_for_read(post_id, viewer_id, db)
    # One point lookup on the `post_likes` primary key; skipped for anonymous.
    liked = await engagement.viewer_has_liked(db, viewer_id, post.id)
    return _serialize(post, clip, author, viewer_has_liked=liked)


def user_posts_query(
    viewer_id: uuid.UUID | None, author_id: uuid.UUID, *, limit: int = 50
) -> Select:
    """One author's visible posts, newest first, as one statement.

    Module-level for the reason `feed.feed_query` and
    `engagement.comments_query` are: so the tests assert against the query the
    router runs rather than a stand-in. The third column is `viewer_has_liked`,
    one EXISTS per row on the `post_likes` primary key — a literal `false` and
    no subquery for an anonymous viewer (CF-113).
    """
    return (
        access.apply_post_visibility(
            select(
                Post,
                Clip,
                engagement.viewer_liked_column(viewer_id).label("viewer_has_liked"),
            ),
            viewer_id,
        )
        .where(Post.author_id == author_id)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(limit)
    )


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
    rows = (await db.execute(user_posts_query(viewer_id, author.id, limit=limit))).all()

    # Probed once for the page rather than per row: it reads five settings
    # fields for an answer that is process-wide and cannot change mid-response.
    #
    # The avatar cache matters more here than in the feed: this page is many
    # posts by *one* author, so without it a 50-post profile grid signed the
    # same avatar URL fifty times.
    r2_ready = storage.r2_configured()
    avatar_cache: dict[str, str | None] = {}
    # Same shape as `feed.get_feed`, and for the same reason: signing is pure
    # CPU inside an `async def`, this page is up to 100 cards — five times the
    # feed's — and it is reachable without a credential. Rendered off the loop,
    # and presign failures reported once per page rather than as a traceback
    # apiece, which under a broken bucket was up to 200 per request here.
    failures: list[str] = []

    def render() -> list[PostOut]:
        return [
            _serialize(
                post,
                clip,
                author,
                r2_ready=r2_ready,
                avatar_cache=avatar_cache,
                failures=failures,
                viewer_has_liked=liked,
            )
            for post, clip, liked in rows
        ]

    items = await run_in_threadpool(render)
    if failures:
        logger.warning(
            "Could not presign %d of this profile page's URLs (first: %s)",
            len(failures),
            failures[0],
        )
    return items


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
    # The author may have liked their own post; not the constant the
    # docstring in `post_view` warns would be "a wrong value forever".
    liked = await engagement.viewer_has_liked(db, user_id, post.id)
    return _serialize(post, clip, author, viewer_has_liked=liked)


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(post_id: uuid.UUID, user_id: UserId, db: DB):
    """Unpublish. The clip itself is untouched — only the post goes."""
    post = await db.get(Post, post_id)
    if post is None or post.author_id != user_id:
        raise HTTPException(status_code=404, detail="Post not found")
    await db.delete(post)
    await db.commit()
