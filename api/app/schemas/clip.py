import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.clip import ActionType
from app.models.visibility import Visibility


class ClipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    game_id: uuid.UUID
    player_id: uuid.UUID | None
    player_name: str | None = None
    action_type: ActionType
    confidence: float
    highlight_score: float | None = None
    start_time: float
    end_time: float
    clip_url: str
    thumbnail_url: str | None
    labels: list[str] = []
    created_at: datetime
    # False once the game's raw upload has been purged by the retention sweep
    # (CF-194) — the clip still plays, but it can no longer be re-cut, so the
    # UI disables trimming instead of firing a request that 400s.
    source_available: bool = True
    # The widest tier a *post* over this clip may take — the clip's own
    # visibility, or its game's where the clip inherits (NULL).
    #
    # Sent so the composer can grey out the tiers this clip cannot support with
    # the reason inline. Without it the only way to discover the ceiling was to
    # pick a tier and read the 409, and since nothing in the product can raise a
    # clip's visibility yet, that was a dead end rather than a step.
    #
    # Defaults to `private`, which is the fail-closed direction: a path that
    # forgets to resolve it offers less than it could, never more. It is a hint
    # for the UI either way — `create_post` re-derives it server-side.
    effective_visibility: Visibility = Visibility.private


class ClipPlaybackOut(BaseModel):
    """A freshly minted playback URL for a clip the caller already holds.

    Deliberately not a `ClipOut`: the caller has the row, and re-sending it
    would invite two copies of the same clip disagreeing about a field the
    refresh never looked at. Only what *expires* is here.

    Shaped after `PostPlayback` (schemas/post.py), which resolves the same pair
    of URLs for the feed. `start_time`/`end_time` are not repeated — the caller
    is refreshing a clip it holds, so it has them — and neither is `proxy_url`:
    when CF-48's per-game proxy lands, a proxy URL belongs here alongside the
    file, and adding it then is additive.

    `clip_url` is `str | None` rather than `str` because `_rewrite_urls` is
    typed that way and returns the stored URL untouched when R2 is not
    configured — the same reason `PostPlayback.clip_url` is optional.
    """

    clip_id: uuid.UUID
    clip_url: str | None
    thumbnail_url: str | None
    # Both None when R2 is unconfigured: the URL served then is the stored
    # public one, which has no signature and so no expiry. Reporting a
    # made-up expiry there would have a client refresh a URL that never dies,
    # and — worse — teach it that a non-null expiry means "signed".
    expires_in: int | None
    expires_at: datetime | None


class ClipTagRequest(BaseModel):
    player_id: uuid.UUID


class ClipLabelsRequest(BaseModel):
    labels: list[str]  # e.g. ["spike", "dig"]


class ClipTrimRequest(BaseModel):
    start_delta: float  # seconds to add/subtract from start (negative = extend earlier)
    end_delta: float    # seconds to add/subtract from end (positive = extend later)


class ClipDeleteRequest(BaseModel):
    clip_ids: list[uuid.UUID]
