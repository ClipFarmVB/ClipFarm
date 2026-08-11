import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.database import get_db
from app.models.user import User
from app.schemas.profile import HandleAvailability, MeOut, ProfileOut, ProfileUpdate
from app.services import handles, storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["profiles"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]

# A handle may be changed once per this window. Renaming is legitimate, but
# rapid churn is how someone frees a handle and impersonates its former holder.
USERNAME_CHANGE_COOLDOWN = timedelta(days=30)

# Avatars are small images; the 2 GB video cap is meaningless here.
MAX_AVATAR_BYTES = 2 * 1024 * 1024
ALLOWED_AVATAR_TYPES = {"image/jpeg", "image/png", "image/webp"}


async def _by_handle(handle: str, db: AsyncSession) -> User:
    """Look a user up by handle, case-insensitively (matches the unique index)."""
    result = await db.execute(
        select(User).where(func.lower(User.username) == handles.normalize(handle))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return user


async def _handle_taken(handle: str, db: AsyncSession, *, excluding: uuid.UUID) -> bool:
    result = await db.execute(
        select(User.id).where(
            func.lower(User.username) == handle,
            User.id != excluding,
        )
    )
    return result.first() is not None


@router.get("/me", response_model=MeOut)
async def get_me(user_id: UserId, db: DB):
    """The caller's own profile, including fields not exposed publicly.

    `username` is null until one is claimed — the frontend uses that to decide
    whether to show the onboarding step.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/handle-available", response_model=HandleAvailability)
async def check_handle(username: str, user_id: UserId, db: DB):
    """Live availability check for the claim/rename form.

    Returns 200 with `available: false` rather than an error status — this is a
    form affordance, and a rejected handle isn't an exceptional condition.
    """
    try:
        candidate = handles.validate(username)
    except handles.HandleError as exc:
        return HandleAvailability(
            username=handles.normalize(username), available=False, reason=str(exc)
        )

    if await _handle_taken(candidate, db, excluding=user_id):
        return HandleAvailability(
            username=candidate, available=False, reason="That username is taken"
        )
    return HandleAvailability(username=candidate, available=True)


@router.patch("/me", response_model=MeOut)
async def update_me(body: ProfileUpdate, user_id: UserId, db: DB):
    """Update the caller's profile. Any subset of fields may be supplied."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if body.username is not None:
        try:
            candidate = handles.validate(body.username)
        except handles.HandleError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        if candidate != (user.username or ""):
            # Claiming a first handle is free; changing an existing one is rate
            # limited. `username_changed_at` stays null on the initial claim so
            # a new user isn't locked out of fixing a typo for 30 days.
            #
            # A generated handle (migration 010) counts as *not yet claimed*: the
            # user never chose it, so replacing it is the free first claim, not a
            # rename. Without this the backfilled cohort — everyone who predates
            # CF-107 — silently starts one step into the cooldown for a name they
            # were assigned.
            is_claim = not user.username or user.username_is_generated

            if not is_claim and user.username_changed_at:
                next_allowed = user.username_changed_at + USERNAME_CHANGE_COOLDOWN
                if datetime.now(timezone.utc) < next_allowed:
                    raise HTTPException(
                        status_code=429,
                        detail=(
                            "Username was changed recently — it can be changed "
                            f"again after {next_allowed.date().isoformat()}"
                        ),
                    )
            if await _handle_taken(candidate, db, excluding=user_id):
                raise HTTPException(status_code=409, detail="That username is taken")

            if not is_claim:
                user.username_changed_at = datetime.now(timezone.utc)
            user.username = candidate
            # Chosen now, whatever it was before.
            user.username_is_generated = False

    if body.display_name is not None:
        user.display_name = body.display_name.strip() or None
    if body.bio is not None:
        user.bio = body.bio.strip() or None
    if body.is_private is not None:
        user.is_private = body.is_private

    try:
        await db.commit()
    except IntegrityError:
        # The unique index is the real arbiter — two simultaneous claims of the
        # same handle both pass the check above and one loses here.
        await db.rollback()
        raise HTTPException(status_code=409, detail="That username is taken")

    await db.refresh(user)
    return user


@router.post("/me/avatar", response_model=MeOut)
async def upload_avatar(user_id: UserId, db: DB, file: UploadFile = File(...)):
    """Replace the caller's avatar.

    The key is deterministic per user, so a re-upload overwrites rather than
    orphaning the previous object.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_AVATAR_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image type. Allowed: {', '.join(sorted(ALLOWED_AVATAR_TYPES))}",
        )
    if not storage.r2_configured():
        raise HTTPException(status_code=503, detail="Storage is not configured")

    key = storage.avatar_key(user.id)
    try:
        # LimitedReader enforces the cap while streaming, even when the client
        # omits Content-Length (same reasoning as the video upload path).
        limited = storage.LimitedReader(file.file, MAX_AVATAR_BYTES)
        # Offload the blocking boto3 call — the API runs a single uvicorn
        # worker, so doing this inline stalls every other request (CF-63).
        avatar_url = await run_in_threadpool(
            storage.upload_fileobj, limited, key, content_type=content_type
        )
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except Exception:
        logger.exception("Avatar upload failed for user %s", user_id)
        raise HTTPException(status_code=500, detail="Storage upload failed")

    # Cache-bust. The key is deterministic, so a second upload yields a byte
    # identical URL: React re-renders the same `src`, and the browser and CDN
    # both serve the image already cached under it. The user changes their photo
    # and the sidebar, /u/{handle} and this page all keep showing the old one
    # until the cache expires. The version param makes the URL change with the
    # upload without changing the object.
    version = int(datetime.now(timezone.utc).timestamp())
    user.avatar_url = f"{avatar_url}?v={version}"
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/{handle}", response_model=ProfileOut)
async def get_profile(handle: str, db: DB):
    """Public profile by handle.

    Registered last so it can't shadow `/users/me` or `/users/handle-available`
    — FastAPI matches in declaration order, and `{handle}` would otherwise
    swallow both. `handles.RESERVED_HANDLES` blocks those names anyway; the
    ordering makes it safe regardless.

    Returns the identity only. A private account's *content* stays hidden — the
    profile itself is visible so someone can find the account to request a
    follow. Content gating arrives with CF-108's visibility model.

    KNOWN, ACCEPTED FOR NOW: this is unauthenticated and unthrottled, so it is
    enumerable. "Findable by handle" and "bulk-listable by a stranger" are not
    the same property, and migration 010's backfill makes handles guessable
    (they come from email local parts), so a wordlist walk would return a roster
    of accounts with bios and photos — on a youth-sports product. CF-108 gates
    *content* visibility and does not cover this. Rate limiting this route is
    tracked separately; until then it is a deliberate risk, not an oversight.
    """
    return await _by_handle(handle, db)
