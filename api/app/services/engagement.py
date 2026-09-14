"""`viewer_has_liked`, answered in SQL (CF-113).

`PostOut.viewer_has_liked` was shipped by CF-109 as a constant `False` so the
response shape would not change under the client when likes arrived, and so
the feed could fill it *"with one query for the whole page rather than one per
card."* This module is that one query: a correlated `EXISTS` against the
`post_likes` primary key, added as a column to any statement that already
selects `Post`. The feed's one-SELECT-per-page property, which
`test_the_page_costs_one_query` pins, survives because it is a column and not
a second round trip.

Anonymous viewers get a literal `false` and no subquery — they cannot have
liked anything, and an always-false EXISTS on every anonymous read is pure
cost (the same call `visible_games_filter` makes for the follow subquery).
"""
import uuid

from sqlalchemy import ColumnElement, false, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.post import Post
from app.models.post_like import PostLike


def viewer_liked_column(viewer_id: uuid.UUID | None) -> ColumnElement[bool]:
    """A boolean column for a statement that selects `Post`.

    Correlated to the outer `posts.id`, scoped to both ends of the row — a
    like *of this post* by *this viewer*, not "has this viewer liked anything".
    """
    if viewer_id is None:
        return false()
    return (
        select(PostLike.post_id)
        .where(PostLike.post_id == Post.id, PostLike.user_id == viewer_id)
        .exists()
    )


async def viewer_has_liked(
    db: AsyncSession, viewer_id: uuid.UUID | None, post_id: uuid.UUID
) -> bool:
    """The single-object form, for `get_post`: one point lookup on the PK."""
    if viewer_id is None:
        return False
    row = await db.execute(
        select(PostLike.post_id).where(
            PostLike.post_id == post_id, PostLike.user_id == viewer_id
        )
    )
    return row.first() is not None
