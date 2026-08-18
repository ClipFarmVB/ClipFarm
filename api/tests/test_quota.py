"""CF-91: upload limits and per-user quota.

Two halves, because the first one alone was not enough. `check_upload_allowed`
is a pure decision over a QuotaStatus, so the policy table is testable without
a database. But every hole found in review lived in the *enforcement* rather
than the policy — a quota that could be refunded by deleting the game, a check
that raced its own insert, an omitted form field that bought free minutes — and
none of those would have failed a policy test. The second half covers the
pieces that turn the decision into a control — the ledger the count is read
from, and the reservation that claims a slot.

The last link, that the upload handlers actually call any of this, is covered
in test_uploads.py alongside the presign flow's own fakes.

Run from api/: pytest tests/test_quota.py
"""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from app.services import quota as quota_service  # noqa: E402
from app.services.quota import (  # noqa: E402
    QuotaStatus,
    UploadLimits,
    charge_for,
    check_upload_allowed,
    limits_for_user,
)

GB = 1024 ** 3

LIMITS = UploadLimits(
    max_upload_bytes=2 * GB,
    max_duration_seconds=4 * 3600,
    allowed_content_types=("video/mp4", "video/quicktime"),
    window_hours=24.0,
    max_games_per_window=5,
    max_minutes_per_window=360.0,
)


def status(games_used: int = 0, minutes_used: float = 0.0) -> QuotaStatus:
    return QuotaStatus(
        limits=LIMITS,
        games_used=games_used,
        minutes_used=minutes_used,
        window_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def upload(st: QuotaStatus, **kw):
    args = {"content_type": "video/mp4", "size_bytes": GB, "duration_seconds": 5400.0}
    args.update(kw)
    return check_upload_allowed(st, **args)


# ── the normal case ──────────────────────────────────────────────────────────


def test_a_normal_game_upload_is_allowed():
    # 90 min, 1 GB, first upload of the day — the case that must never trip.
    assert upload(status()) is None


def test_unknown_size_skips_the_size_check():
    # No Content-Length: the streaming cap is the backstop, not a rejection.
    assert upload(status(), size_bytes=None) is None


# ── per-upload limits ────────────────────────────────────────────────────────


def test_disallowed_content_type_is_415():
    code, message = upload(status(), content_type="video/x-flv")
    assert code == 415
    assert "video/mp4" in message  # names what *is* allowed


def test_oversize_file_is_413_and_names_the_limit():
    code, message = upload(status(), size_bytes=3 * GB)
    assert code == 413
    assert "2.0 GB" in message


def test_at_exactly_the_size_limit_is_allowed():
    assert upload(status(), size_bytes=2 * GB) is None


def test_overlong_video_is_413_and_names_the_limit():
    code, message = upload(status(), duration_seconds=5 * 3600)
    assert code == 413
    assert "240 min" in message


# ── per-user quota ───────────────────────────────────────────────────────────


def test_game_count_quota_is_429():
    code, message = upload(status(games_used=5))
    assert code == 429
    assert "5 of 5" in message


def test_last_slot_still_fits():
    assert upload(status(games_used=4, minutes_used=60.0)) is None


def test_minute_quota_counts_the_requested_video_not_just_history():
    # 300 min used and a 90 min upload = 390 > 360: rejected even though the
    # history alone is under the cap.
    code, message = upload(status(games_used=1, minutes_used=300.0))
    assert code == 429
    assert "60 min" in message  # what's actually left


def test_quota_message_explains_that_it_recovers():
    _, message = upload(status(games_used=5))
    assert "age out" in message


def test_per_upload_limits_are_checked_before_quota():
    # An oversize file from a user who is also out of quota reports the file
    # problem — the one the user can act on by picking a different file.
    code, _ = upload(status(games_used=5), size_bytes=3 * GB)
    assert code == 413


# ── policy wiring ────────────────────────────────────────────────────────────


def test_limits_come_from_settings():
    # CF-64 will make this per-plan; until then every user gets the configured
    # defaults, and this guards the settings names the endpoint depends on.
    from app.config import settings
    import uuid

    limits = limits_for_user(uuid.uuid4())
    assert limits.max_upload_bytes == settings.max_upload_bytes
    assert limits.max_duration_seconds == settings.max_upload_duration_seconds
    assert limits.max_games_per_window == settings.quota_max_games_per_window
    assert limits.max_minutes_per_window == settings.quota_max_minutes_per_window
    assert set(limits.allowed_content_types) == settings.allowed_content_types_set


def test_default_limits_clear_a_real_match():
    # The guardrail must not fire on normal footage: a 2.5 h match, well inside
    # both the duration cap and a day's minute allowance.
    import uuid

    limits = limits_for_user(uuid.uuid4())
    assert limits.max_duration_seconds >= 2.5 * 3600
    assert limits.max_minutes_per_window >= 150


def test_remaining_never_goes_negative():
    st = status(games_used=99, minutes_used=9999.0)
    assert st.games_remaining == 0
    assert st.minutes_remaining == 0.0


# ── enforcement: what an undeclared duration costs ───────────────────────────


# 75 MB at the 1 Mbps low-bitrate assumption = 600 s. Used throughout so the
# arithmetic is visible rather than hidden behind a helper.
SIZE_75MB = 75_000_000
SIZE_75MB_AS_SECONDS = 600.0


def test_an_undeclared_duration_is_priced_from_the_verified_size():
    """Charging zero made the minute cap advisory; a flat maximum overcharged.

    Zero meant omitting one optional field cost nothing, so the real ceiling
    became max_games x max_duration. A flat maximum fixed that but charged a
    3-minute MKV — which Chrome cannot probe — the full 4 hours.
    """
    assert charge_for(LIMITS, None, SIZE_75MB) == pytest.approx(SIZE_75MB_AS_SECONDS)


def test_a_plausible_declared_duration_is_charged_as_declared():
    assert charge_for(LIMITS, 5400.0, SIZE_75MB) == 5400.0


@pytest.mark.parametrize("claim", [0.0, 0.001, 1.0, -9999.0])
def test_a_duration_the_size_contradicts_is_discarded(claim):
    """The hole a flat "undeclared" rule left open.

    Omission was priced defensively, but an explicit 0 — or any number below
    what the byte count physically allows — was taken at face value, which is
    the same bypass reachable by sending a value instead of omitting one.
    A 75 MB file cannot be a second long at any real bitrate.
    """
    assert charge_for(LIMITS, claim, SIZE_75MB) == pytest.approx(SIZE_75MB_AS_SECONDS)


def test_a_short_clip_is_charged_as_a_short_clip():
    """The overcharge this replaced: small file, small charge, probe or no probe."""
    assert charge_for(LIMITS, None, 10_000_000) == pytest.approx(80.0)


def test_the_size_stand_in_never_exceeds_the_per_video_cap():
    # Anything longer is rejected outright, so charging beyond it is meaningless.
    assert charge_for(LIMITS, None, 10 * 1024 ** 3) == LIMITS.max_duration_seconds


def test_with_no_size_to_check_against_an_unknown_duration_costs_the_maximum():
    # Nothing to cross-check, so fall back to the defensive default.
    assert charge_for(LIMITS, None, None) == LIMITS.max_duration_seconds
    assert charge_for(LIMITS, 0.0, None) == LIMITS.max_duration_seconds


def test_undeclared_duration_is_refused_once_the_window_is_nearly_full():
    # 300 min used, and the upload is priced from its size: over the 360 cap.
    code, message = upload(
        status(minutes_used=300.0), duration_seconds=None, size_bytes=2 * GB
    )
    assert code == 429
    assert "wasn't provided" in message  # tells the client how to fix it


def test_five_undeclared_uploads_cannot_exceed_the_minute_cap():
    """The regression: walk the window forward charging the default each time."""
    used = 0.0
    accepted = 0
    for _ in range(LIMITS.max_games_per_window):
        rejected = upload(
            status(games_used=accepted, minutes_used=used),
            duration_seconds=None,
            size_bytes=2 * GB,
        )
        if rejected:
            break
        used += charge_for(LIMITS, None, 2 * GB) / 60.0
        accepted += 1
    assert used <= LIMITS.max_minutes_per_window


def test_declaring_zero_cannot_beat_the_minute_cap_either():
    """The same walk, but lying instead of omitting — it must not go further."""
    used = 0.0
    accepted = 0
    for _ in range(LIMITS.max_games_per_window):
        if upload(
            status(games_used=accepted, minutes_used=used),
            duration_seconds=0.0,
            size_bytes=2 * GB,
        ):
            break
        used += charge_for(LIMITS, 0.0, 2 * GB) / 60.0
        accepted += 1
    assert used <= LIMITS.max_minutes_per_window


# ── enforcement: the ledger the count comes from ─────────────────────────────


class _FakeResult:
    """Serves both shapes reserve_upload reads: the window aggregate (`one`)
    and the per-game idempotency lookup (`scalars().first()`)."""

    def __init__(self, row=(0, 0.0), existing=None):
        self._row = row
        self._existing = existing

    def one(self):
        return self._row

    def scalars(self):
        return self

    def first(self):
        return self._existing


class _RecordingSession:
    """Captures statements and added objects instead of running them."""

    def __init__(self, row=(0, 0.0), existing=None):
        self.statements = []
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self._row = row
        # A charge already on the game, as a retry or sibling would find.
        self._existing = existing

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(self._row, self._existing)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, _obj):
        return None


def _sql(session) -> str:
    return " ".join(str(s) for s in session.statements)


def test_usage_is_counted_from_upload_events_not_games():
    """The refund loop: games are hard-deleted, so counting them gave slots back.

    upload x5 -> 429 -> delete x5 -> counters read 0 -> repeat, with the GPU
    spend already incurred.
    """
    session = _RecordingSession()
    asyncio.run(quota_service.get_quota_status(session, uuid.uuid4(), LIMITS))
    sql = _sql(session)
    assert "upload_events" in sql
    assert "FROM games" not in sql


def test_usage_is_scoped_to_the_owner_and_the_window():
    session = _RecordingSession()
    asyncio.run(quota_service.get_quota_status(session, uuid.uuid4(), LIMITS))
    sql = _sql(session)
    assert "owner_id" in sql
    assert "created_at" in sql


def test_reserve_takes_the_lock_before_reading_the_quota():
    """Read-then-insert around a multi-GB upload let parallel requests all pass."""
    session = _RecordingSession()
    asyncio.run(
        quota_service.reserve_upload(
            session, uuid.uuid4(),
            game_id=uuid.uuid4(),
            content_type="video/mp4", size_bytes=GB, duration_seconds=600.0,
            limits=LIMITS,
        )
    )
    assert "pg_advisory_xact_lock" in str(session.statements[0])


def test_reserve_writes_the_charge_before_the_upload_starts():
    session = _RecordingSession()
    event, rejection = asyncio.run(
        quota_service.reserve_upload(
            session, uuid.uuid4(),
            game_id=uuid.uuid4(),
            content_type="video/mp4", size_bytes=GB, duration_seconds=600.0,
            limits=LIMITS,
        )
    )
    assert rejection is None
    assert event is not None
    assert session.added == [event]
    assert event.charged_seconds == 600.0
    assert session.commits == 1  # committed, so the lock is released


def test_reserve_prices_an_undeclared_duration_from_the_size():
    session = _RecordingSession()
    event, _ = asyncio.run(
        quota_service.reserve_upload(
            session, uuid.uuid4(),
            game_id=uuid.uuid4(),
            content_type="video/mp4", size_bytes=GB, duration_seconds=None,
            limits=LIMITS,
        )
    )
    assert event is not None
    assert event.charged_seconds == pytest.approx(GB * 8 / 1_000_000)


def test_reserve_is_idempotent_per_game():
    """A double-submit must share one charge, not compete for two slots.

    Charging twice was the mechanism behind the worst failure here: the second
    completion of the same upload was rejected on the slot the first had just
    taken, and its rejection path then deleted the object and the row out from
    under the completion that was about to enqueue them.
    """
    game_id = uuid.uuid4()
    already = quota_service.UploadEvent(
        id=uuid.uuid4(), owner_id=uuid.uuid4(), game_id=game_id, charged_seconds=1234.0
    )
    session = _RecordingSession(row=(5, 99999.0), existing=already)  # window full

    event, rejection = asyncio.run(
        quota_service.reserve_upload(
            session, uuid.uuid4(),
            game_id=game_id,
            content_type="video/mp4", size_bytes=GB, duration_seconds=600.0,
            limits=LIMITS,
        )
    )

    assert rejection is None, "an already-charged game is never re-rejected"
    assert event is already
    assert session.added == [], "and no second charge is written"


def test_a_rejected_reservation_claims_nothing():
    # Window already full: 5 of 5 used.
    session = _RecordingSession(row=(5, 0.0))
    event, rejection = asyncio.run(
        quota_service.reserve_upload(
            session, uuid.uuid4(),
            game_id=uuid.uuid4(),
            content_type="video/mp4", size_bytes=GB, duration_seconds=600.0,
            limits=LIMITS,
        )
    )
    assert event is None
    assert rejection is not None and rejection[0] == 429
    assert session.added == []
    assert session.commits == 0
    assert session.rollbacks == 1  # lock dropped without writing


