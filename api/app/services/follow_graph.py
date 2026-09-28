"""Follow-graph reads (CF-110).

Split from `services/access.py` on purpose: that module is synchronous and
database-free so the visibility predicates stay pure and unit-testable (asserted
by `test_access_module_stays_free_of_fastapi` and friends). Anything that needs
a session lives here instead, and callers pass the resolved boolean in.
"""
import uuid

from sqlalchemy import Select, select
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

    Variadic because a post is gated on two levels — its own and its clip's
    effective one — and the author of a post is always the owner of the game
    behind it (`create_post` enforces that). One lookup answers both; asking
    twice was two round trips for one fact.
    """
    if viewer_id is None or owner_id is None or viewer_id == owner_id:
        return False
    if Visibility.followers not in levels:
        return False
    return await is_accepted_follower(db, viewer_id, owner_id)


def followed_author_ids(viewer_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    """Subquery: the ids `viewer_id` has an **accepted** edge to.

    Here rather than inline in the feed because "which accounts count as
    followed" is the question this module exists to answer, and it had drifted
    into a third copy — `access.accepted_follow_exists`, `is_accepted_follower`,
    and one more written out in the router. Only the first was pinned by a test,
    so dropping `status == accepted` from the router's copy changed real
    behaviour and the whole feed suite still passed.

    What that regression leaks is narrower than it sounds — the visibility
    ladder is unaffected, since both predicates independently require an
    accepted edge — but an author's *public* posts would appear in the feed of
    someone who had merely requested to follow them. One source, one place to
    pin it.
    """
    return select(Follow.followee_id).where(
        Follow.follower_id == viewer_id,
        Follow.status == FollowStatus.accepted,
    )
