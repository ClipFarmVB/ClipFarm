"""Rendering and resolving public profiles (CF-107, lifted here by CF-110).

Both halves lived in `routers/profiles.py` while that router was the only thing
serving a profile. CF-110 added three more endpoints that return them — follower
lists, following lists, pending requests — and each one that reimplements this
is a place the two rules can diverge:

* **avatars must be presigned**, or the client gets a URL it cannot load while
  the API reports 200; and
* **generated handles must not resolve**, or the email-derived backfill becomes
  an existence oracle keyed to real addresses.

The second is the one that actually broke: `follows` resolved handles the public
profile route deliberately 404s, so `POST /users/johnsmith/follow` answered for
accounts that never chose to be findable. A rule enforced in one router and not
its neighbour is a rule that only looks enforced.
"""
import logging
from typing import TypeVar

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.profile import ProfileOut
from app.services import handles, storage

logger = logging.getLogger(__name__)

_Schema = TypeVar("_Schema", bound=ProfileOut)


def serialize(user: User, schema: type[_Schema]) -> _Schema:
    """Render a user, presigning the avatar.

    The R2 bucket is not public — every other media URL in the app is presigned
    before it reaches a browser (clips, thumbnails, condensed video). Returning
    the stored `r2_public_url` form here would hand the client a URL it cannot
    load, and the API would still report success.

    Presigned URLs also carry a fresh signature each time, so a re-uploaded
    avatar is picked up without a cache-busting query param — which is just as
    well, since a `?v=` baked into the stored value breaks the key extraction in
    `presign_from_stored_url`.

    The cost of that freshness is that the URL never repeats, so an unchanged
    avatar is re-fetched on every page load and sidebar mount. That was
    negligible at one avatar per page. It is less so now: a 50-row follower list
    presigns 50 objects per request, and CF-111's feed will do the same. The fix
    is a stable URL plus a version token taken from the object itself (an ETag,
    or an `avatar_updated_at` column), which buys freshness *and* caching —
    tracked as follow-up, and now with a second caller pushing on it.
    """
    out = schema.model_validate(user)
    if not user.avatar_url or not storage.r2_configured():
        return out
    try:
        return out.model_copy(
            update={"avatar_url": storage.presign_from_stored_url(user.avatar_url)}
        )
    except Exception:
        # A signing failure shouldn't take the whole profile down — the rest of
        # the response is still useful, and the avatar degrades to broken.
        logger.warning("Could not presign avatar for user %s", user.id, exc_info=True)
        return out


async def by_handle(handle: str, db: AsyncSession) -> User:
    """Resolve a **publicly findable** handle, or 404.

    Generated handles do not resolve. The CF-107 backfill derives them from
    email local parts, so answering for them turns any handle-keyed endpoint
    into an existence oracle keyed to real addresses — `john.smith@…` becomes
    `johnsmith` — for accounts that never chose to be findable, on a
    youth-sports product. The backfill exists to give the column a uniqueness
    guarantee, and it keeps that either way; being publicly resolvable is a
    separate thing the user opts into by picking a name.

    404 rather than 403, so it is indistinguishable from a handle nobody holds.
    """
    user = (
        await db.execute(
            select(User).where(func.lower(User.username) == handles.normalize(handle))
        )
    ).scalar_one_or_none()
    if user is None or user.username_is_generated:
        raise HTTPException(status_code=404, detail="Profile not found")
    return user
