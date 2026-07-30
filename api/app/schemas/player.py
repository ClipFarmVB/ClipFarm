import uuid

from pydantic import BaseModel, ConfigDict


class PlayerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    jersey_number: int | None
    team_id: uuid.UUID | None
    photo_url: str | None


class PlayerCreate(BaseModel):
    name: str
    jersey_number: int | None = None
    team_id: uuid.UUID | None = None
