import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlayerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    jersey_number: int | None
    team_id: uuid.UUID | None
    photo_url: str | None


class PlayerCreate(BaseModel):
    # Lengths mirror the columns (String(255), String(2048)), so an oversized
    # value is a 422 here rather than a database error at commit. The
    # min_length=1 on photo_url refuses "": it would be stored as-is and render
    # as <img src="">, and null is the way to leave a photo unset.
    name: str = Field(min_length=1, max_length=255)
    jersey_number: int | None = None
    # Nullable in the type only so create_player can answer a missing team with
    # its own 400; PlayerUpdate does not repeat this.
    team_id: uuid.UUID | None = None
    photo_url: str | None = Field(default=None, min_length=1, max_length=2048)


class PlayerUpdate(BaseModel):
    """PATCH body — every field may be omitted, and only sent fields change.

    The router reads this with `model_dump(exclude_unset=True)`, so "omitted"
    and "sent as null" stay distinguishable, and this schema gives them
    different meanings per field. That departs from `PostUpdate` and
    `ProfileUpdate`, where null means "leave it alone":

    - `jersey_number`, `photo_url`: null **clears** the value. Both columns are
      nullable and "not known yet" is a legitimate state for a player. An
      empty `photo_url` is a 422, so null is the only way to clear it.
    - `name`, `team_id`: null is a **422**. `name` is NOT NULL, and a null
      `team_id` would orphan the player — `get_owned_player` rejects a teamless
      row, so every later PATCH would 404 and the team could never be put back
      (CF-238). Rejecting it here, at the boundary, is what lets the router
      drop its runtime guard (CF-253).

    The types still read `| None` so that omission works; the validator below
    is what refuses an explicit null. Validators do not run on defaults, so an
    omitted field never reaches it.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    jersey_number: int | None = None
    team_id: uuid.UUID | None = None
    photo_url: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("name", "team_id")
    @classmethod
    def not_null_when_sent(cls, v: object) -> object:
        if v is None:
            raise ValueError("cannot be null; omit the field to leave it unchanged")
        return v
