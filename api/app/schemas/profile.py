import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProfileOut(BaseModel):
    """A user's public identity.

    Deliberately excludes `email` — it's the login credential and is not part of
    anyone's public profile, so it must not leak through a handle lookup.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str | None
    display_name: str | None
    bio: str | None
    avatar_url: str | None
    is_private: bool
    created_at: datetime


class MeOut(ProfileOut):
    """The caller's own profile. Adds fields only the owner should see.

    Adding a field here is safe; adding one to `ProfileOut` publishes it through
    the unauthenticated handle lookup. `email` sits here for exactly that reason
    and `test_profile_routes.py` pins the split.
    """

    email: str
    username_changed_at: datetime | None
    # Lets the frontend keep prompting a backfilled user to pick a real handle.
    username_is_generated: bool


class ProfileUpdate(BaseModel):
    """All fields optional — a PATCH may set any subset.

    `None` means "not supplied" rather than "clear it"; clearing a bio is done
    by sending an empty string, which keeps the common case unambiguous.
    """

    username: str | None = None
    display_name: str | None = Field(default=None, max_length=50)
    bio: str | None = Field(default=None, max_length=280)
    is_private: bool | None = None


class HandleAvailability(BaseModel):
    username: str
    available: bool
    reason: str | None = None
