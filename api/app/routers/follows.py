import base64
import binascii
import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import ColumnElement, delete, func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id, get_optional_user_id
from app.database import get_db
from app.models.follow import Follow, FollowStatus
from app.models.user import User
from app.schemas.follow import (
    FollowOut,
    FollowPage,
    FollowRequestOut,
    FollowRequestPage,
    FollowStateOut,
)
from app.schemas.profile import ProfileOut
from app.services import profiles

logger = logging.getLogger(__name__)

router = APIRouter(tags=["follows"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]
ViewerId = Annotated[uuid.UUID | None, Depends(get_optional_user_id)]

DEFAULT_PAGE = 50


async def _adjust_counts(db: AsyncSession, follower_id: uuid.UUID, followee_id: uuid.UUID, delta: int) -> None:
    """Move both counters by `delta` in the caller's transaction.

    Only accepted edges are counted — a pending request is not a follower, and
    showing it as one would leak that someone requested access.

    Written as UPDATE ... SET x = x + n rather than read-modify-write so two
    concurrent follows can't both read the same value and lose one increment.

    **Decrements are floored at zero in SQL** rather than allowed to go negative
    and be caught by `ck_users_*_count_non_negative`. The CHECK is still worth
    having — it turns a *new* counter bug into an error at the write that causes
    it — but as the only guard it converts a wrong number into a stuck
    authorization edge: `unfollow` does not wrap its commit, so a decrement from
    0 aborts the whole transaction *including the DELETE*, every retry does the
    same, and the follower keeps `followers`-tier access with no way to revoke
    it. The counters are already known not to be authoritative — deleting a user
    cascades their edges away without adjusting the other side, which CF-116
    reconciles — so revocation must not depend on them being right. GREATEST
    keeps the ladder monotonic and leaves the CHECK guarding every other writer.
    """
    follower_expr: ColumnElement[int]
    following_expr: ColumnElement[int]
    if delta < 0:
        follower_expr = func.greatest(User.follower_count + delta, 0)
        following_expr = func.greatest(User.following_count + delta, 0)
    else:
        follower_expr = User.follower_count + delta
        following_expr = User.following_count + delta

    await db.execute(
        update(User).where(User.id == followee_id).values(follower_count=follower_expr)
    )
    await db.execute(
        update(User).where(User.id == follower_id).values(following_count=following_expr)
    )


def _state(existing: Follow | None) -> FollowStateOut:
    """The one place an edge becomes a button state.

    `pending` is *not* following — conflating the two is how a requester would
    appear to have access they don't have.
    """
    if existing is None:
        return FollowStateOut(status=None, following=False)
    return FollowStateOut(
        status=existing.status, following=existing.status is FollowStatus.accepted
    )


async def _edge(db: AsyncSession, follower_id: uuid.UUID, followee_id: uuid.UUID) -> Follow | None:
    """The edge between two users, or None. Re-read after a lost race so the
    response describes the row that actually survived rather than the one this
    request meant to write."""
    return (
        await db.execute(
            select(Follow).where(
                Follow.follower_id == follower_id, Follow.followee_id == followee_id
            )
        )
    ).scalar_one_or_none()


@router.post("/users/{handle}/follow", response_model=FollowStateOut)
async def follow_user(handle: str, user_id: UserId, db: DB):
    """Follow, or request to follow a private account.

    Idempotent: following twice returns the existing state rather than erroring
    or creating a second edge (the unique constraint would reject it anyway).

    **Accepted race, stated rather than hidden.** `is_private` is read here and
    the status is decided from it, so a request already in flight when the
    target switches to private lands as `accepted` instead of `pending` — and an
    accepted edge is `followers`-tier access to their footage. The window is a
    few milliseconds and closing it means locking the target's row on every
    follow, which no comparable product does.

    It is called out because it is the only race in this file that *grants*
    access rather than miscounting, and because the fix if we ever want one is
    not the obvious re-read: it is demoting existing accepted edges when an
    account goes private, which is a decision about people who already have
    access. CF-116.
    """
    target = await profiles.by_handle(handle, db)
    # Read the two fields off the ORM object *before* anything can roll back.
    # `db.rollback()` expires every instance in the identity map
    # (`_restore_snapshot` runs with `dirty_only=False` on a top-level
    # transaction), so a `target.id` afterwards is a lazy refresh — which under
    # AsyncSession raises MissingGreenlet and 500s the very recovery path below,
    # exactly where the duplicate-pair loser was supposed to be handled. Plain
    # locals cannot expire.
    target_id = target.id
    target_is_private = target.is_private

    if target_id == user_id:
        # Also a CHECK in the database — this is the friendly message.
        raise HTTPException(status_code=400, detail="You cannot follow yourself")

    existing = await _edge(db, user_id, target_id)
    if existing is not None:
        # A pending request against an account that has since gone public would
        # otherwise be stranded forever: this endpoint short-circuits on any
        # existing edge, so the requester waits on an approval queue the target
        # no longer has, while everyone arriving after the flip is accepted
        # outright. Promote it — the same person pressing Follow today would be
        # accepted, and holding them back for having asked earlier is the wrong
        # way round. Guarded like every other status write here, so a concurrent
        # accept can't double-count.
        if existing.status is FollowStatus.pending and not target_is_private:
            promoted = await db.execute(
                update(Follow)
                .where(Follow.id == existing.id, Follow.status == FollowStatus.pending)
                .values(status=FollowStatus.accepted)
            )
            if promoted.rowcount != 1:
                # The third mutation in this file, and it needs the same ending
                # as the other two: the counters were guarded on rowcount but
                # the *response* was not, so a promote that wrote nothing still
                # answered `accepted / following: true`. Withdraw on one device
                # while tapping Follow on another and the DELETE lands first —
                # the client then renders Following over an edge that does not
                # exist, and the next `follow-state` call says `null`. Exactly
                # the lie `unfollow_user` and `reject_follow_request` were
                # rewritten to stop telling.
                await db.rollback()
                survivor = await _edge(db, user_id, target_id)
                logger.info(
                    "follow: promote matched nothing for %s -> %s (now %s)",
                    user_id, target_id, survivor.status if survivor else None,
                )
                return _state(survivor)

            await _adjust_counts(db, user_id, target_id, +1)
            await db.commit()
            return FollowStateOut(status=FollowStatus.accepted, following=True)
        return _state(existing)

    # Public accounts accept immediately; private ones hold the edge as a
    # request until the target approves. This is what makes "private by
    # default" more than a label.
    new_status = FollowStatus.pending if target_is_private else FollowStatus.accepted
    db.add(Follow(follower_id=user_id, followee_id=target_id, status=new_status))

    try:
        # _adjust_counts is INSIDE the try on purpose. Its db.execute() autoflushes
        # the pending Follow, so a duplicate-pair violation surfaces there rather
        # than at commit — with the call outside, two concurrent follows of a
        # *public* account both passed the `existing is None` check and the second
        # 500'd instead of returning the existing state. Private targets never hit
        # it, since a pending edge skips the counters entirely, which is why the
        # recovery looked like it worked.
        if new_status is FollowStatus.accepted:
            await _adjust_counts(db, user_id, target_id, +1)
        await db.commit()
    except IntegrityError:
        # Lost a race with a concurrent follow — the unique pair is the arbiter.
        await db.rollback()
        existing = await _edge(db, user_id, target_id)
        if existing is None:
            raise HTTPException(status_code=409, detail="Could not follow")
        logger.info("follow: lost the insert race for %s -> %s", user_id, target_id)
        return _state(existing)

    return FollowStateOut(status=new_status, following=new_status is FollowStatus.accepted)


@router.delete("/users/{handle}/follow", response_model=FollowStateOut)
async def unfollow_user(handle: str, user_id: UserId, db: DB):
    """Unfollow, or withdraw a pending request. Idempotent — unfollowing
    someone you don't follow succeeds rather than 404ing, so a double-tap or a
    retry can't fail."""
    target = await profiles.by_handle(handle, db)
    target_id = target.id
    existing = await _edge(db, user_id, target_id)

    if existing is not None:
        # Conditional DELETE, counters behind its rowcount — the same guard
        # accept_follow_request uses, for the same reason. Read-check-then-write
        # let two concurrent unfollows both see `accepted`, both delete, and
        # both decrement, leaving follower_count at -1 for an edge that existed
        # once. Nothing surfaced it either: SQLAlchemy's ORM delete only warns
        # that it matched no rows and commits the decrement regardless, so the
        # request pair returned 200/200 with a corrupt count behind them.
        removed = await db.execute(
            delete(Follow).where(
                Follow.id == existing.id, Follow.status == existing.status
            )
        )
        if removed.rowcount != 1:
            # Matched nothing, so the row changed underneath the guard — in
            # practice, the target accepting a pending request in the same
            # instant. Returning `following: false` here is the dangerous
            # reading of "idempotent": the user is told they revoked access
            # while remaining an accepted follower, and since the counters agree
            # with the surviving edge, CF-116's drift job would never surface it
            # either. Answer for the row that exists instead, so the button
            # renders Unfollow and a second tap actually revokes.
            await db.rollback()
            survivor = await _edge(db, user_id, target_id)
            logger.info(
                "unfollow: edge changed under the guard for %s -> %s (now %s)",
                user_id, target_id, survivor.status if survivor else None,
            )
            return _state(survivor)

        if existing.status is FollowStatus.accepted:
            await _adjust_counts(db, user_id, target_id, -1)
        await db.commit()

    # Either way the edge is gone, so the answer is the same — the loser of a
    # double-tap still describes the world correctly.
    return FollowStateOut(status=None, following=False)


@router.get("/users/{handle}/follow-state", response_model=FollowStateOut)
async def get_follow_state(handle: str, user_id: UserId, db: DB):
    """What the follow button should render for this viewer."""
    target = await profiles.by_handle(handle, db)
    return _state(await _edge(db, user_id, target.id))


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """`<iso timestamp>|<uuid>` → the sort key, or 400.

    The timestamp must carry an offset, and the reason is not the one you might
    expect. asyncpg does **not** raise when a naive datetime meets a
    `timestamptz` column — measured, it returns the same rows as the aware
    equivalent, because it silently reinterprets naive input as UTC. So the
    failure mode is not a 500, it is a page that starts in the wrong place
    whenever the client meant a local time, with no error anywhere.

    A cursor this endpoint issued is always aware. A naive one is therefore
    hand-crafted and malformed, and 400 is the honest answer.
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


def _findable(stmt):
    """Drop rows whose handle was never chosen.

    `profiles.by_handle` refuses to *resolve* a generated handle, which guards
    the lookup direction only. A CF-107 backfill account — username derived from
    `john.smith@…` as `johnsmith`, and 404 at `GET /users/johnsmith` by design —
    can still follow a public account, and without this filter any viewer reads
    its full `ProfileOut`, username included, straight off that account's
    follower list. `services/profiles.py` was lifted out of the router because a
    rule enforced in one place and not its neighbour is a rule that only looks
    enforced; the edge lists are that neighbour.

    Applied to the requests list too, so a generated-handle requester can't
    surface the same way through `/users/me/follow-requests`.
    """
    return stmt.where(User.username_is_generated.is_(False))


def _page_query(stmt, cursor: str | None, limit: int):
    """Newest-first keyset window, one row over the limit.

    Keyset on the **full** sort key, never OFFSET (epic decision 5), which
    duplicates and skips rows as new edges land mid-scroll. The cursor carries
    `id` as well as `created_at`: naming `id` as the tiebreaker in the ORDER BY
    is an admission that ties happen, and a cursor filtering on the timestamp
    alone drops the rest of a tied group whenever a page boundary lands inside
    one. Bulk-accepting a backlog of requests produces exactly that — many edges
    sharing a timestamp.

    The extra row is how the last page is known without a second COUNT over the
    same filtered set.
    """
    stmt = stmt.order_by(Follow.created_at.desc(), Follow.id.desc()).limit(limit + 1)
    if cursor:
        stmt = stmt.where(tuple_(Follow.created_at, Follow.id) < _decode_cursor(cursor))
    return stmt


async def _edge_page(
    db: AsyncSession,
    *,
    match_col,
    other_col,
    subject_id: uuid.UUID,
    cursor: str | None,
    limit: int,
) -> FollowPage:
    """One page of accepted edges, newest first."""
    q = _page_query(
        _findable(
            select(Follow, User)
            .join(User, other_col == User.id)
            .where(match_col == subject_id, Follow.status == FollowStatus.accepted)
        ),
        cursor,
        limit,
    )

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


def _assert_lists_visible(target: User, viewer_id: uuid.UUID | None) -> None:
    """Private accounts show their lists only to the owner: who follows you is
    itself information about you, and a private account shouldn't leak its
    audience to a stranger.

    A public account's lists resolve for anyone, signed-out included. The counts
    are already anonymous — `ProfileOut` carries `follower_count` on the public
    profile route, deliberately, so someone can find an account and decide to
    follow it — and putting a bearer token in front of the list behind a number
    everyone can read is a lock on an open door. If raising the cost of scraping
    the graph is ever the goal, rate limiting is the tool for it (CF-116).
    """
    if target.is_private and target.id != viewer_id:
        raise HTTPException(status_code=404, detail="Profile not found")


@router.get("/users/{handle}/followers", response_model=FollowPage)
async def list_followers(
    handle: str,
    db: DB,
    viewer_id: ViewerId = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = DEFAULT_PAGE,
):
    """Who follows this account."""
    target = await profiles.by_handle(handle, db)
    _assert_lists_visible(target, viewer_id)
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
    viewer_id: ViewerId = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = DEFAULT_PAGE,
):
    """Who this account follows — same privacy rule as followers."""
    target = await profiles.by_handle(handle, db)
    _assert_lists_visible(target, viewer_id)
    return await _edge_page(
        db,
        match_col=Follow.follower_id,
        other_col=Follow.followee_id,
        subject_id=target.id,
        cursor=cursor,
        limit=limit,
    )


@router.get("/users/me/follow-requests", response_model=FollowRequestPage)
async def list_follow_requests(
    user_id: UserId,
    db: DB,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = DEFAULT_PAGE,
):
    """Pending requests awaiting the caller's approval.

    Paginated like the two lists beside it, and for a sharper reason: this is
    the one endpoint whose backlog size nobody controls. `follows` has no rate
    limiting yet (CF-116), and follow-spam is the named vector — so an unbounded
    version loads every pending row plus its joined `User` and signs an avatar
    URL for each, on the event loop thread, sized by whoever is spamming.

    That cost is **CPU, not I/O**, and the distinction is worth keeping
    straight: `generate_presigned_url` computes a SigV4 HMAC locally and issues
    no request, and `storage._client()` is `lru_cache`d so there is no
    per-call client build either (its own docstring says so). Measured with the
    repo's `_BOTO_CONFIG`, 40 signings is ~16 ms. Real at 50 rows and worth
    bounding; not a network hop, and calling it one would point the next person
    with a profiler at somewhere nothing happens.
    """
    q = _page_query(
        _findable(
            select(Follow, User)
            .join(User, Follow.follower_id == User.id)
            .where(Follow.followee_id == user_id, Follow.status == FollowStatus.pending)
        ),
        cursor,
        limit,
    )

    rows = (await db.execute(q)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return FollowRequestPage(
        items=[
            FollowRequestOut(
                id=follow.id,
                created_at=follow.created_at,
                requester=profiles.serialize(user, ProfileOut),
            )
            for follow, user in rows
        ],
        next_cursor=_encode_cursor(rows[-1][0]) if (has_more and rows) else None,
    )


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
    follower_id, followee_id = follow.follower_id, follow.followee_id

    result = await db.execute(
        update(Follow)
        .where(Follow.id == follow.id, Follow.status == FollowStatus.pending)
        .values(status=FollowStatus.accepted)
    )
    if result.rowcount == 1:
        await _adjust_counts(db, follower_id, followee_id, +1)
        await db.commit()
        return FollowStateOut(status=FollowStatus.accepted, following=True)

    # Matched nothing. A duplicate accept is one way to get here and the
    # requester withdrawing in the same instant is the other, and the two need
    # different answers: reporting `accepted` for a withdrawn request tells the
    # owner they granted access to someone who is no longer following them,
    # which is the same class of lie as the unfollow case above. Re-read and
    # answer for whatever row survived.
    await db.rollback()
    survivor = await db.get(Follow, request_id)
    logger.info(
        "accept: request %s changed under the guard (now %s)",
        request_id, survivor.status if survivor else None,
    )
    if survivor is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return _state(survivor)


@router.post("/follow-requests/{request_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_follow_request(request_id: uuid.UUID, user_id: UserId, db: DB):
    """Decline. The row is deleted rather than kept as `rejected`, so the
    requester can ask again later — and so a rejection isn't a permanent record
    of who was turned down.

    **This delete is deliberately not guarded on `pending`, unlike its two
    siblings.** Both this and `accept` pass `_own_pending_request` while the row
    still reads `pending`, so an accept-then-reject interleaving is reachable
    from one owner on two devices, or from a client retrying a slow accept. A
    `WHERE status = 'pending'` delete then matches nothing and 204s anyway: the
    owner is told the request was declined while the requester is an accepted
    follower with live access to their footage. Between a guard that leaves
    access alive and one that revokes it, the decline has to win — the owner
    said no, and no interleaving turns that into yes. RETURNING makes the
    counters follow whichever status was actually removed, so winning the race
    can't leave `follower_count` claiming a follower who is gone.
    """
    follow = await _own_pending_request(request_id, user_id, db)
    follower_id, followee_id = follow.follower_id, follow.followee_id

    removed = (
        await db.execute(
            delete(Follow).where(Follow.id == follow.id).returning(Follow.status)
        )
    ).scalar_one_or_none()
    # `==` rather than `is`: this value comes back through RETURNING rather than
    # from the identity map, and `FollowStatus` is a `str` enum, so equality is
    # right whether the driver hands back the member or the raw label.
    if removed == FollowStatus.accepted:
        logger.info(
            "reject: request %s had been accepted concurrently; access revoked",
            request_id,
        )
        await _adjust_counts(db, follower_id, followee_id, -1)
    await db.commit()
