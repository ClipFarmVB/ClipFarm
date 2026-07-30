import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class CollectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    clip_count: int = 0
    created_at: datetime


class CollectionCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        if len(v) > 100:
            raise ValueError("name too long (max 100 characters)")
        return v


class CollectionRename(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        if len(v) > 100:
            raise ValueError("name too long (max 100 characters)")
        return v


class CollectionAddClip(BaseModel):
    clip_id: uuid.UUID
