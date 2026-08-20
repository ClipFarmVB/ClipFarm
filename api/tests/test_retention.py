"""CF-194: raw uploads are deleted once raw_upload_retention_days elapses.

Two layers, because the risk sits in two different places:

* the orchestration (below) — what the sweep deletes, and in what order — is
  exercised against fakes for the DB and storage seams;
* the selection SQL (bottom of the file) is exercised against a real database,
  since a widened status filter or a flipped comparison there is silent data
  loss that no amount of mocking would catch.

Run from the api/ dir: `cd api && pytest tests/test_retention.py`.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("celery")

from app.workers import tasks  # noqa: E402

NOW = datetime.now(timezone.utc)
RETENTION_DAYS = 7
CUTOFF = NOW - timedelta(days=RETENTION_DAYS)
OLD = NOW - timedelta(days=30)
RECENT = NOW - timedelta(hours=1)


def _raiser(exc):
    def _raise(*_args, **_kwargs):
        raise exc

    return _raise


def _row(name="a", *, last_modified=OLD):
    """A game and its object, as the two passes see them."""
    return {
        "id": uuid.uuid4(),
        "url": f"https://cdn.example/raw/{name}.mp4",
        "key": f"raw/{name}.mp4",
        "obj": {"key": f"raw/{name}.mp4", "last_modified": last_modified},
    }


class _Fakes:
    """Stands in for the three _sync_db helpers and the two storage calls."""

    def __init__(self, expired=(), objects=(), referenced=(), clear_ok=True):
        self._expired = list(expired)
        self._objects = list(objects)
        self._referenced = set(referenced)
        self._clear_ok = clear_ok
        self.cutoffs = []
        self.cleared = []
        self.deleted = []
        self.delete_batches = []
        self.calls = []

    # ── _sync_db ────────────────────────────────────────────────────────────
    def expired_raw_uploads(self, cutoff):
        self.calls.append("select")
        self.cutoffs.append(cutoff)
        return list(self._expired)

    def clear_raw_video_url(self, game_id, expected_url):
        self.calls.append("clear")
        if not self._clear_ok:
            return False
        self.cleared.append(game_id)
        # Releasing the row is what makes its object unreferenced.
        self._referenced.discard("raw/" + expected_url.rsplit("/", 1)[-1])
        return True

    def referenced_raw_keys(self):
        self.calls.append("referenced")
        return set(self._referenced)

    # ── storage ─────────────────────────────────────────────────────────────
    def list_objects(self, prefix):
        self.calls.append("list")
        return iter([o for o in self._objects if o["key"].startswith(prefix)])

    def delete_files(self, keys):
        keys = list(keys)
        self.calls.append("delete")
        self.delete_batches.append(keys)
        self.deleted.extend(keys)
        return []


def _install(monkeypatch, fakes, retention_days=RETENTION_DAYS):
    from app.config import settings
    from app.services import storage
    from app.workers import _sync_db

    monkeypatch.setattr(settings, "raw_upload_retention_days", retention_days)
    monkeypatch.setattr(_sync_db, "sync_expired_raw_uploads", fakes.expired_raw_uploads)
    monkeypatch.setattr(_sync_db, "sync_clear_raw_video_url", fakes.clear_raw_video_url)
    monkeypatch.setattr(_sync_db, "sync_referenced_raw_keys", fakes.referenced_raw_keys)
    monkeypatch.setattr(storage, "list_objects", fakes.list_objects)
    monkeypatch.setattr(storage, "delete_files", fakes.delete_files)


def test_expired_upload_is_released_then_reclaimed(monkeypatch):
    r = _row()
    fakes = _Fakes(expired=[(r["id"], r["url"])], objects=[r["obj"]], referenced=[r["key"]])
    _install(monkeypatch, fakes)

    tasks._sweep_expired_raw_uploads()

    assert fakes.cleared == [r["id"]]
    assert fakes.deleted == [r["key"]]


def test_row_is_released_before_the_object_is_deleted(monkeypatch):
    """Order matters: an object deleted while its row still points at it turns
    every later trim into a download failure plus two Celery retries. The
    reverse only leaks storage, which the reclaim pass then recovers."""
    r = _row()
    fakes = _Fakes(expired=[(r["id"], r["url"])], objects=[r["obj"]], referenced=[r["key"]])
    _install(monkeypatch, fakes)

    tasks._sweep_expired_raw_uploads()

    assert fakes.calls.index("clear") < fakes.calls.index("delete")


def test_cutoff_matches_the_configured_window(monkeypatch):
    fakes = _Fakes()
    _install(monkeypatch, fakes, retention_days=30)

    tasks._sweep_expired_raw_uploads()

    assert abs((fakes.cutoffs[0] - (NOW - timedelta(days=30))).total_seconds()) < 60


def test_zero_retention_keeps_everything(monkeypatch):
    """0 means keep forever — the sweep must not even look."""
    r = _row()
    fakes = _Fakes(expired=[(r["id"], r["url"])], objects=[r["obj"]])
    _install(monkeypatch, fakes, retention_days=0)

    tasks._sweep_expired_raw_uploads()

    assert fakes.calls == []


def test_referenced_object_is_never_deleted(monkeypatch):
    """The row is the guard. An object a game still points at is live however
    old it is — a long-queued game's source, say, or a resumed upload."""
    live = _row("live")
    fakes = _Fakes(objects=[live["obj"]], referenced=[live["key"]])
    _install(monkeypatch, fakes)

    tasks._sweep_expired_raw_uploads()

    assert fakes.deleted == []


def test_recent_object_is_never_deleted(monkeypatch):
    """The second, independent guard: an object whose row has not been written
    yet is unreferenced for a moment, and must survive that moment."""
    inflight = _row("inflight", last_modified=RECENT)
    fakes = _Fakes(objects=[inflight["obj"]])
    _install(monkeypatch, fakes)

    tasks._sweep_expired_raw_uploads()

    assert fakes.deleted == []


def test_orphan_from_an_earlier_failed_delete_is_reclaimed(monkeypatch):
    """The reconciliation that makes null-first ordering safe.

    A worker killed between the two passes — or an R2 outage during the delete —
    leaves an object no row references. Without this pass it is billed forever,
    which is the exact cost the card set out to bound.
    """
    orphan = _row("orphan")
    fakes = _Fakes(objects=[orphan["obj"]])
    _install(monkeypatch, fakes)

    tasks._sweep_expired_raw_uploads()

    assert fakes.deleted == [orphan["key"]]


def test_objects_are_deleted_in_one_batched_call(monkeypatch):
    """Serial deletes would hold the task open for a round trip per object."""
    rows = [_row(f"o{i}") for i in range(25)]
    fakes = _Fakes(objects=[r["obj"] for r in rows])
    _install(monkeypatch, fakes)

    tasks._sweep_expired_raw_uploads()

    assert len(fakes.delete_batches) == 1
    assert sorted(fakes.deleted) == sorted(r["key"] for r in rows)


def test_nothing_is_deleted_without_the_reference_set(monkeypatch):
    """A failed reference read is indistinguishable from 'nothing is
    referenced', and acting on it would delete live uploads. Abort instead."""
    from app.workers import _sync_db

    orphan = _row("orphan")
    fakes = _Fakes(objects=[orphan["obj"]])
    _install(monkeypatch, fakes)
    monkeypatch.setattr(
        _sync_db, "sync_referenced_raw_keys", _raiser(RuntimeError("database down"))
    )

    tasks._sweep_expired_raw_uploads()

    assert fakes.deleted == []


def test_a_failed_release_leaves_its_object_alone(monkeypatch):
    """A False from the guarded update means the row changed underneath the
    sweep, so it is still referenced and the reclaim pass skips it."""
    r = _row()
    fakes = _Fakes(
        expired=[(r["id"], r["url"])],
        objects=[r["obj"]],
        referenced=[r["key"]],
        clear_ok=False,
    )
    _install(monkeypatch, fakes)

    tasks._sweep_expired_raw_uploads()

    assert fakes.deleted == []


def test_undeletable_objects_are_left_for_the_next_sweep(monkeypatch):
    """delete_files reports failures instead of raising — they stay unreferenced
    and past the cutoff, so the next sweep finds them again."""
    from app.services import storage

    rows = [_row("a"), _row("b")]
    fakes = _Fakes(objects=[r["obj"] for r in rows])
    _install(monkeypatch, fakes)
    monkeypatch.setattr(storage, "delete_files", lambda keys: ["raw/a.mp4"])

    tasks._sweep_expired_raw_uploads()  # must not raise


@pytest.mark.parametrize(
    "failing", ["sync_expired_raw_uploads", "sync_referenced_raw_keys", "list_objects"]
)
def test_sweep_never_raises(monkeypatch, failing):
    """Reporting must never break processing: process_game calls this once the
    game is already marked ready."""
    from app.services import storage
    from app.workers import _sync_db

    r = _row()
    fakes = _Fakes(expired=[(r["id"], r["url"])], objects=[r["obj"]])
    _install(monkeypatch, fakes)
    module = storage if failing == "list_objects" else _sync_db
    monkeypatch.setattr(module, failing, _raiser(RuntimeError("boom")))

    tasks._sweep_expired_raw_uploads()  # must not raise


# ─── The selection SQL, against a real database ──────────────────────────────
# The tests above mock these helpers away, which leaves the highest-consequence
# code in the change — the query that decides what gets deleted — unexercised.
# A widened status filter or a flipped comparison would ship as silent data loss
# with everything green. These run the real statements against in-memory SQLite:
# the `games` table alone, since the sweep touches nothing else and SQLite does
# not enforce the owner FK.

sa = pytest.importorskip("sqlalchemy")


@pytest.fixture
def db(monkeypatch):
    """An in-memory `games` table wired into the _sync_db helpers."""
    from sqlalchemy.orm import Session

    from app.models.game import Game, GameStatus
    from app.workers import _sync_db

    engine = sa.create_engine("sqlite://")
    Game.__table__.create(engine)
    monkeypatch.setattr(_sync_db, "_engine", engine)

    def add(**kwargs):
        row = dict(
            id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
            title="game",
            status=GameStatus.ready,
            raw_video_url=f"https://cdn.example/raw/{uuid.uuid4()}.mp4",
            created_at=OLD,
        )
        row.update(kwargs)
        with Session(engine) as s:
            s.add(Game(**row))
            s.commit()
        return row["id"], row["raw_video_url"]

    add.engine = engine  # type: ignore[attr-defined]
    return add


def test_selects_only_uploads_past_the_cutoff(db):
    from app.workers._sync_db import sync_expired_raw_uploads

    old_id, _ = db(created_at=NOW - timedelta(days=8))
    db(created_at=NOW - timedelta(days=6))

    assert [gid for gid, _ in sync_expired_raw_uploads(CUTOFF)] == [old_id]


def test_skips_games_that_still_need_their_source(db):
    """Only terminal states. An `uploading` row is a transfer in flight and a
    `queued`/`processing` one is about to read the file — deleting under either
    is the data loss this filter exists to prevent."""
    from app.models.game import GameStatus
    from app.workers._sync_db import sync_expired_raw_uploads

    ready_id, _ = db(status=GameStatus.ready)
    failed_id, _ = db(status=GameStatus.failed)
    for live in (GameStatus.uploading, GameStatus.queued, GameStatus.processing):
        db(status=live)

    assert sorted(str(gid) for gid, _ in sync_expired_raw_uploads(CUTOFF)) == sorted(
        [str(ready_id), str(failed_id)]
    )


def test_skips_rows_with_no_raw_upload(db):
    """An already-swept game must not come back on every later sweep."""
    from app.workers._sync_db import sync_expired_raw_uploads

    db(raw_video_url=None)

    assert sync_expired_raw_uploads(CUTOFF) == []


def test_clear_is_guarded_on_the_url_the_sweep_saw(db):
    from sqlalchemy.orm import Session

    from app.models.game import Game
    from app.workers._sync_db import sync_clear_raw_video_url

    gid, url = db()

    assert sync_clear_raw_video_url(gid, "https://cdn.example/raw/other.mp4") is False
    with Session(db.engine) as s:
        assert s.get(Game, gid).raw_video_url == url, "a mismatched URL must not clear it"

    assert sync_clear_raw_video_url(gid, url) is True
    with Session(db.engine) as s:
        assert s.get(Game, gid).raw_video_url is None

    assert sync_clear_raw_video_url(uuid.uuid4(), url) is False, "missing row"


def test_reference_set_covers_every_status(db):
    """The guard for the reclaim pass: an object referenced by ANY row is live,
    whatever its age or status — an in-flight upload most of all."""
    from app.models.game import GameStatus
    from app.workers._sync_db import sync_referenced_raw_keys

    _, uploading = db(status=GameStatus.uploading, created_at=NOW)
    _, ready = db(status=GameStatus.ready)
    db(raw_video_url=None)

    keys = sync_referenced_raw_keys()

    assert keys == {
        "raw/" + uploading.rsplit("/", 1)[-1],
        "raw/" + ready.rsplit("/", 1)[-1],
    }, "keys are stored-URL paths, not URLs"
