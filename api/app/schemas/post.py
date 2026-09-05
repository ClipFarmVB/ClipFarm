import uuid
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.models.visibility import Visibility


class _Author(Protocol):
    """What `PostAuthor.from_author` needs off a user row.

    A Protocol rather than importing `User`: this schema is imported by the
    model layer's consumers and a concrete import runs the cycle the other way.
    Annotating it at all is the point — `from_author` is the one
    security-relevant serializer here, and an unannotated parameter opts exactly
    it out of type checking, so a column rename would land silently.
    """

    id: uuid.UUID
    username: str | None
    display_name: str | None
    avatar_url: str | None
    username_is_generated: bool


class PostAuthor(BaseModel):
    """The author, denormalized into the post response.

    A feed card needs the handle and avatar to render; making the client fetch
    each author separately would be one request per card.

    Built through `from_author`, never `model_validate`, because a **generated**
    handle must not appear here. The CF-107 backfill derives handles from email
    local parts, so publishing one turns any response carrying it into an
    existence oracle keyed to a real address — which `get_profile` and
    `_findable_author` both refuse, and which this schema was reaching around.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str | None
    display_name: str | None
    avatar_url: str | None

    @classmethod
    def from_author(cls, author: _Author) -> "PostAuthor":
        """Withhold a handle its owner never chose.

        `create_post` does not require a claimed handle, so a backfilled user
        can post publicly and an anonymous read would have returned
        `johnsmith` — derived from `john.smith@…`. Nulled here rather than
        filtered in the router because every path that serializes a post goes
        through this schema, and the next one should not have to remember.

        Nulling rather than omitting keeps the response shape stable; clients
        already handle a null username, since a user who has not claimed a
        handle legitimately has none.
        """
        return cls(
            id=author.id,
            username=None if author.username_is_generated else author.username,
            display_name=author.display_name,
            avatar_url=author.avatar_url,
        )


class PostPlayback(BaseModel):
    """Everything needed to play the post, resolved from the clip.

    `clip_url` is the only source today. When CF-48 lands a per-game proxy,
    `proxy_url` is populated and the player prefers it, seeking to
    (start_time, end_time) instead of loading a per-clip file — which is why
    the times are here even though the file path alone would suffice now.
    """

    clip_url: str | None
    thumbnail_url: str | None
    proxy_url: str | None = None
    start_time: float
    end_time: float


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    clip_id: uuid.UUID
    caption: str | None
    visibility: Visibility
    like_count: int
    comment_count: int
    created_at: datetime
    author: PostAuthor
    playback: PostPlayback
    # Always False until CF-113 adds likes. Shipped now so the response shape
    # doesn't change under the client when it does, and so the feed can fill it
    # with one query for the whole page rather than one per card.
    viewer_has_liked: bool = False


class PostCreate(BaseModel):
    clip_id: uuid.UUID
    caption: str | None = Field(default=None, max_length=500)
    # Defaults to private, like everything else in the epic — publishing wider
    # than that is an explicit act.
    visibility: Visibility = Visibility.private


class PostUpdate(BaseModel):
    """Caption only.

    Visibility is deliberately not editable here: widening a post may require
    widening the clip it references, which is a decision the UI has to surface
    explicitly rather than something a PATCH quietly performs.

    **The two empties differ.** Omitting `caption`, or sending `null`, means
    "leave it alone"; sending `""` clears it. There is no way to say "clear it"
    with `null` because that is the same JSON a client sends for a field it
    isn't editing, and guessing between them would make a partial update
    destructive. `ProfileUpdate` uses the same convention, which is the other
    reason not to invert it here alone.
    """

    caption: str | None = Field(default=None, max_length=500)
