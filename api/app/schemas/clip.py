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


class ClipTagRequest(BaseModel):
    player_id: uuid.UUID


class ClipLabelsRequest(BaseModel):
    labels: list[str]  # e.g. ["spike", "dig"]


class ClipTrimRequest(BaseModel):
    start_delta: float  # seconds to add/subtract from start (negative = extend earlier)
    end_delta: float    # seconds to add/subtract from end (positive = extend later)


class ClipVisibilityRequest(BaseModel):
    """Who may read this clip (CF-109b).

    The clip's OWN tier, which overrides the game's rather than being bounded
    by it — `access._effective` resolves NULL as "inherit", and a set value
    wins. A public clip inside a private game is a supported and documented
    state: it is reachable by direct link and through a collection, while
    `GET /games/{id}/clips` still 404s the whole game. Publishing *that clip*
    is not the right to enumerate its game.

    There is no way back to NULL — "inherit from the game" is a state only the
    detector's original write produces. It costs nothing today, because no
    write path for a game's visibility exists, so every game is private and an
    explicit `private` and an inherited one are the same answer. If a game
    setter ever lands, this is the thing to revisit.
    """

    visibility: Visibility


class ClipDeleteRequest(BaseModel):
    clip_ids: list[uuid.UUID]
