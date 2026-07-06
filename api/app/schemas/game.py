import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.game import GameStatus


class GameOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: GameStatus
    created_at: datetime
    clip_count: int | None = None
    condense_requested: bool = False
    condensed_video_url: str | None = None
    original_duration: float | None = None
    condensed_duration: float | None = None


class GameCreate(BaseModel):
    title: str


class GameRename(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title cannot be empty")
        if len(v) > 255:
            raise ValueError("title too long (max 255 characters)")
        return v
