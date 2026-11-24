"""CF-321: every path that removes a clip also removes its phone rendition.

The retention sweep lists only `raw/`, so a `clips-mobile/` object one of these
paths misses is orphaned in R2 permanently — and nothing else would notice,
because the suite stayed green with `mobile_url` dropped from all three loops.
One assertion per site that the rendition's key is among the deletes: the
two routers, `sync_delete_game_clips`, and `process_game_task`'s purge of a game
deleted during processing.

Plus the recut's ordering: the column is cleared before the stale rendition is
purged, so a failure between the two can never leave `mobile_url` pointing at a
deleted object.

And the paths that store it: `process_game_task` uploads the rendition to its
own `clips-mobile/` key (never over the full-size clip) and records it on the
row, `sync_save_clips` persists the column, and a recut that returns a
rendition uploads and records it too.

Same house pattern as test_api_error_paths.py — stand-in sessions and direct
calls, no TestClient and no database.
"""
import asyncio
import sys
import types
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

OWNER = "owner-1"
MOBILE_KEY = "clips-mobile/g/a.mp4"
MOBILE_URL = f"https://example.invalid/{MOBILE_KEY}"


class _Game:
    def __init__(self):
        self.id = uuid.uuid4()
        self.owner_id = OWNER
        self.raw_video_url = None
        self.condensed_video_url = None
        self.upload_id = None


def _clip(game_id):
    return type("_Clip", (), {
        "id": uuid.uuid4(), "game_id": game_id,
        "clip_url": "https://example.invalid/clips/g/a.mp4",
        "thumbnail_url": "https://example.invalid/thumbs/g/a.jpg",
        "mobile_url": MOBILE_URL,
    })()


def test_deleting_a_game_deletes_its_clips_phone_renditions(monkeypatch):
    from app.routers import games
    from app.services import storage

    deleted: list[str] = []
    monkeypatch.setattr(storage, "delete_file", deleted.append)

    game = _Game()
    clip = _clip(game.id)

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [clip]

    class _DB:
        async def get(self, _model, _pk):
            return game

        async def execute(self, _q):
            return _Result()

        async def delete(self, _obj):
            return None

        async def commit(self):
            return None

    asyncio.run(games.delete_game(game.id, OWNER, _DB()))

    assert MOBILE_KEY in deleted, deleted


def test_bulk_deleting_clips_deletes_their_phone_renditions(monkeypatch):
    from app.routers import clips
    from app.services import storage

    deleted: list[str] = []
    monkeypatch.setattr(storage, "delete_file", deleted.append)

    game = _Game()
    clip = _clip(game.id)

    class _Result:
        def all(self):
            return [(clip, game)]

    class _DB:
        async def execute(self, _q):
            return _Result()

        async def delete(self, _obj):
            return None

        async def commit(self):
            return None

    body = type("_Body", (), {"clip_ids": [clip.id]})()
    asyncio.run(clips.delete_clips(body, _DB(), OWNER))

    assert MOBILE_KEY in deleted, deleted


def test_reprocessing_a_game_returns_its_phone_renditions_for_purge(monkeypatch):
    """`sync_delete_game_clips` is what makes process_game idempotent; the URLs
    it returns are the only record of which objects to purge."""
    from app.workers import _sync_db

    clip = _clip(uuid.uuid4())

    class _Query:
        def filter(self, *_a):
            return self

        def all(self):
            return [clip]

    class _Session:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def query(self, _model):
            return _Query()

        def delete(self, _obj):
            return None

        def commit(self):
            return None

    monkeypatch.setattr(_sync_db, "Session", _Session)

    urls = _sync_db.sync_delete_game_clips(uuid.uuid4())

    assert MOBILE_URL in urls, urls


def test_a_recut_without_a_rendition_clears_the_column_before_purging(monkeypatch, tmp_path):
    """Delete-then-write leaves `mobile_url` pointing at a deleted object if the
    write fails and the retries run out; write-then-delete leaves at worst an
    orphan under `clips-mobile/`, which is recoverable by hand."""
    from app.services import storage
    from app.workers import _sync_db, tasks

    events: list[tuple] = []

    clip_file = Path(tmp_path) / "recut.mp4"
    clip_file.write_bytes(b"")

    def _recut_single(*_a, **_k):
        return clip_file, None, None  # no rendition this time

    fake_clip = types.ModuleType("ml.pipeline.clip")
    fake_clip.recut_single = _recut_single  # type: ignore[attr-defined]
    fake_pipeline = types.ModuleType("ml.pipeline")
    fake_pipeline.clip = fake_clip  # type: ignore[attr-defined]
    fake_ml = types.ModuleType("ml")
    fake_ml.pipeline = fake_pipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ml", fake_ml)
    monkeypatch.setitem(sys.modules, "ml.pipeline", fake_pipeline)
    monkeypatch.setitem(sys.modules, "ml.pipeline.clip", fake_clip)

    monkeypatch.setattr(storage, "download_file", lambda *_a, **_k: None)
    monkeypatch.setattr(
        storage, "upload_file", lambda _p, key, _ct: f"https://example.invalid/{key}"
    )
    monkeypatch.setattr(storage, "delete_file", lambda key: events.append(("delete", key)))
    monkeypatch.setattr(
        _sync_db, "sync_update_clip_url",
        lambda cid, url, thumb=None, mobile_url="unset": events.append(("write", mobile_url)),
    )

    gid, cid = uuid.uuid4(), uuid.uuid4()
    tasks.recut_clip_task.run(
        str(cid), str(gid), "https://example.invalid/raw/x.mp4", 1.0, 5.0,
    )

    mobile_key = storage.mobile_clip_key(gid, cid)
    assert events == [("write", None), ("delete", mobile_key)], events


# ── storing it: process_game_task's upload-and-save step (R6-M1, R6-M2) ────
#
# `process_game_task` is driven in-process with every stage stubbed, the same
# seam `_drive_failing_task` in test_worker_safety.py uses: its DB helpers are
# imported inside the task body, so patching `app.workers._sync_db` reaches
# them. `generate_clips` is a stand-in that writes one clip, one thumbnail and
# one rendition, so what is under test is only what the worker does with them.


def _drive_process_game(monkeypatch, tmp_path, *, exists):
    """Run `process_game_task` to completion with the pipeline stubbed out.

    `exists` answers `sync_game_exists` call by call: the first is the probe
    above the task's `try`, the second the "deleted during processing" check
    after the uploads. Returns what the worker uploaded, deleted and saved, the
    kwargs `generate_clips` was called with, and how many times the task reached
    the retention sweep at the end of its success path.

    The sweep is stubbed: unstubbed it opens `settings.database_url` (which
    `.env` may point at Supabase), clears `raw_video_url` on expired games and
    lists the real `raw/` prefix. Counting its calls also proves the success
    path ran to its end rather than returning early.
    """
    from app.config import settings
    from app.services import storage
    from app.workers import _sync_db, locks, tasks

    record: dict = {
        "uploads": {}, "deleted": [], "saved": [], "gen_kwargs": None, "sweeps": 0,
    }

    fake_cv2 = types.ModuleType("cv2")
    fake_cv2.CAP_PROP_FPS = 5  # type: ignore[attr-defined]
    fake_cv2.CAP_PROP_FRAME_COUNT = 7  # type: ignore[attr-defined]
    fake_cv2.CAP_PROP_FRAME_HEIGHT = 4  # type: ignore[attr-defined]

    class _Cap:
        def __init__(self, _path):
            pass

        def get(self, prop):
            return {5: 30.0, 7: 300, 4: 1080}[prop]

        def release(self):
            return None

    fake_cv2.VideoCapture = _Cap  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    def _generate_clips(_video, _detections, out_dir, **kwargs):
        record["gen_kwargs"] = kwargs
        out = Path(out_dir)
        paths = {}
        for name in ("clip.mp4", "thumb.jpg", "clip.mobile.mp4"):
            p = out / name
            p.write_bytes(name.encode())
            paths[name] = p
        return [{
            "clip_path": paths["clip.mp4"],
            "thumb_path": paths["thumb.jpg"],
            "mobile_path": paths["clip.mobile.mp4"],
            "action": "spike", "confidence": 0.9, "highlight_score": 0.8,
            "start": 1.0, "end": 5.0, "labels": [],
        }]

    fake_clip = types.ModuleType("ml.pipeline.clip")
    fake_clip.generate_clips = _generate_clips  # type: ignore[attr-defined]
    fake_pipeline = types.ModuleType("ml.pipeline")
    fake_pipeline.clip = fake_clip  # type: ignore[attr-defined]
    fake_ml = types.ModuleType("ml")
    fake_ml.pipeline = fake_pipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ml", fake_ml)
    monkeypatch.setitem(sys.modules, "ml.pipeline", fake_pipeline)
    monkeypatch.setitem(sys.modules, "ml.pipeline.clip", fake_clip)

    class _FakeLock:
        def __init__(self, *_a, **_k):
            pass

        def acquire(self):
            return True

        def release(self):
            return None

        def still_held(self):
            return True

    answers = iter(exists)
    monkeypatch.setattr(locks, "GameLock", _FakeLock)
    monkeypatch.setattr(_sync_db, "sync_game_exists", lambda _gid: next(answers))
    monkeypatch.setattr(_sync_db, "sync_set_game_status", lambda *_a, **_k: None)
    monkeypatch.setattr(_sync_db, "sync_set_game_progress", lambda *_a, **_k: None)
    monkeypatch.setattr(_sync_db, "sync_set_original_duration", lambda *_a, **_k: None)
    monkeypatch.setattr(_sync_db, "sync_settle_upload_charge", lambda *_a, **_k: None)
    monkeypatch.setattr(_sync_db, "sync_delete_game_clips", lambda _gid: [])
    monkeypatch.setattr(_sync_db, "sync_save_clips", record["saved"].extend)

    def _download(_key, local_path):
        Path(local_path).write_bytes(b"raw")

    def _upload(local_path, key, _ct):
        record["uploads"][key] = Path(local_path).name
        return f"https://example.invalid/{key}"

    monkeypatch.setattr(storage, "download_file", _download)
    monkeypatch.setattr(storage, "upload_file", _upload)
    monkeypatch.setattr(storage, "delete_file", record["deleted"].append)

    monkeypatch.delenv("ROBOFLOW_API_KEY", raising=False)
    def _sweep():
        record["sweeps"] += 1

    monkeypatch.setattr(tasks, "_sweep_expired_raw_uploads", _sweep)
    monkeypatch.setattr(tasks, "_will_attempt_modal", lambda *_a, **_k: False)
    monkeypatch.setattr(tasks, "_pose_first_fallback", lambda *_a, **_k: [])
    monkeypatch.setattr(tasks, "_refine_with_pose", lambda _v, _k, dets, *_a, **_kw: dets)
    # A value the default would not produce, so the assertion below can only
    # pass if the setting itself reached the pipeline.
    monkeypatch.setattr(settings, "mobile_short_side", 480)

    monkeypatch.setattr(tasks.process_game_task, "max_retries", 0)
    monkeypatch.setattr(tasks.celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(tasks.celery_app.conf, "task_eager_propagates", False)

    gid = uuid.uuid4()
    result = tasks.process_game_task.apply(
        args=[str(gid), "https://example.invalid/raw/x.mp4"]
    )
    assert result.successful(), result.result
    return gid, record


def test_a_processed_clip_uploads_and_records_its_phone_rendition(monkeypatch, tmp_path):
    """Acceptance box 1's "stored" half: the rendition goes to its own key,
    the full-size clip is not overwritten, and the saved row points at it."""
    from app.services import storage

    gid, rec = _drive_process_game(monkeypatch, tmp_path, exists=[True, True])

    assert rec["gen_kwargs"]["mobile_short_side"] == 480, rec["gen_kwargs"]

    assert len(rec["saved"]) == 1, rec["saved"]
    row = rec["saved"][0]
    cid = row["id"]
    mobile_key = storage.mobile_clip_key(gid, cid)
    clip_key = storage.clip_key(gid, cid)
    assert mobile_key.startswith("clips-mobile/")

    assert rec["uploads"].get(mobile_key) == "clip.mobile.mp4", rec["uploads"]
    assert rec["uploads"].get(clip_key) == "clip.mp4", rec["uploads"]
    assert row["mobile_url"] == f"https://example.invalid/{mobile_key}", row
    assert row["clip_url"] == f"https://example.invalid/{clip_key}", row
    assert rec["deleted"] == [], rec["deleted"]
    # The success path ran to its last step, the (stubbed) retention sweep.
    assert rec["sweeps"] == 1, rec["sweeps"]


def test_a_game_deleted_during_processing_purges_its_phone_renditions(monkeypatch, tmp_path):
    """The fourth delete site: clips uploaded for a game deleted mid-run are
    discarded, and the rendition must go with them or nothing ever will."""
    from app.services import storage

    gid, rec = _drive_process_game(monkeypatch, tmp_path, exists=[True, False])

    assert rec["saved"] == [], "a deleted game's clips must not be saved"
    mobile_keys = [k for k in rec["uploads"] if k.startswith("clips-mobile/")]
    assert len(mobile_keys) == 1, rec["uploads"]
    assert mobile_keys[0] in rec["deleted"], rec["deleted"]
    # And the other two, so the assertion above is not satisfied by accident.
    assert set(rec["deleted"]) == set(rec["uploads"]), (rec["deleted"], rec["uploads"])
    cid = uuid.UUID(Path(mobile_keys[0]).stem)
    assert mobile_keys[0] == storage.mobile_clip_key(gid, cid)
    # The discard path returns before the sweep, which runs on success only.
    assert rec["sweeps"] == 0, rec["sweeps"]


def test_saving_a_clip_row_persists_its_phone_rendition(monkeypatch):
    from app.workers import _sync_db

    added: list = []

    class _Session:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def add(self, obj):
            added.append(obj)

        def commit(self):
            return None

    monkeypatch.setattr(_sync_db, "Session", _Session)

    _sync_db.sync_save_clips([{
        "id": uuid.uuid4(), "game_id": uuid.uuid4(), "action_type": "spike",
        "confidence": 0.9, "start_time": 1.0, "end_time": 5.0,
        "clip_url": "https://example.invalid/clips/g/a.mp4",
        "thumbnail_url": "https://example.invalid/thumbs/g/a.jpg",
        "mobile_url": MOBILE_URL,
    }])

    assert len(added) == 1
    assert added[0].mobile_url == MOBILE_URL


def test_a_recut_with_a_rendition_uploads_and_records_it(monkeypatch, tmp_path):
    """The sibling of the no-rendition recut above: a new rendition goes to the
    `clips-mobile/` key, the column is written with it, and nothing is purged."""
    from app.config import settings
    from app.services import storage
    from app.workers import _sync_db, tasks

    uploads: dict[str, str] = {}
    deleted: list[str] = []
    writes: list[tuple] = []
    seen: dict = {}

    out = Path(tmp_path)
    paths = {}
    for name in ("recut.mp4", "recut.jpg", "recut.mobile.mp4"):
        p = out / name
        p.write_bytes(name.encode())
        paths[name] = p

    def _recut_single(*_a, **kwargs):
        seen.update(kwargs)
        return paths["recut.mp4"], paths["recut.jpg"], paths["recut.mobile.mp4"]

    fake_clip = types.ModuleType("ml.pipeline.clip")
    fake_clip.recut_single = _recut_single  # type: ignore[attr-defined]
    fake_pipeline = types.ModuleType("ml.pipeline")
    fake_pipeline.clip = fake_clip  # type: ignore[attr-defined]
    fake_ml = types.ModuleType("ml")
    fake_ml.pipeline = fake_pipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ml", fake_ml)
    monkeypatch.setitem(sys.modules, "ml.pipeline", fake_pipeline)
    monkeypatch.setitem(sys.modules, "ml.pipeline.clip", fake_clip)

    def _upload(local_path, key, _ct):
        uploads[key] = Path(local_path).name
        return f"https://example.invalid/{key}"

    monkeypatch.setattr(storage, "download_file", lambda *_a, **_k: None)
    monkeypatch.setattr(storage, "upload_file", _upload)
    monkeypatch.setattr(storage, "delete_file", deleted.append)
    monkeypatch.setattr(
        _sync_db, "sync_update_clip_url",
        lambda cid, url, thumb=None, mobile_url="unset": writes.append((url, thumb, mobile_url)),
    )
    monkeypatch.setattr(settings, "mobile_short_side", 480)

    gid, cid = uuid.uuid4(), uuid.uuid4()
    tasks.recut_clip_task.run(
        str(cid), str(gid), "https://example.invalid/raw/x.mp4", 1.0, 5.0,
    )

    mobile_key = storage.mobile_clip_key(gid, cid)
    clip_key = storage.clip_key(gid, cid)
    assert seen.get("mobile_short_side") == 480, seen
    assert uploads.get(mobile_key) == "recut.mobile.mp4", uploads
    assert uploads.get(clip_key) == "recut.mp4", uploads
    assert writes == [(
        f"https://example.invalid/{clip_key}",
        f"https://example.invalid/{storage.thumbnail_key(gid, cid)}",
        f"https://example.invalid/{mobile_key}",
    )], writes
    assert deleted == [], deleted
