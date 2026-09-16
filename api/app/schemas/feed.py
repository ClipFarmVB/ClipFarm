from pydantic import BaseModel

from app.schemas.post import PostOut


class FeedPage(BaseModel):
    """One page of the home feed.

    `next_cursor` is null on the last page — that is the client's stop signal,
    and it is set from whether an extra row was fetched rather than from
    `len(items) < limit`, which would stop one page early whenever a page
    happens to land exactly on the boundary.
    """

    items: list[PostOut]
    next_cursor: str | None = None
