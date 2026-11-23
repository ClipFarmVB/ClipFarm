"""CF-321: every path that removes a clip also removes its phone rendition.

The retention sweep lists only `raw/`, so a `clips-mobile/` object one of these
paths misses is orphaned in R2 permanently — and nothing else would notice,
because the suite stayed green with `mobile_url` dropped from all three loops.
One assertion per site that the rendition's key is among the deletes.

Plus the recut's ordering: the column is cleared before the stale rendition is
purged, so a failure between the two can never leave `mobile_url` pointing at a
deleted object.

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
