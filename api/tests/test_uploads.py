"""CF-163: presigned direct-to-R2 uploads.

The api never sees the bytes any more, so the only things standing between a
user and a multi-GB GPU job are the presign-time checks and the completion-time
confirmation. These drive the handlers directly with a fake session and a fake
storage layer — no DB, no network, in keeping with the rest of api/tests.

Run from api/: pytest tests/test_uploads.py
"""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("celery")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy.sql.dml import Update  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.game import Game, GameStatus  # noqa: E402
from app.routers import games as games_router  # noqa: E402
from app.schemas.game import CompletedPart, UploadComplete, UploadCreate  # noqa: E402
from app.services import storage  # noqa: E402

USER = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_USER = uuid.UUID("00000000-0000-0000-0000-0000000000ff")


# ── Fakes ────────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _UpdateResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


def _where_columns(stmt) -> set[str]:
    """Column names compared in a statement's WHERE clause."""
    where = stmt.whereclause
    if where is None:
        return set()
    clauses = getattr(where, "clauses", [where])
    return {c.left.name for c in clauses if hasattr(c, "left")}


class FakeDB:
    """Just enough AsyncSession for the upload handlers."""

    def __init__(self, *games: Game, stale: list[Game] | None = None):
        self.games = {g.id: g for g in games}
        self.stale = stale or []          # what the sweep's SELECT returns
        self.added: list[Game] = []
        self.deleted: list[Game] = []
        self.commits = 0

    async def execute(self, stmt):
        if isinstance(stmt, Update):
            return self._claim(stmt)
        rows, self.stale = self.stale, []  # one sweep per test
        return _Result(rows)

    def _claim(self, stmt) -> _UpdateResult:
        """Model the completion handler's conditional UPDATE:

            UPDATE games SET status='queued', upload_id=NULL
             WHERE id=:id AND status='uploading'

        The `status` predicate is read off the statement rather than assumed.
        Hardcoding it here would make the fake enforce atomicity that the
        production code might not have, and the concurrency test below would
        then pass even against an unguarded UPDATE — proving nothing.
        """
        guarded = "status" in _where_columns(stmt)
        claimed = [
            g for g in self.games.values()
            if not guarded or g.status == GameStatus.uploading
        ]
        for g in claimed:
            g.status = GameStatus.queued
            g.upload_id = None
        return _UpdateResult(len(claimed))

    def add(self, obj):
        self.added.append(obj)
        self.games[obj.id] = obj

    async def get(self, _model, pk):
        return self.games.get(pk)

    async def delete(self, obj):
        self.deleted.append(obj)
        self.games.pop(obj.id, None)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass

    async def rollback(self):
        pass


class FakeTask:
    def __init__(self):
        self.calls = []

    def delay(self, *args, **kwargs):
        self.calls.append((args, kwargs))


@pytest.fixture
def fake_storage(monkeypatch):
    """Stub every R2 call the handlers make, recording what was asked for."""
    calls: dict[str, list] = {
        "presign_put": [], "create_multipart": [], "presign_part": [],
        "complete_multipart": [], "abort_multipart": [], "delete_file": [],
    }
    head: dict[str, dict | None] = {"value": {"size": 1024, "content_type": "video/mp4"}}

    def presign_put(key, content_type, expires_in):
        calls["presign_put"].append((key, content_type, expires_in))
        return f"https://r2.test/{key}?sig=single"

    def create_multipart(key, content_type):
        calls["create_multipart"].append((key, content_type))
        return "upload-id-123"

    def presign_upload_part(key, upload_id, part_number, expires_in):
        calls["presign_part"].append((key, upload_id, part_number))
        return f"https://r2.test/{key}?partNumber={part_number}"

    monkeypatch.setattr(storage, "presign_put", presign_put)
    monkeypatch.setattr(storage, "create_multipart", create_multipart)
    monkeypatch.setattr(storage, "presign_upload_part", presign_upload_part)
    monkeypatch.setattr(storage, "complete_multipart",
                        lambda *a: calls["complete_multipart"].append(a))
    monkeypatch.setattr(storage, "abort_multipart",
                        lambda *a: calls["abort_multipart"].append(a))
    monkeypatch.setattr(storage, "delete_file",
                        lambda k: calls["delete_file"].append(k))
    monkeypatch.setattr(storage, "head_object", lambda _k: head["value"])

    calls["_head"] = head  # type: ignore[assignment]
    return calls


@pytest.fixture
def fake_task(monkeypatch):
    task = FakeTask()
    monkeypatch.setattr(games_router, "process_game_task", task)
    return task


def _create(size_bytes: int, content_type: str = "video/mp4", filename: str = "game.mp4"):
    return UploadCreate(
        title="Varsity vs Lincoln",
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        condense=False,
    )


def _uploading_game(**kw) -> Game:
    # progress/created_at are set explicitly: their column defaults are applied
    # by SQLAlchemy at flush time, and these rows are never flushed.
    defaults = dict(
        id=uuid.uuid4(),
        owner_id=USER,
        title="Varsity vs Lincoln",
        status=GameStatus.uploading,
        raw_video_url=f"{settings.r2_public_url}/raw/abc.mp4",
        condense_requested=False,
        upload_id=None,
        progress=0.0,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    return Game(**defaults)


# ── Part planning ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "size,part,expected",
    [
        (0, 100, 1),        # S3 rejects a multipart upload with zero parts
        (1, 100, 1),
        (100, 100, 1),      # exact multiple must not produce a trailing empty part
        (101, 100, 2),
        (250, 100, 3),
        (2 * 1024**3, 100 * 1024**2, 21),
    ],
)
def test_plan_part_count(size, part, expected):
    assert storage.plan_part_count(size, part) == expected


def test_plan_part_count_rejects_nonpositive_part_size():
    with pytest.raises(ValueError):
        storage.plan_part_count(100, 0)


# ── Key derivation ───────────────────────────────────────────────────────────


def test_game_raw_key_keeps_the_extension():
    gid = uuid.uuid4()
    assert storage.game_raw_key(gid, "game.mov") == f"raw/{gid}.mov"


def test_sanitized_filename_cannot_escape_the_key_prefix():
    gid = uuid.uuid4()
    hostile = games_router._sanitize_filename("../../etc/passwd.mp4")
    key = storage.game_raw_key(gid, hostile)
    assert key == f"raw/{gid}.mp4"
    assert ".." not in key and key.count("/") == 1


@pytest.mark.parametrize(
    "filename,expected_ext",
    [
        ("game.MP4", ".mp4"),
        ("game.mp4?x=1", ".mp4x1"),   # would otherwise truncate on urlparse
        ("game.mp4#frag", ".mp4frag"),
        ("game", ""),                  # no extension at all
        ("archive.tar.gz", ".gz"),
    ],
)
def test_raw_key_extension_survives_a_urlparse_round_trip(filename, expected_ext):
    """`raw_video_url` is parsed back into a key everywhere downstream, so a key
    containing '?' or '#' would point at an object that does not exist."""
    from urllib.parse import urlparse

    gid = uuid.uuid4()
    key = storage.game_raw_key(gid, filename)
    assert key == f"raw/{gid}{expected_ext}"
    assert urlparse(f"https://cdn.test/{key}").path.lstrip("/") == key


# ── Route ordering ───────────────────────────────────────────────────────────


def test_upload_config_is_declared_before_the_game_id_route():
    """FastAPI matches in declaration order: if `/games/{game_id}` came first,
    `/games/upload-config` would be parsed as a UUID and 422."""
    paths = [getattr(r, "path", "") for r in games_router.router.routes]
    assert paths.index("/games/upload-config") < paths.index("/games/{game_id}")


# ── Presign: rejection before any bytes move ─────────────────────────────────


def test_rejects_unsupported_content_type(fake_storage):
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.create_upload(_create(1024, "application/zip"), USER, db))
    assert exc.value.status_code == 415
    # Nothing was signed and no row was created — the rejection is free.
    assert not fake_storage["presign_put"] and not fake_storage["create_multipart"]
    assert db.added == []


def test_rejects_declared_size_over_cap(fake_storage, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 1024)
    db = FakeDB()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.create_upload(_create(1025), USER, db))
    assert exc.value.status_code == 413
    assert not fake_storage["presign_put"] and db.added == []


# ── Presign: the two transfer modes ──────────────────────────────────────────


def test_small_file_gets_a_single_put(fake_storage, monkeypatch):
    monkeypatch.setattr(settings, "single_put_max_bytes", 1000)
    db = FakeDB()
    ticket = asyncio.run(games_router.create_upload(_create(999), USER, db))

    assert ticket.mode == "single"
    assert ticket.upload_url and not ticket.parts and ticket.upload_id is None
    assert not fake_storage["create_multipart"], "no multipart bookkeeping for a small file"

    game = db.added[0]
    assert game.status == GameStatus.uploading, "nothing is queueable until the object exists"
    assert game.upload_id is None


def test_large_file_gets_multipart_with_one_url_per_part(fake_storage, monkeypatch):
    monkeypatch.setattr(settings, "single_put_max_bytes", 1000)
    monkeypatch.setattr(settings, "upload_part_size_bytes", 500)
    monkeypatch.setattr(settings, "max_upload_bytes", 10_000)
    db = FakeDB()
    ticket = asyncio.run(games_router.create_upload(_create(1200), USER, db))

    assert ticket.mode == "multipart"
    assert ticket.upload_id == "upload-id-123"
    assert ticket.part_size_bytes == 500
    assert [p.part_number for p in ticket.parts] == [1, 2, 3]
    # Stored so a delete can abort the upload instead of leaking billable parts.
    assert db.added[0].upload_id == "upload-id-123"


def test_content_type_is_signed_into_the_url(fake_storage, monkeypatch):
    """R2 enforces the signed Content-Type, which is what makes the file-type
    check a real control rather than an advisory one."""
    monkeypatch.setattr(settings, "single_put_max_bytes", 10_000)
    asyncio.run(games_router.create_upload(_create(100, "video/webm"), USER, FakeDB()))
    _key, content_type, _ttl = fake_storage["presign_put"][0]
    assert content_type == "video/webm"


def test_content_type_parameters_are_ignored(fake_storage, monkeypatch):
    """Browsers may send `video/mp4; codecs=...`; the bare type is what counts."""
    monkeypatch.setattr(settings, "single_put_max_bytes", 10_000)
    ticket = asyncio.run(
        games_router.create_upload(_create(100, "video/mp4; codecs=avc1"), USER, FakeDB())
    )
    assert ticket.content_type == "video/mp4"


# ── Abandoned-upload sweep ───────────────────────────────────────────────────


def test_presign_sweeps_the_callers_abandoned_uploads(fake_storage, monkeypatch):
    monkeypatch.setattr(settings, "single_put_max_bytes", 10_000)
    stale = _uploading_game(upload_id="old-upload")
    db = FakeDB(stale=[stale])

    asyncio.run(games_router.create_upload(_create(100), USER, db))

    assert stale in db.deleted, "stale upload row should be swept"
    assert fake_storage["abort_multipart"], "its multipart upload should be aborted"


def test_sweeping_a_single_put_upload_deletes_the_object(fake_storage, monkeypatch):
    """A single PUT that succeeded but was never completed leaves a real object.
    The row is the only record of its key, so deleting the row without the
    object strands it in the bucket, unreferenced and billed forever."""
    monkeypatch.setattr(settings, "single_put_max_bytes", 10_000)
    stale = _uploading_game(upload_id=None)  # single PUT — no multipart to abort
    db = FakeDB(stale=[stale])

    asyncio.run(games_router.create_upload(_create(100), USER, db))

    assert stale in db.deleted
    assert fake_storage["delete_file"] == ["raw/abc.mp4"]
    assert not fake_storage["abort_multipart"], "nothing to abort for a single PUT"


def test_multipart_is_aborted_when_the_row_cannot_be_written(fake_storage, monkeypatch):
    """The row is the only handle on a multipart upload — delete_game and the
    sweep both reach it through the row. If the insert fails, nothing else
    could ever abort it."""
    monkeypatch.setattr(settings, "single_put_max_bytes", 100)
    monkeypatch.setattr(settings, "upload_part_size_bytes", 500)
    monkeypatch.setattr(settings, "max_upload_bytes", 10_000)

    db = FakeDB()

    async def boom():
        raise RuntimeError("connection reset")

    db.commit = boom  # type: ignore[method-assign]

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.create_upload(_create(1200), USER, db))

    assert exc.value.status_code == 500
    assert fake_storage["abort_multipart"], "the orphaned multipart must be aborted"


def test_a_failing_sweep_never_blocks_a_new_upload(fake_storage, monkeypatch):
    monkeypatch.setattr(settings, "single_put_max_bytes", 10_000)
    monkeypatch.setattr(
        storage, "abort_multipart",
        lambda *a: (_ for _ in ()).throw(RuntimeError("R2 down")),
    )
    db = FakeDB(stale=[_uploading_game(upload_id="old-upload")])

    ticket = asyncio.run(games_router.create_upload(_create(100), USER, db))
    assert ticket.mode == "single"


# ── Completion: the guards before anything is queued ─────────────────────────


def test_completion_requires_ownership(fake_storage, fake_task):
    game = _uploading_game(owner_id=OTHER_USER)
    db = FakeDB(game)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))
    assert exc.value.status_code == 404
    assert fake_task.calls == []


def test_completion_is_not_repeatable(fake_storage, fake_task):
    """A duplicated completion call must not enqueue the pipeline twice."""
    game = _uploading_game(status=GameStatus.queued)
    db = FakeDB(game)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))
    assert exc.value.status_code == 409
    assert fake_task.calls == []


def test_completion_fails_when_no_object_landed(fake_storage, fake_task):
    fake_storage["_head"]["value"] = None
    game = _uploading_game()
    db = FakeDB(game)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))
    assert exc.value.status_code == 400
    assert fake_task.calls == [], "a failed transfer must never become a job"


def test_multipart_completion_requires_its_parts(fake_storage, fake_task):
    game = _uploading_game(upload_id="upload-id-123")
    db = FakeDB(game)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))
    assert exc.value.status_code == 400
    assert fake_task.calls == []


def test_oversize_object_is_deleted_and_rejected(fake_storage, fake_task, monkeypatch):
    """A presigned PUT cannot enforce Content-Length, so a client can declare a
    small file and upload a large one. This is where that is caught — after the
    transfer, but still before any GPU time is spent."""
    monkeypatch.setattr(settings, "max_upload_bytes", 1024)
    fake_storage["_head"]["value"] = {"size": 99_999, "content_type": "video/mp4"}
    game = _uploading_game()
    db = FakeDB(game)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))

    assert exc.value.status_code == 413
    assert fake_storage["delete_file"] == ["raw/abc.mp4"], "the object must not linger"
    assert game in db.deleted, "the row must not linger either"
    assert fake_task.calls == []


def test_successful_completion_queues_exactly_once(fake_storage, fake_task):
    game = _uploading_game(condense_requested=True)
    db = FakeDB(game)

    out = asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))

    assert out.status == GameStatus.queued
    assert game.upload_id is None, "nothing left to abort"
    assert len(fake_task.calls) == 1
    args, kwargs = fake_task.calls[0]
    assert args == (str(game.id), f"{settings.r2_public_url}/raw/abc.mp4")
    assert kwargs == {"condense": True}


def test_concurrent_completions_enqueue_exactly_once(fake_storage, fake_task):
    """The `status != uploading` check is a read-then-write: a HEAD (and, for
    multipart, an assembly call) sit between it and the commit, so two calls
    can both pass it while the row still reads `uploading`. Only the atomic
    claim keeps this to one job — and two GPU runs is what it costs otherwise.

    Sequential calls cannot show this; these interleave at the threadpool
    boundary that the real storage calls also yield on.
    """
    game = _uploading_game()
    db = FakeDB(game)

    async def both():
        return await asyncio.gather(
            games_router.complete_upload(game.id, UploadComplete(), USER, db),
            games_router.complete_upload(game.id, UploadComplete(), USER, db),
            return_exceptions=True,
        )

    results = asyncio.run(both())

    assert len(fake_task.calls) == 1, "the pipeline must be enqueued exactly once"
    conflicts = [r for r in results if isinstance(r, HTTPException)]
    assert [c.status_code for c in conflicts] == [409], "the loser gets a clean 409"


def test_successful_multipart_completion_assembles_then_queues(fake_storage, fake_task):
    game = _uploading_game(upload_id="upload-id-123")
    db = FakeDB(game)
    body = UploadComplete(
        parts=[CompletedPart(part_number=2, etag='"b"'), CompletedPart(part_number=1, etag='"a"')]
    )

    asyncio.run(games_router.complete_upload(game.id, body, USER, db))

    key, upload_id, parts = fake_storage["complete_multipart"][0]
    assert (key, upload_id) == ("raw/abc.mp4", "upload-id-123")
    assert [p["PartNumber"] for p in parts] == [2, 1], "storage layer does the sorting"
    assert len(fake_task.calls) == 1
