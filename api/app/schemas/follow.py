import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.follow import FollowStatus
from app.schemas.profile import ProfileOut


class FollowStateOut(BaseModel):
    """What the follow button renders.

    `status` is None when there's no edge at all. `following` is the flattened
    question the UI actually asks — `pending` is *not* following, and conflating
    the two is how a requester would appear to have access they don't have.
    """

    status: FollowStatus | None
    following: bool


class FollowOut(BaseModel):
    created_at: datetime
    user: ProfileOut


class FollowPage(BaseModel):
    """One page of edges, plus the token for the next.

    The cursor has to come back from the server now that it is opaque — the
    previous bare-list response worked only because the client could rebuild it
    from the last row's `created_at`, which is the same thing that made it miss
    the `id` tiebreaker. `next_cursor` is null on the last page.
    """

    items: list[FollowOut]
    next_cursor: str | None = None


class FollowRequestOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    requester: ProfileOut


class FollowRequestPage(BaseModel):
    """One page of pending requests.

    The same shape as `FollowPage` rather than the bare list this started as.
    The requests list is the one whose length nobody controls — `follows` has no
    rate limiting yet (CF-116), and follow-spam is the named vector — so it is
    the last endpoint that should hand back every row it finds.
    """

    items: list[FollowRequestOut]
    next_cursor: str | None = None
