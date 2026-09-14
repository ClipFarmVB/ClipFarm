import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.post import PostAuthor


class LikeStateOut(BaseModel):
    """What the client reconciles to after a like or an unlike.

    Both return this rather than a 204, deliberately: the client renders the
    like optimistically, and the count it guessed can be wrong under concurrent
    likes. The server's number is the one that matches the rows, so it comes
    back on every write and the card's "counts match reality" is something the
    user actually sees.
    """

    liked: bool
    like_count: int


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=500)

    @field_validator("body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # Stripped, and refused if nothing is left: `min_length` alone admits
        # a comment of spaces. Same posture as `create_post`'s caption.
        stripped = value.strip()
        if not stripped:
            raise ValueError("A comment needs some text.")
        return stripped


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    post_id: uuid.UUID
    body: str
    created_at: datetime
    # Built through `from_author`, never `model_validate` — the same rule
    # `PostOut` carries, for the same reason: a generated handle must not
    # appear here. A backfill account's comment stays; its handle does not.
    author: PostAuthor


class CommentPage(BaseModel):
    """One page of comments, newest first, plus the token for the next.

    Same shape as `FollowPage` and `FeedPage`: the cursor is opaque, so the
    client cannot rebuild it from the last row, and `next_cursor` is null on
    the last page.
    """

    items: list[CommentOut]
    next_cursor: str | None = None
