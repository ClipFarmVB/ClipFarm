"""Loading a post the viewer may read — the one copy (CF-113).

This was `routers/posts.py::_load_for_read`. It moved here the moment a second
router needed it: liking or commenting on a post is gated on being able to see
it, and the card says to reuse CF-108's helper rather than re-derive the rule.
The reason it is a *service* and not two router-private copies is the one
`services/profiles.py` gives for the handle lookup — a rule enforced in one
router and not its neighbour only looks enforced, and the follow graph found
that out twice in review.

`access.py` stays synchronous and database-free (CF-108 pinned that); this is
the async layer above it that resolves the follow edges and hands them in.
"""
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clip import Clip
from app.models.game import Game
from app.models.post import Post
from app.models.user import User
from app.services import access, follow_graph


async def load_for_read(
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
    #
    # The levels handed to the author lookup depend on whether the principals
    # coincide, which is what keeps the merged path free of a wasted query. When
    # they do, one lookup answers both tiers, so both are passed. When they
    # don't, the author's edge governs the post's tier alone — passing the
    # clip's as well would fire a `follows` lookup against the *author* whose
    # result `may_read` then discards, and a second one for the owner anyway.
    clip_level = access.effective(clip, game)
    same_principal = post.author_id == game.owner_id
    author_levels = (
        (post.visibility, clip_level) if same_principal else (post.visibility,)
    )
    follows_author = await follow_graph.resolve_follow(
        db, viewer_id, post.author_id, *author_levels
    )
    follows_owner = (
        follows_author
        if same_principal
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
