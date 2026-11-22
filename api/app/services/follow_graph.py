"""Follow-graph reads (CF-110).

Split from `services/access.py` on purpose: that module is synchronous and
database-free so the visibility predicates stay pure and unit-testable (asserted
by `test_access_module_stays_free_of_fastapi` and friends). Anything that needs
a session lives here instead, and callers pass the resolved boolean in.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.follow import Follow, FollowStatus
from app.models.visibility import Visibility


async def is_accepted_follower(
    db: AsyncSession, viewer_id: uuid.UUID | None, owner_id: uuid.UUID
) -> bool:
    """Whether `viewer_id` has an **accepted** edge to `owner_id`.

    Pending requests deliberately do not count — that is the whole point of the
    request flow: until the target approves, the requester sees exactly what a
    stranger sees.
    """
    if viewer_id is None or viewer_id == owner_id:
        return False
    row = await db.execute(
        select(Follow.id).where(
            Follow.follower_id == viewer_id,
            Follow.followee_id == owner_id,
            Follow.status == FollowStatus.accepted,
        )
    )
    return row.first() is not None


async def resolve_follow(
    db: AsyncSession,
    viewer_id: uuid.UUID | None,
    owner_id: uuid.UUID | None,
    *levels: Visibility | None,
) -> bool:
    """Resolve the edge only when one of `levels` actually depends on it.

    The single-object read paths call this before handing the answer to
    `access.can_view_*`. Taking the levels rather than just the game is what
    makes the short-circuit real: `followers` is the only tier the follow graph
    can change, so for a private or public object the query cannot affect the
    outcome and is not issued.

    Variadic because one edge is often spent on more than one level: a clip
    read asks about the clip's effective tier and its game's, and a post read
    asks about the post's tier and its clip's. For a post that holds only when
    the author is the game's owner, which `create_post` enforces and the schema
    does not — so `posts._load_for_read` passes both levels in one call when the
    two principals coincide and resolves each edge separately when they differ.
    """
    if viewer_id is None or owner_id is None or viewer_id == owner_id:
        return False
    if Visibility.followers not in levels:
        return False
    return await is_accepted_follower(db, viewer_id, owner_id)
