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
from sqlalchemy.sql.dml import Delete, Update  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.game import Game, GameStatus  # noqa: E402
from app.models.upload_event import UploadEvent  # noqa: E402
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

    def first(self):
        return self._rows[0] if self._rows else None


class _AggregateResult:
    """A one-row aggregate, as the CF-91 quota count returns."""

    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


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

    def __init__(
        self,
        *games: Game,
        stale: list[Game] | None = None,
        quota_usage: tuple[int, float] = (0, 0.0),
    ):
        self.games = {g.id: g for g in games}
        self.stale = stale or []          # what the sweep's SELECT returns
        self.quota_usage = quota_usage    # (accepted uploads, charged seconds)
        # A charge already on this game, as a retry or a sibling completion
        # would find. None = this is the first completion to get here.
        self.existing_reservation = None
        self.added: list = []
        self.deleted: list = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt):
        if isinstance(stmt, Update):
            return self._claim(stmt)
        if isinstance(stmt, Delete):
            return self._discard(stmt)
        sql = str(stmt)
        # CF-91 takes a per-user advisory lock before reading the quota; it
        # returns nothing the handler looks at.
        if "pg_advisory_xact_lock" in sql:
            return _AggregateResult((None,))
        if "upload_events" in sql:
            # Two different reads hit this table: the window aggregate, and the
            # per-game idempotency lookup that makes a double-submit share one
            # charge. Only the first is an aggregate.
            if "count(" in sql:
                return _AggregateResult(self.quota_usage)
            return _Result([self.existing_reservation] if self.existing_reservation else [])
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
            if isinstance(g, Game) and (not guarded or g.status == GameStatus.uploading)
        ]
        for g in claimed:
            g.status = GameStatus.queued
            g.upload_id = None
        return _UpdateResult(len(claimed))

    def _discard(self, stmt) -> _UpdateResult:
        """Model the quota-rejection disposal:

            DELETE FROM games WHERE id=:id AND status='uploading'

        Guarded the same way as the claim, and for the same reason: a sibling
        completion that already claimed this game must not have its row deleted
        out from under it. As with `_claim`, the predicate is read off the
        statement rather than assumed, so an unguarded DELETE would fail the
        test rather than quietly pass it.
        """
        guarded = "status" in _where_columns(stmt)
        doomed = [
            g for g in self.games.values()
            if isinstance(g, Game) and (not guarded or g.status == GameStatus.uploading)
        ]
        for g in doomed:
            self.deleted.append(g)
            self.games.pop(g.id, None)
        return _UpdateResult(len(doomed))

    def add(self, obj):
        self.added.append(obj)
        # Only games are addressable by get(); the CF-91 ledger row is written
        # through the same session but never read back by id.
        if isinstance(obj, Game):
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
        self.rollbacks += 1


class FakeTask:
    def __init__(self):
        self.calls = []

    def delay(self, *args, **kwargs):
        self.calls.append((args, kwargs))


# Container headers, hand-built rather than sliced off a fixture video: the
# check reads only the first few dozen bytes, so a real file would add megabytes
# to the repo to exercise the same handful.
ISO_BMFF_HEADER = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00"   # .mp4
QUICKTIME_HEADER = b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00"   # .mov
FRAGMENTED_MP4_HEADER = b"\x00\x00\x00\x18stypmsdh\x00\x00\x00\x00"
EBML_HEADER = b"\x1a\x45\xdf\xa3\x93\x42\x82\x88matroska"       # .mkv / .webm
ZIP_HEADER = b"PK\x03\x04\x14\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00\x00"


def _qt_header(lead: bytes, *, lead_size: int = 20) -> bytes:
    """A QuickTime file that opens with `lead` instead of `ftyp`.

    Shaped like the file measured in CF-244's review: a small leading box, then
    the `wide`/`mdat` pair ffmpeg's mov muxer writes. QTFF does not require
    `ftyp`, so each of these is a legal .mov rather than a contrived one.
    """
    return (
        lead_size.to_bytes(4, "big") + lead + b"\x00" * (lead_size - 8)
        + b"\x00\x00\x00\x08wide"
        + b"\x00\x00\x07\x54mdat"
    )


@pytest.fixture
def fake_storage(monkeypatch):
    """Stub every R2 call the handlers make, recording what was asked for."""
    calls: dict[str, list] = {
        "presign_put": [], "create_multipart": [], "presign_part": [],
        "complete_multipart": [], "abort_multipart": [], "delete_file": [],
    }
    head: dict[str, dict | None] = {"value": {"size": 1024, "content_type": "video/mp4"}}
    # CF-244: complete_upload now reads the object's first bytes to check the
    # container. Defaulted to a valid ISO-BMFF header so every test that merely
    # passes *through* completion keeps testing what it was written to test;
    # the tests that care set `_header["value"]` themselves.
    header: dict[str, bytes | None] = {"value": ISO_BMFF_HEADER}

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
    monkeypatch.setattr(storage, "head_bytes", lambda _k, _n: header["value"])

    calls["_head"] = head  # type: ignore[assignment]
    calls["_header"] = header  # type: ignore[assignment]
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


def test_the_declared_content_type_is_signed_into_the_url(fake_storage, monkeypatch):
    """R2 rejects a PUT whose Content-Type header differs from the signed value.

    Renamed from `test_content_type_is_signed_into_the_url`, whose name read as
    a claim that the file-type check was a real control over the upload. It
    binds the header only (CF-244) — a client that sends the header it signed
    can put anything behind it. What this pins is that the *declared* type
    reaches the signature, which is still worth pinning; the bytes are checked
    in test_a_non_video_upload_is_deleted_and_rejected.
    """
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


def test_sweeping_a_multipart_upload_deletes_the_object_too(fake_storage, monkeypatch):
    """`upload_id` set does not mean "no object exists yet".

    complete_multipart runs before the row is claimed, so a request that dies
    between assembly and the claim leaves a real object on a row that still
    carries an upload id. Aborting alone would leave it orphaned — the exact
    leak the single-PUT branch was added to close.
    """
    monkeypatch.setattr(settings, "single_put_max_bytes", 10_000)
    stale = _uploading_game(upload_id="assembled-already")
    db = FakeDB(stale=[stale])

    asyncio.run(games_router.create_upload(_create(100), USER, db))

    assert fake_storage["abort_multipart"], "still attempts the abort"
    assert fake_storage["delete_file"] == ["raw/abc.mp4"], "and deletes the object"
    assert stale in db.deleted


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


# ── CF-244: the stored bytes, not the declared type ─────────────────────────
#
# The presigned PUT binds the declared Content-Type to the signature as a
# HEADER. A client that sends the header it signed can put anything behind it,
# so these cover the only look the api takes at what was actually stored.


@pytest.mark.parametrize(
    "name,header",
    [
        ("mp4", ISO_BMFF_HEADER),
        ("mov (QuickTime brand)", QUICKTIME_HEADER),
        ("fragmented mp4 (styp)", FRAGMENTED_MP4_HEADER),
        ("matroska / webm", EBML_HEADER),
    ],
)
def test_every_allowed_container_family_completes(name, header, fake_storage, fake_task):
    """The guard must not be the thing that breaks a legitimate upload.

    The QuickTime case is the one to watch: browsers fill `File.type` from the
    file extension, so a .mov declared `video/mp4` is ordinary rather than
    adversarial, and a strict declared-vs-sniffed check would reject it.
    """
    fake_storage["_header"]["value"] = header
    game = _uploading_game()

    asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, FakeDB(game)))

    assert fake_storage["delete_file"] == []
    assert len(fake_task.calls) == 1


def test_a_non_video_upload_is_deleted_and_rejected(fake_storage, fake_task):
    """The bug CF-244 is about. The declared type passed the allowlist at
    presign and the signature verified, so nothing before this point looked at
    the bytes at all."""
    fake_storage["_header"]["value"] = ZIP_HEADER
    game = _uploading_game()
    db = FakeDB(game)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))

    assert exc.value.status_code == 415
    assert fake_storage["delete_file"] == ["raw/abc.mp4"]
    assert fake_task.calls == [], "a rejected upload must never reach the worker"


def test_a_zero_byte_upload_is_rejected(fake_storage, fake_task):
    """The case the fail-open path would otherwise hide.

    A ranged read of a zero-length object is unsatisfiable, so R2 answers 416,
    `head_bytes` returns None, and "unreadable header" waves it through — an
    empty file all the way to a GPU job. Decided from `head["size"]` instead,
    which also keeps the verdict independent of exactly how storage answers an
    unsatisfiable range.
    """
    fake_storage["_head"]["value"] = {"size": 0, "content_type": "video/mp4"}
    fake_storage["_header"]["value"] = None
    game = _uploading_game()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, FakeDB(game)))

    assert exc.value.status_code == 415
    assert fake_storage["delete_file"] == ["raw/abc.mp4"]
    assert fake_task.calls == []


def test_an_object_too_short_to_sniff_is_not_read_at_all(fake_storage, fake_task, monkeypatch):
    """Rejecting on size saves the ranged request rather than making one that
    cannot succeed."""
    reads: list = []
    monkeypatch.setattr(storage, "head_bytes", lambda k, n: reads.append((k, n)))
    fake_storage["_head"]["value"] = {"size": 4, "content_type": "video/mp4"}
    game = _uploading_game()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, FakeDB(game)))

    assert exc.value.status_code == 415
    assert reads == [], "no point asking storage for the header of a 4-byte object"


def test_an_unreadable_header_allows_the_upload_through(fake_storage, fake_task):
    """Fails OPEN, which is the opposite of what a None from head_object does
    four lines above it — and deliberately.

    No object at all means there is nothing to accept, so 400 is the only
    answer. A header that cannot be read means R2 would not hand over the head
    of an object it has already confirmed: the transfer completed, possibly 8 GB
    of it, and discarding that over a storage hiccup costs the user far more
    than letting a mislabelled file reach a worker that will fail to decode it.
    The cheaper wrong answer differs, so the direction does.

    It does mean the check is bypassable by anyone who can make the ranged read
    fail. That is the accepted cost, recorded here rather than left implicit.
    """
    fake_storage["_header"]["value"] = None
    game = _uploading_game()

    asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, FakeDB(game)))

    assert fake_storage["delete_file"] == []
    assert len(fake_task.calls) == 1


def test_a_truncated_or_misaligned_header_is_not_a_container():
    """Straight at the sniffer: driving these through the handler would only
    prove the fake returned what it was told to."""
    assert games_router._sniff_video_container(b"") is None
    assert games_router._sniff_video_container(b"\x00\x00\x00\x20fty") is None
    # `ftyp` present but not at offset 4 — a prefix check without the offset
    # would accept this.
    assert games_router._sniff_video_container(b"ftyp\x00\x00\x00\x20isom") is None


def test_the_sniff_reads_only_the_signature_window(fake_storage, fake_task, monkeypatch):
    """A ranged read, not a download. These objects run to 8 GB."""
    seen: list[tuple] = []

    def recording_head_bytes(key, count):
        seen.append((key, count))
        return ISO_BMFF_HEADER

    monkeypatch.setattr(storage, "head_bytes", recording_head_bytes)
    game = _uploading_game()

    asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, FakeDB(game)))

    assert seen == [("raw/abc.mp4", games_router.VIDEO_SIGNATURE_BYTES)]
    assert games_router.VIDEO_SIGNATURE_BYTES <= 64, (
        "the window only has to reach the ISO-BMFF brand at offset 8; a large "
        "read here is bytes transferred on every completion for nothing"
    )


# ── CF-244 review: QuickTime does not require `ftyp` ────────────────────────
#
# The first version of this check accepted ISO-BMFF only on a leading `ftyp` or
# `styp`. That is right for MP4 — ISO/IEC 14496-12 requires `ftyp` early — but
# `video/quicktime` is on the allowlist too and QTFF has no such requirement, so
# these files were being deleted with a 415 and no retry path.
#
# Demonstrated on real bytes before these were written: take an ffmpeg-produced
# .mov and replace its 20-byte `ftyp` with a same-length `free` box, so every
# chunk offset in the moov stays valid. ffprobe reports
# `format_name=mov,mp4,m4a,3gp,3g2,mj2`, duration intact, exit 0. The old sniff
# returned None for the same bytes.


@pytest.mark.parametrize("lead", [b"wide", b"moov", b"mdat", b"free", b"skip", b"pnot", b"junk"])
def test_quicktime_without_ftyp_is_a_video(lead):
    """Each of these may legally open a .mov. None of them is `ftyp`."""
    assert games_router._sniff_video_container(_qt_header(lead)) == "iso-bmff"


def test_a_quicktime_without_ftyp_completes(fake_storage, fake_task):
    """Through the handler, not just the sniffer: the cost of the old answer was
    paid on the reject branch, which deletes the row and the object."""
    fake_storage["_header"]["value"] = _qt_header(b"wide")
    game = _uploading_game()

    asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, FakeDB(game)))

    assert fake_storage["delete_file"] == [], "a legal .mov must not be destroyed"
    assert len(fake_task.calls) == 1


def test_an_unrecognised_leading_box_is_stepped_over():
    """A type list will always be narrower than the decoder's.

    ffmpeg does not match on the leading atom at all — it walks the chain — so
    the same .mov with its first atom relabelled to a type nothing recognises
    still probes as `mov,mp4,m4a,3gp,3g2,mj2` (checked with ffprobe). Anything
    that only widened the list would still delete that file.
    """
    assert games_router._sniff_video_container(_qt_header(b"XXXX")) == "iso-bmff"


def test_the_walk_needs_the_box_sizes_to_be_plausible():
    """What keeps the step-over from accepting anything at all.

    A box states its length before its type, so arbitrary bytes have to carry a
    chain of usable lengths to reach a type this recognises.
    """
    # Unknown type, and a size too small to be a box header — not a chain.
    assert games_router._sniff_video_container(b"\x00\x00\x00\x03XXXXmdat" + b"\x00" * 8) is None
    # Unknown type with size 0: "runs to end of file", so nothing follows it.
    assert games_router._sniff_video_container(b"\x00\x00\x00\x00XXXXmdat" + b"\x00" * 8) is None
    # A size that walks past the window rather than onto a known type.
    assert games_router._sniff_video_container(b"\x7f\xff\xff\xffXXXX" + b"\x00" * 56) is None


def test_still_not_a_video(fake_storage):
    """The widening must not have turned the check off."""
    assert games_router._sniff_video_container(ZIP_HEADER) is None
    assert games_router._sniff_video_container(b"not a video, just some prose about one." * 2) is None
    assert games_router._sniff_video_container(b"\x89PNG\r\n\x1a\n" + b"\x00" * 56) is None


def test_the_too_short_floor_is_not_the_read_window():
    """These are separate numbers on purpose.

    `too_short` drives the destructive branch; the window is sized for how far
    the sniff may have to walk. Tying them together would mean every widening of
    the read deleted more real files — a 40-byte object is not something to
    reject just because the sniff would have liked 64 bytes.
    """
    assert games_router.MIN_CONTAINER_BYTES <= games_router.VIDEO_SIGNATURE_BYTES
    assert games_router.MIN_CONTAINER_BYTES <= 16, (
        "this is the size below which an object is deleted unread; keep it at "
        "the shortest thing that could carry a container header"
    )


def test_oversize_object_is_deleted_and_rejected(fake_storage, fake_task, monkeypatch):
    """A presigned PUT cannot enforce Content-Length, so a client can declare a
    small file and upload a large one. This is where that is caught — after the
    transfer, but still before any GPU time is spent."""
    monkeypatch.setattr(settings, "max_upload_bytes", 8 * 1024**3)
    fake_storage["_head"]["value"] = {"size": 9 * 1024**3, "content_type": "video/mp4"}
    game = _uploading_game()
    db = FakeDB(game)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))

    assert exc.value.status_code == 413
    # CF-167: the same sentence the presign path and the dropzone would give,
    # naming the cap in the units the user was shown it in.
    assert exc.value.detail == "File is 9 GB; the maximum is 8 GB."
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


# ── CF-91: where the quota actually bites ────────────────────────────────────
#
# The policy table lives in test_quota.py. These cover the wiring — that the
# handlers call it at all, at the right point, and dispose of what they reject.
# Deleting the quota calls from the router leaves every test in test_quota.py
# green, so this is the half that notices.


def _full_window(monkeypatch):
    """Config where one upload is the whole allowance."""
    monkeypatch.setattr(settings, "quota_max_games_per_window", 1)
    monkeypatch.setattr(settings, "quota_max_minutes_per_window", 60.0)


def test_presign_refuses_a_user_who_is_already_over_quota(fake_storage, monkeypatch):
    """The point of checking at presign: no URL, so no 2 GB transfer to waste."""
    _full_window(monkeypatch)
    db = FakeDB(quota_usage=(1, 600.0))  # one upload already accepted

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.create_upload(_create(1024), USER, db))

    assert exc.value.status_code == 429
    assert not fake_storage["presign_put"], "no ticket is issued"
    assert db.added == [], "and no row is written"


def test_presign_refuses_a_video_over_the_duration_cap(fake_storage, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_duration_seconds", 3600.0)
    db = FakeDB()
    body = _create(1024)
    body.duration_seconds = 7200.0

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.create_upload(body, USER, db))

    assert exc.value.status_code == 413
    assert not fake_storage["presign_put"]


def test_presign_carries_the_declared_duration_onto_the_row(fake_storage):
    """It has to survive to completion, where the charge is taken.

    Reading it from the completion request instead would let a client declare
    a long video to get a ticket and a short one to be billed for it.
    """
    db = FakeDB()
    body = _create(1024)
    body.duration_seconds = 5400.0

    asyncio.run(games_router.create_upload(body, USER, db))

    assert db.added[0].declared_duration == 5400.0


def test_presign_does_not_charge_the_quota(fake_storage):
    """An abandoned ticket must cost nothing.

    Charging here would mean the sweep had to refund abandoned rows, which is
    the refundable-quota loop the ledger exists to prevent.
    """
    db = FakeDB()
    asyncio.run(games_router.create_upload(_create(1024), USER, db))

    assert [o for o in db.added if isinstance(o, UploadEvent)] == []


def test_completion_charges_the_quota_before_queueing(fake_storage, fake_task):
    game = _uploading_game(declared_duration=1800.0)
    db = FakeDB(game)

    asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))

    charged = [o for o in db.added if isinstance(o, UploadEvent)]
    assert len(charged) == 1
    assert charged[0].charged_seconds == 1800.0
    assert charged[0].game_id == game.id, "linked, so the worker can settle it"
    assert len(fake_task.calls) == 1


def test_completion_prices_an_undeclared_duration_from_the_file_size(
    fake_storage, fake_task, monkeypatch
):
    """MKV can't be probed in the browser, so this path is the common one.

    A flat maximum here charged a 3-minute clip four hours and blocked the
    user's day. The verified byte count stands in instead.
    """
    monkeypatch.setattr(settings, "max_upload_duration_seconds", 14400.0)
    game = _uploading_game(declared_duration=None)
    db = FakeDB(game)
    fake_storage["_head"]["value"] = {"size": 75_000_000, "content_type": "video/x-matroska"}

    asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))

    charged = [o for o in db.added if isinstance(o, UploadEvent)][0]
    # 75 MB at the low-bitrate assumption — minutes, not the 4 h cap.
    assert charged.charged_seconds == pytest.approx(600.0)
    assert charged.charged_seconds < 14400.0


def test_a_declared_duration_the_file_size_contradicts_is_not_believed(
    fake_storage, fake_task, monkeypatch
):
    """Declaring 0 (or a token value) was the way around the minute cap.

    Charging it at face value let one account queue max_games x max_duration
    against a much smaller minute allowance — the same defect the undeclared
    case was fixed for, reachable by sending a number instead of omitting one.
    """
    monkeypatch.setattr(settings, "max_upload_duration_seconds", 14400.0)
    game = _uploading_game(declared_duration=0.0)
    db = FakeDB(game)
    fake_storage["_head"]["value"] = {"size": 75_000_000, "content_type": "video/mp4"}

    asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))

    charged = [o for o in db.added if isinstance(o, UploadEvent)][0]
    assert charged.charged_seconds == pytest.approx(600.0), "priced from size, not the claim"


def test_completion_over_quota_discards_the_object_and_the_row(
    fake_storage, fake_task, monkeypatch
):
    """The upload raced past the presign preview — dispose, don't process."""
    _full_window(monkeypatch)
    game = _uploading_game(declared_duration=600.0)
    db = FakeDB(game, quota_usage=(1, 600.0))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))

    assert exc.value.status_code == 429
    assert fake_storage["delete_file"] == ["raw/abc.mp4"], "no orphaned object"
    assert db.deleted == [game], "and no row left to process"
    assert not fake_task.calls, "nothing queued"


class _LostClaimDB(FakeDB):
    """A session where the conditional UPDATE always loses.

    Models the other half of a completion race: the row still read `uploading`
    at the handler's early check, and a concurrent request claimed it before
    this one got to the UPDATE.
    """

    def _claim(self, stmt):
        return _UpdateResult(0)


def _charge_on(game: Game) -> UploadEvent:
    """The charge a sibling completion of this game would already have made."""
    return UploadEvent(id=uuid.uuid4(), owner_id=USER, game_id=game.id, charged_seconds=1800.0)


def test_the_loser_of_a_completion_race_gets_409_and_keeps_the_winners_charge(
    fake_storage, fake_task
):
    """The loser must not hand anything back.

    The reservation is keyed to the game, so it is the *same row* the winner is
    using — releasing it here would uncharge an upload that is about to run.
    """
    game = _uploading_game()
    db = _LostClaimDB(game)
    db.existing_reservation = _charge_on(game)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))

    assert exc.value.status_code == 409, "a lost race is a conflict, not a crash"
    assert [o for o in db.added if isinstance(o, UploadEvent)] == [], "no second charge"
    assert db.deleted == [], "and the winner's row is untouched"
    assert not fake_task.calls


def test_a_sibling_completion_at_the_quota_boundary_cannot_destroy_the_upload(
    fake_storage, fake_task, monkeypatch
):
    """Double-submit with exactly one slot left used to delete a live upload.

    The charge was reserved before the atomic claim, so the sibling saw the
    winner's fresh event, was rejected as over quota, and then ran the
    rejection path — deleting the object and the game row out from under the
    completion that was about to enqueue it.

    Keying the reservation to the game removes the rejection entirely: the
    sibling finds the existing charge instead of competing for a slot.
    """
    monkeypatch.setattr(settings, "quota_max_games_per_window", 1)
    game = _uploading_game(declared_duration=1800.0)
    # Window already full *because of this game's own charge*.
    db = _LostClaimDB(game, quota_usage=(1, 1800.0))
    db.existing_reservation = _charge_on(game)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))

    assert exc.value.status_code == 409, "a conflict, not a quota rejection"
    assert fake_storage["delete_file"] == [], "the winner's object survives"
    assert db.deleted == [], "and so does its row"


class _StolenAfterHeadDB(FakeDB):
    """The row reads `uploading` when the handler loads it, but is gone by the
    time the disposal DELETE runs — a sibling claimed it during the HEAD.

    Needed because the handler's early status check would short-circuit a row
    that already reads `queued`, so simply pre-setting the status tests the
    fast path instead of the guard.
    """

    def _discard(self, stmt):
        return _UpdateResult(0)


def test_a_genuine_quota_rejection_only_discards_an_upload_it_still_owns(
    fake_storage, fake_task, monkeypatch
):
    """The disposal DELETE carries the same `uploading` predicate as the claim.

    Without it, a caller that lost the row during its HEAD still deleted the
    object and the row — destroying the upload the winner was about to enqueue.
    """
    monkeypatch.setattr(settings, "quota_max_games_per_window", 1)
    game = _uploading_game(declared_duration=1800.0)
    # Over quota with no charge on this game, so the rejection itself is real.
    db = _StolenAfterHeadDB(game, quota_usage=(1, 1800.0))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))

    assert exc.value.status_code == 409, "someone else owns it now — not our 429"
    assert fake_storage["delete_file"] == [], "not ours to delete"
    assert not fake_task.calls


def test_a_quota_rejection_it_does_own_discards_object_and_row(
    fake_storage, fake_task, monkeypatch
):
    """The other side of the guard: still ours, so clean it up properly."""
    monkeypatch.setattr(settings, "quota_max_games_per_window", 1)
    game = _uploading_game(declared_duration=1800.0)
    db = FakeDB(game, quota_usage=(1, 1800.0))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.complete_upload(game.id, UploadComplete(), USER, db))

    assert exc.value.status_code == 429
    assert fake_storage["delete_file"] == ["raw/abc.mp4"], "no orphaned object"
    assert db.deleted == [game], "and no row left to process"
    assert not fake_task.calls


# --- CF-217: the column must not bound the multipart upload id ---------------


def test_upload_id_column_is_unbounded():
    """R2 issues multipart upload ids far longer than the varchar(255) this
    column started as, so every multipart upload failed in production with
    StringDataRightTruncationError while small single-PUT uploads kept working.

    Asserted against the column type rather than by inserting, because the bug
    needs no database to reproduce — it is a schema decision — and because the
    thing worth preventing is someone reinstating a bound. A larger fixed width
    would fail this too, deliberately: neither AWS nor Cloudflare documents a
    maximum, so a bound is a guess, and the observed 343 characters is evidence
    about one provider on one day rather than a limit.
    """
    from sqlalchemy import String

    from app.models.game import Game

    column_type = Game.__table__.c.upload_id.type
    length = getattr(column_type, "length", None)

    assert not (isinstance(column_type, String) and length is not None), (
        f"upload_id is bounded at {length} characters; R2 multipart upload ids "
        "are longer than that (~343 observed). Use Text."
    )
