import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.visibility import Visibility


class PostAuthor(BaseModel):
    """The author, denormalized into the post response.

    A feed card needs the handle and avatar to render; making the client fetch
    each author separately would be one request per card.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str | None
    display_name: str | None
    avatar_url: str | None


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
    """

    caption: str | None = Field(default=None, max_length=500)
