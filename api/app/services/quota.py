"""Upload limits and per-user processing quota (CF-91).

A cost guardrail, not a billing system. GPU inference runs roughly $0.25 per
hour of footage, so without a ceiling one account uploading a multi-hour video —
or looping uploads — can burn the whole budget. Plan tiers (CF-64) are the real
answer; this bounds the damage until they land.

Two independent caps, both enforced server-side:

* **Per-upload** — content type, byte size, and duration of a single video.
* **Per-window** — how many videos, and how many minutes of footage, one user
  may queue in a rolling period.

The split between `limits_for_user` (policy) and `check_upload_allowed` (a pure
decision over that policy) is the seam CF-64 plugs into: a plan lookup replaces
the body of `limits_for_user` and every caller keeps working.

Three things make the count trustworthy, and each exists because the obvious
version of it did not hold:

* **Consumption is counted from `upload_events`, not from `games`.** Games are
  hard-deleted, so counting them let a user upload to the cap, delete, and
  repeat — refunding GPU spend that had already happened.
* **The check and the reservation are one transaction**, serialised per user by
  an advisory lock. Reading the quota and then inserting after a multi-GB
  upload left a window minutes wide in which any number of parallel requests
  all saw the same "under the cap".
* **A duration the file size contradicts is not believed.** Charging a missing
  or zero duration at face value bought 5 x the per-video cap against a much
  smaller minute cap; charging every unknown one a flat maximum instead
  punished formats the browser cannot probe. The charge falls back to the
  verified byte count — see `charge_for` — and the worker settles it to the
  probed truth.

The api never sees the video's metadata — probing a multi-GB object inside a
request handler is not affordable — so the duration it acts on at accept time
is always a claim. `app.workers.tasks` re-probes before any GPU stage, rejects
over-cap footage there, and calls `settle_upload_charge` with the real number.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.upload_event import UploadEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadLimits:
    """What a single user is allowed. Today the same for everyone (CF-64)."""

    max_upload_bytes: int
    max_duration_seconds: float
    allowed_content_types: tuple[str, ...]
    window_hours: float
    max_games_per_window: int
    max_minutes_per_window: float


@dataclass(frozen=True)
class QuotaStatus:
    """Where a user stands against their limits right now."""

    limits: UploadLimits
    games_used: int
    minutes_used: float
    window_started_at: datetime

    @property
    def games_remaining(self) -> int:
        return max(0, self.limits.max_games_per_window - self.games_used)

    @property
    def minutes_remaining(self) -> float:
        return max(0.0, self.limits.max_minutes_per_window - self.minutes_used)


def limits_for_user(user_id: uuid.UUID) -> UploadLimits:
    """Resolve the limits that apply to a user.

    CF-64 seam: today every user gets the configured defaults. When plans
    exist, look the user's tier up here and return its numbers — callers below
    and in the routers do not change.
    """
    return UploadLimits(
        max_upload_bytes=settings.max_upload_bytes,
        max_duration_seconds=settings.max_upload_duration_seconds,
        allowed_content_types=tuple(sorted(settings.allowed_content_types_set)),
        window_hours=settings.quota_window_hours,
        max_games_per_window=settings.quota_max_games_per_window,
        max_minutes_per_window=settings.quota_max_minutes_per_window,
    )


async def get_quota_status(
    db: AsyncSession, user_id: uuid.UUID, limits: UploadLimits | None = None
) -> QuotaStatus:
    """Usage for `user_id` over the trailing window.

    Reads the append-only ledger, so what counts is what was *accepted* — not
    what still exists. Deleting the game does not give the slot back; the only
    thing that returns one is time passing out of the window.

    Every accepted upload counts, including ones whose processing later failed.
    A genuine pipeline failure does eat a slot, but exempting failures would
    leave a hole: a rejected upload is still a file transferred and decoded, and
    an exempt failure can be repeated without limit.
    """
    limits = limits or limits_for_user(user_id)
    window_started_at = datetime.now(timezone.utc) - timedelta(hours=limits.window_hours)

    row = (
        await db.execute(
            select(
                func.count(UploadEvent.id),
                func.coalesce(func.sum(UploadEvent.charged_seconds), 0.0),
            ).where(
                UploadEvent.owner_id == user_id,
                UploadEvent.created_at >= window_started_at,
            )
        )
    ).one()

    return QuotaStatus(
        limits=limits,
        games_used=int(row[0] or 0),
        minutes_used=float(row[1] or 0.0) / 60.0,
        window_started_at=window_started_at,
    )


def _fmt_window(hours: float) -> str:
    return f"{hours:g} hours" if hours != 1 else "hour"


def fmt_size(num_bytes: int) -> str:
    """Render a byte count the way the dropzone does — GB once it is one, MB
    below that, no trailing ".0".

    Public because `complete_upload` needs the same wording for the same cap:
    CF-167 was two places disagreeing about one limit, and "8.0 GB" here
    against "8 GB" in the UI, or "0.5 GB" against "512 MB", is that bug again
    in smaller print. Mirrors fmtLimit in web/src/components/UploadZone.tsx —
    change the two together.

    Decimal rather than f-string rounding because Python rounds half to even
    and JS's toFixed rounds half away from zero, so a limit landing exactly on
    a half would render differently on each side.
    """
    gb = Decimal(num_bytes) / Decimal(1024**3)
    if gb >= 10:
        return f"{gb.quantize(Decimal(1), rounding=ROUND_HALF_UP)} GB"
    if gb >= 1:
        return f"{gb.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP).normalize():f} GB"
    mb = Decimal(num_bytes) / Decimal(1024**2)
    return f"{mb.quantize(Decimal(1), rounding=ROUND_HALF_UP)} MB"


# Bitrate bounds used to sanity-check and stand in for a declared duration.
# These are physical limits on what a video file *can* be, not tuning knobs:
# together they say a file of N bytes cannot be shorter than N/MAX and is very
# unlikely to be longer than N/MIN.
#
# MAX is set high (4K phone footage) so only a clearly impossible claim is
# rejected; MIN is set low (heavily compressed 720p) so the stand-in
# over-estimates rather than under-charges. Both are deliberately generous in
# the direction that avoids punishing an honest upload.
_MAX_PLAUSIBLE_BITRATE_BPS = 100_000_000  # 100 Mbps
_MIN_PLAUSIBLE_BITRATE_BPS = 1_000_000    # 1 Mbps


def _duration_from_size(limits: UploadLimits, size_bytes: int | None) -> float:
    """A conservative stand-in duration for a file we can't time.

    Derived from the object's real size, which — unlike the duration — is
    verified. Assuming the lowest plausible bitrate over-estimates the length,
    which is the safe direction for a charge that gets settled downward later.
    Capped at the per-video maximum, since anything longer is rejected anyway.
    """
    if not size_bytes:
        return limits.max_duration_seconds
    return min(size_bytes * 8 / _MIN_PLAUSIBLE_BITRATE_BPS, limits.max_duration_seconds)


def charge_for(
    limits: UploadLimits, duration_seconds: float | None, size_bytes: int | None = None
) -> float:
    """Seconds this upload costs the minute quota at accept time.

    The api never decodes the video, so the duration is always a claim. Two
    failure modes pull in opposite directions and this is the seam where both
    are handled:

    * **Too cheap.** Charging a missing or zero duration at face value made the
      minute cap advisory — omit the field (or send `0`, or `0.001`) and the
      only real limit left was the count cap, so the ceiling became
      `max_games x max_duration` rather than the stated one.
    * **Too expensive.** Charging every unknown duration the flat per-video
      maximum punishes formats the browser cannot probe. Chrome can't read MKV
      metadata and MKV is a type we accept, so a 3-minute clip was charged 4
      hours and blocked the user's day.

    Both are answered by falling back to the file's *size*, which is verified,
    rather than to a constant. A claim below what the byte count physically
    allows is impossible, so it is discarded and the size stands in; otherwise
    the claim is taken at face value. `settle_upload_charge` corrects whatever
    is left once the worker has actually measured the file.
    """
    if size_bytes:
        # The shortest a file this size could possibly be. A claim under it is
        # not a low estimate, it is a contradiction — treat it as no claim.
        physical_floor = size_bytes * 8 / _MAX_PLAUSIBLE_BITRATE_BPS
        if duration_seconds is None or duration_seconds < physical_floor:
            return _duration_from_size(limits, size_bytes)
        return duration_seconds

    # No size either (nothing to cross-check against): fall back to the cap.
    if duration_seconds is None or duration_seconds <= 0:
        return limits.max_duration_seconds
    return duration_seconds


def check_upload_allowed(
    status: QuotaStatus,
    *,
    content_type: str | None,
    size_bytes: int | None,
    duration_seconds: float | None,
) -> tuple[int, str] | None:
    """Decide whether one upload may proceed.

    Returns `None` when it may, or `(http_status, message)` when it may not.
    Pure so the policy is testable without a database, and so the presigned
    upload flow (CF-163) can call it before a byte moves.

    `content_type` and `size_bytes` may be None when the caller has no
    trustworthy value and the check belongs elsewhere — the presigned flow
    validates the type at presign and R2 enforces it from the signature, so
    re-deriving it from a HEAD at completion would risk rejecting an upload
    that already succeeded on a storage quirk. A None `duration_seconds` is
    *not* skipped: it is priced from the size instead — see `charge_for`.

    Callers that go on to accept the upload must use `reserve_upload`, which
    runs this inside the transaction that records the charge. Calling this on
    its own is a read-only preview.
    """
    limits = status.limits

    if content_type is not None and content_type not in limits.allowed_content_types:
        return 415, (
            "Unsupported file type"
            + (f" ({content_type})" if content_type else "")
            + f". Allowed: {', '.join(limits.allowed_content_types)}."
        )

    if size_bytes is not None and size_bytes > limits.max_upload_bytes:
        return 413, (
            f"File is {fmt_size(size_bytes)}; the maximum is "
            f"{fmt_size(limits.max_upload_bytes)}."
        )

    if duration_seconds is not None and duration_seconds > limits.max_duration_seconds:
        return 413, (
            f"Video is {duration_seconds / 60:.0f} min long; the maximum is "
            f"{limits.max_duration_seconds / 60:.0f} min. Split it into "
            "separate uploads."
        )

    window = _fmt_window(limits.window_hours)

    if status.games_used >= limits.max_games_per_window:
        return 429, (
            f"Upload quota reached: {status.games_used} of "
            f"{limits.max_games_per_window} videos in the last {window}. "
            "Your quota frees up as earlier uploads age out of the window."
        )

    requested_minutes = charge_for(limits, duration_seconds, size_bytes) / 60.0
    if status.minutes_used + requested_minutes > limits.max_minutes_per_window:
        # Say which of the two it is. An upload charged at the maximum for a
        # missing duration is otherwise indistinguishable from a genuinely
        # 4-hour video, and the client can fix one of those.
        this_upload = (
            f"this video is {requested_minutes:.0f} min"
            if duration_seconds is not None
            else f"this upload is charged the {requested_minutes:.0f} min maximum "
                 "because its length wasn't provided"
        )
        return 429, (
            f"Upload quota reached: {this_upload} and you "
            f"have {status.minutes_remaining:.0f} min of your "
            f"{limits.max_minutes_per_window:.0f} min per {window} left. "
            "Your quota frees up as earlier uploads age out of the window."
        )

    return None


def _advisory_lock_key(user_id: uuid.UUID) -> int:
    """A stable signed 64-bit key for `pg_advisory_xact_lock`.

    Derived from the uuid's own bytes rather than `hash()`, which is salted per
    process and would hand two api workers different keys for the same user.
    """
    return int.from_bytes(user_id.bytes[:8], "big", signed=True)


async def reserve_upload(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    game_id: uuid.UUID,
    content_type: str | None,
    size_bytes: int | None,
    duration_seconds: float | None,
    limits: UploadLimits | None = None,
) -> tuple[UploadEvent | None, tuple[int, str] | None]:
    """Check the quota and claim a slot for one game, in one transaction.

    Returns `(event, None)` on success or `(None, (status, message))` on
    rejection.

    Checking and then inserting are one step on purpose. Split apart — read the
    quota, stream a multi-GB upload, insert the row — the window between them is
    as long as the upload, and N parallel requests from one account all read the
    same "under the cap" and all commit. That is a total bypass of both caps,
    not an off-by-one, so the reservation is taken *before* any bytes move.

    `pg_advisory_xact_lock` serialises the read-and-insert per user. It is held
    only for the statements below, not for the transfer: this commits before
    the caller touches storage. Contention is per user, so one account hammering
    the endpoint cannot slow anyone else down.

    **Idempotent per game.** A game already carrying a charge returns that
    charge rather than a second one, which is what makes a double-submit safe:
    two completions of the same upload share one reservation, so neither can be
    rejected on a slot the other just took, and the loser of the race has
    nothing to hand back. It also means a crash between this commit and the
    caller's own leaves a retry re-using the charge instead of paying twice.
    """
    limits = limits or limits_for_user(user_id)

    await db.execute(select(func.pg_advisory_xact_lock(_advisory_lock_key(user_id))))

    existing = (
        await db.execute(select(UploadEvent).where(UploadEvent.game_id == game_id))
    ).scalars().first()
    if existing is not None:
        return existing, None

    status = await get_quota_status(db, user_id, limits)
    rejection = check_upload_allowed(
        status,
        content_type=content_type,
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
    )
    if rejection:
        # Drop the lock without writing; nothing was claimed.
        await db.rollback()
        return None, rejection

    event = UploadEvent(
        owner_id=user_id,
        game_id=game_id,
        charged_seconds=charge_for(limits, duration_seconds, size_bytes),
    )
    db.add(event)
    await db.commit()  # releases the advisory lock
    await db.refresh(event)
    return event, None


# There is deliberately no release/refund helper. With the reservation keyed to
# the game, the loser of a completion race shares the winner's charge rather
# than holding one of its own, so there is nothing to hand back — and a helper
# that deletes ledger rows is precisely the refund path this table exists to
# prevent. An upload that is rejected outright never reaches a reservation.


# Settlement — correcting a reservation to the probed duration — lives in
# app.workers._sync_db as `sync_settle_upload_charge`, because the only caller
# is the Celery worker and it has no event loop.

