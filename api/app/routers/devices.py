import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.database import get_db
from app.models.device_token import DeviceToken
from app.schemas.device_token import DeviceTokenRegister, DeviceTokenUnregister

router = APIRouter(prefix="/devices", tags=["devices"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]


@router.post("/tokens", status_code=status.HTTP_204_NO_CONTENT)
async def register_device_token(body: DeviceTokenRegister, db: DB, user_id: UserId):
    """Register this device for push, or move an existing registration here.

    Idempotent by the token, which is what makes it safe for the app to call on
    every launch — and the app should, because the OS reissues tokens without
    telling the user.

    **A conflict re-owns the row.** The alternative — refusing, or keeping both
    rows — leaves a device notifying whoever signed into it last month, which
    is the failure CF-343's sign-out step is also aimed at. Between the two
    risks that is the right way round: re-owning costs an attacker who already
    holds someone's token the ability to receive "your game is ready" in their
    place, while refusing costs a resold phone's new owner nothing and leaks
    the previous owner's activity to a stranger, with no action by anybody.

    `created_at` is deliberately absent from the SET clause: a device that
    re-registers is the same registration, so it keeps the date it first
    appeared. `last_seen_at` is passed explicitly rather than left to the
    column default, because a Python-side `default=` is applied only to the
    INSERT and never to `ON CONFLICT DO UPDATE`.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        pg_insert(DeviceToken)
        .values(
            owner_id=user_id,
            platform=body.platform,
            token=body.token,
            created_at=now,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=["token"],
            set_={
                "owner_id": user_id,
                "platform": body.platform,
                "last_seen_at": now,
            },
        )
    )
    await db.execute(stmt)
    await db.commit()
    return None


@router.post("/tokens/unregister", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_device_token(body: DeviceTokenUnregister, db: DB, user_id: UserId):
    """Stop notifying this device. Called on sign-out (CF-343).

    **A POST, not `DELETE /devices/tokens` with a body.** RFC 9110 gives a
    payload on DELETE no defined semantics and explicitly allows intermediaries
    to drop it, and this is the half of the pair that must not fail quietly: a
    sign-out whose body was stripped returns 204 while the token stays
    registered, and the next person to sign in on that phone gets somebody
    else's notifications. Putting the token in the path instead trades that for
    a token in every access log, and Expo's `ExponentPushToken[...]` spelling
    needs percent-encoding to survive one.

    Scoped to the caller, and 204 either way — whether a row was deleted, the
    token was never registered, or it belongs to someone else. Sign-out must
    not be able to fail, and a 404 on somebody else's token would answer
    "is this token registered?" for anyone who asks.
    """
    await db.execute(
        delete(DeviceToken).where(
            DeviceToken.token == body.token,
            DeviceToken.owner_id == user_id,
        )
    )
    await db.commit()
    return None
