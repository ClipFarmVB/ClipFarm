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


class ClipTagRequest(BaseModel):
    player_id: uuid.UUID


class ClipLabelsRequest(BaseModel):
    labels: list[str]  # e.g. ["spike", "dig"]


class ClipTrimRequest(BaseModel):
    start_delta: float  # seconds to add/subtract from start (negative = extend earlier)
    end_delta: float    # seconds to add/subtract from end (positive = extend later)


class ClipDeleteRequest(BaseModel):
    clip_ids: list[uuid.UUID]
