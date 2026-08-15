import base64
import binascii
import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.database import get_db
from app.models.follow import Follow, FollowStatus
from app.models.user import User
from app.schemas.follow import FollowOut, FollowPage, FollowRequestOut, FollowStateOut
from app.schemas.profile import ProfileOut
from app.services import profiles

logger = logging.getLogger(__name__)

router = APIRouter(tags=["follows"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]


async def _adjust_counts(db: AsyncSession, follower_id: uuid.UUID, followee_id: uuid.UUID, delta: int) -> None:
    """Move both counters by `delta` in the caller's transaction.

    Only accepted edges are counted — a pending request is not a follower, and
    showing it as one would leak that someone requested access.

    Written as UPDATE ... SET x = x + n rather than read-modify-write so two
    concurrent follows can't both read the same value and lose one increment.
    """
    await db.execute(
        update(User)
        .where(User.id == followee_id)
        .values(follower_count=User.follower_count + delta)
    )
    await db.execute(
        update(User)
        .where(User.id == follower_id)
        .values(following_count=User.following_count + delta)
    )


@router.post("/users/{handle}/follow", response_model=FollowStateOut)
async def follow_user(handle: str, user_id: UserId, db: DB):
    """Follow, or request to follow a private account.

    Idempotent: following twice returns the existing state rather than erroring
    or creating a second edge (the unique constraint would reject it anyway).
    """
    target = await profiles.by_handle(handle, db)
    if target.id == user_id:
        # Also a CHECK in the database — this is the friendly message.
        raise HTTPException(status_code=400, detail="You cannot follow yourself")

    existing = (
        await db.execute(
            select(Follow).where(
                Follow.follower_id == user_id, Follow.followee_id == target.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return FollowStateOut(status=existing.status, following=existing.status is FollowStatus.accepted)

    # Public accounts accept immediately; private ones hold the edge as a
    # request until the target approves. This is what makes "private by
    # default" more than a label.
    new_status = FollowStatus.pending if target.is_private else FollowStatus.accepted
    db.add(Follow(follower_id=user_id, followee_id=target.id, status=new_status))

    try:
        # _adjust_counts is INSIDE the try on purpose. Its db.execute() autoflushes
        # the pending Follow, so a duplicate-pair violation surfaces there rather
        # than at commit — with the call outside, two concurrent follows of a
        # *public* account both passed the `existing is None` check and the second
        # 500'd instead of returning the existing state. Private targets never hit
        # it, since a pending edge skips the counters entirely, which is why the
        # recovery looked like it worked.
        if new_status is FollowStatus.accepted:
            await _adjust_counts(db, user_id, target.id, +1)
        await db.commit()
    except IntegrityError:
        # Lost a race with a concurrent follow — the unique pair is the arbiter.
        await db.rollback()
        existing = (
            await db.execute(
                select(Follow).where(
                    Follow.follower_id == user_id, Follow.followee_id == target.id
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise HTTPException(status_code=409, detail="Could not follow")
        return FollowStateOut(
            status=existing.status, following=existing.status is FollowStatus.accepted
        )

    return FollowStateOut(status=new_status, following=new_status is FollowStatus.accepted)


@router.delete("/users/{handle}/follow", response_model=FollowStateOut)
async def unfollow_user(handle: str, user_id: UserId, db: DB):
    """Unfollow, or withdraw a pending request. Idempotent — unfollowing
    someone you don't follow succeeds rather than 404ing, so a double-tap or a
    retry can't fail."""
    target = await profiles.by_handle(handle, db)
    existing = (
        await db.execute(
            select(Follow).where(
                Follow.follower_id == user_id, Follow.followee_id == target.id
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        was_accepted = existing.status is FollowStatus.accepted
        await db.delete(existing)
        if was_accepted:
            await _adjust_counts(db, user_id, target.id, -1)
        await db.commit()

    return FollowStateOut(status=None, following=False)


@router.get("/users/{handle}/follow-state", response_model=FollowStateOut)
async def get_follow_state(handle: str, user_id: UserId, db: DB):
    """What the follow button should render for this viewer."""
    target = await profiles.by_handle(handle, db)
    existing = (
        await db.execute(
            select(Follow).where(
                Follow.follower_id == user_id, Follow.followee_id == target.id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        return FollowStateOut(status=None, following=False)
    return FollowStateOut(
        status=existing.status, following=existing.status is FollowStatus.accepted
    )


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """`<iso timestamp>|<uuid>` → the sort key, or 400.

    The timestamp must carry an offset. `fromisoformat` happily parses a naive
    string, and comparing naive to a `timestamptz` column raises inside asyncpg
    — a 500 for what is a malformed request, so it is rejected here as a 400
    instead.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, _, follow_id = raw.partition("|")
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            raise ValueError("cursor timestamp must be timezone-aware")
        return parsed, uuid.UUID(follow_id)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid cursor")


def _encode_cursor(follow: Follow) -> str:
    raw = f"{follow.created_at.isoformat()}|{follow.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


async def _edge_page(
    db: AsyncSession,
    *,
    match_col,
    other_col,
    subject_id: uuid.UUID,
    cursor: str | None,
    limit: int,
) -> FollowPage:
    """One page of accepted edges, newest first.

    Keyset on the **full** sort key, never OFFSET (epic decision 5), which
    duplicates and skips rows as new follows land mid-scroll.

    The cursor carries `id` as well as `created_at`. Naming `id` as the
    tiebreaker in the ORDER BY is an admission that ties happen — and a cursor
    that filters on the timestamp alone drops the rest of a tied group whenever
    a page boundary lands inside one. Bulk-accepting a backlog of requests
    produces exactly that: many edges sharing a timestamp.
    """
    q = (
        select(Follow, User)
        .join(User, other_col == User.id)
        .where(match_col == subject_id, Follow.status == FollowStatus.accepted)
        .order_by(Follow.created_at.desc(), Follow.id.desc())
        # One extra row, discarded — that's how the last page is known without a
        # second COUNT over the same filtered set.
        .limit(limit + 1)
    )
    if cursor:
        q = q.where(tuple_(Follow.created_at, Follow.id) < _decode_cursor(cursor))

    rows = (await db.execute(q)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return FollowPage(
        items=[
            FollowOut(
                created_at=follow.created_at, user=profiles.serialize(user, ProfileOut)
            )
            for follow, user in rows
        ],
        next_cursor=_encode_cursor(rows[-1][0]) if (has_more and rows) else None,
    )


@router.get("/users/{handle}/followers", response_model=FollowPage)
async def list_followers(
    handle: str,
    db: DB,
    user_id: UserId,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    """Who follows this account.

    Private accounts show their lists only to the owner: who follows you is
    itself information about you, and a private account shouldn't leak its
    audience to a stranger.
    """
    target = await profiles.by_handle(handle, db)
    if target.is_private and target.id != user_id:
        raise HTTPException(status_code=404, detail="Profile not found")
    return await _edge_page(
        db,
        match_col=Follow.followee_id,
        other_col=Follow.follower_id,
        subject_id=target.id,
        cursor=cursor,
        limit=limit,
    )


@router.get("/users/{handle}/following", response_model=FollowPage)
async def list_following(
    handle: str,
    db: DB,
    user_id: UserId,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    """Who this account follows — same privacy rule as followers."""
    target = await profiles.by_handle(handle, db)
    if target.is_private and target.id != user_id:
        raise HTTPException(status_code=404, detail="Profile not found")
    return await _edge_page(
        db,
        match_col=Follow.follower_id,
        other_col=Follow.followee_id,
        subject_id=target.id,
        cursor=cursor,
        limit=limit,
    )


@router.get("/users/me/follow-requests", response_model=list[FollowRequestOut])
async def list_follow_requests(user_id: UserId, db: DB):
    """Pending requests awaiting the caller's approval."""
    rows = (
        await db.execute(
            select(Follow, User)
            .join(User, Follow.follower_id == User.id)
            .where(Follow.followee_id == user_id, Follow.status == FollowStatus.pending)
            .order_by(Follow.created_at.desc())
        )
    ).all()
    return [
        FollowRequestOut(
            id=follow.id,
            created_at=follow.created_at,
            requester=profiles.serialize(user, ProfileOut),
        )
        for follow, user in rows
    ]


async def _own_pending_request(request_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> Follow:
    follow = await db.get(Follow, request_id)
    if follow is None or follow.followee_id != user_id or follow.status is not FollowStatus.pending:
        raise HTTPException(status_code=404, detail="Request not found")
    return follow


@router.post("/follow-requests/{request_id}/accept", response_model=FollowStateOut)
async def accept_follow_request(request_id: uuid.UUID, user_id: UserId, db: DB):
    """Approve — this is the moment the follower gains `followers`-tier access.

    The status change is a **conditional** UPDATE and the counters move only if
    it matched a row. Read-check-then-write would let two concurrent accepts of
    the same request — a double-tap, or a client retry on a slow response — both
    read `pending`, both write `accepted`, and both increment, leaving
    `follower_count` at 2 for a single edge. Atomic `x = x + n` prevents *lost*
    increments, not *duplicate* ones; only the guarded UPDATE does that.
    """
    follow = await _own_pending_request(request_id, user_id, db)

    result = await db.execute(
        update(Follow)
        .where(Follow.id == follow.id, Follow.status == FollowStatus.pending)
        .values(status=FollowStatus.accepted)
    )
    if result.rowcount == 1:
        await _adjust_counts(db, follow.follower_id, follow.followee_id, +1)
    await db.commit()

    # Either way the edge is accepted now, so the response is the same — a
    # retry that lost the race still describes the world correctly.
    return FollowStateOut(status=FollowStatus.accepted, following=True)


@router.post("/follow-requests/{request_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_follow_request(request_id: uuid.UUID, user_id: UserId, db: DB):
    """Decline. The row is deleted rather than kept as `rejected`, so the
    requester can ask again later — and so a rejection isn't a permanent record
    of who was turned down."""
    follow = await _own_pending_request(request_id, user_id, db)
    # No counters here — a pending edge was never counted — so a plain delete is
    # safe against a concurrent duplicate: the second finds nothing and the
    # 404 from _own_pending_request is the correct answer.
    await db.delete(follow)
    await db.commit()
