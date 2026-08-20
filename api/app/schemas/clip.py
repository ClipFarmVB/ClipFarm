import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.clip import ActionType


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


class ClipTagRequest(BaseModel):
    player_id: uuid.UUID


class ClipLabelsRequest(BaseModel):
    labels: list[str]  # e.g. ["spike", "dig"]


class ClipTrimRequest(BaseModel):
    start_delta: float  # seconds to add/subtract from start (negative = extend earlier)
    end_delta: float    # seconds to add/subtract from end (positive = extend later)


class ClipDeleteRequest(BaseModel):
    clip_ids: list[uuid.UUID]
