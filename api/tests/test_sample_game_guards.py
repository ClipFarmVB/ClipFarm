"""CF-220: the example game's guards, and the signup hook's failure posture.

A copy of the example game shares its media with every other copy — its clips
store the same R2 URLs as the source game's — and the footage is ours rather
than the account owner's. So the routes that would delete those objects, or
publish that footage, behave differently for a sample:

* deleting the game or its clips removes rows and never calls
  `storage.delete_file`;
* `PATCH /clips/{id}/visibility`, `POST /posts`, `GET /clips/{id}/share` and
  `GET /clips/{id}/download` answer 409;
* a relabel still applies to the owner's copy but writes no `Correction`;
* a trim refuses (409) with its own message rather than the retention one;
* the configured source game itself cannot be deleted, have clips deleted, or
  be trimmed (409) while `SAMPLE_GAME_ID` names it.

Each refusal is paired with a control on an ordinary game, so a stub that
never reached the guarded code cannot pass for a guard that held.

The signup hook is covered here too, against a stub session, because its
real-Postgres suite (`test_sample_game_pg.py`) skips wherever no server is
running: off means the copier is never called, a DO NOTHING insert does not
copy, and a copier that raises costs the example and not the account.

Fake sessions and `asyncio.run` inside plain defs, the house pattern — see
`test_async_tests_would_be_silently_skipped.py`.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("celery")

from fastapi import HTTPException  # noqa: E402

from app import auth  # noqa: E402
from app.config import settings  # noqa: E402
from app.models.clip import ActionType, Clip  # noqa: E402
from app.models.correction import Correction  # noqa: E402
from app.models.game import Game, GameStatus  # noqa: E402
from app.models.visibility import Visibility  # noqa: E402
from app.routers import clips as clips_router  # noqa: E402
from app.routers import collections as collections_router  # noqa: E402
from app.routers import games as games_router  # noqa: E402
from app.routers import posts as posts_router  # noqa: E402
from app.schemas.clip import (  # noqa: E402
    ClipDeleteRequest,
    ClipLabelsRequest,
    ClipTrimRequest,
    ClipVisibilityRequest,
)
from app.schemas.game import GameOut  # noqa: E402
from app.schemas.post import PostCreate  # noqa: E402
from app.services import sample_game, storage  # noqa: E402

OWNER = uuid.uuid4()
NOW = datetime.now(timezone.utc)


def _game(*, is_sample: bool) -> Game:
    return Game(
        id=uuid.uuid4(),
        owner_id=OWNER,
        title="Demo: Varsity vs Lincoln" if is_sample else "Varsity vs Lincoln",
        status=GameStatus.ready,
        is_sample=is_sample,
        # A sample carries no source upload; an ordinary game does, so the
        # delete control has a raw object to remove as well.
        raw_video_url=None if is_sample else "https://r2.test/raw/abc.mp4",
        condensed_video_url=None,
        condense_requested=False,
        progress=1.0,
        visibility=Visibility.private,
        created_at=NOW,
    )


def _clip(game: Game) -> Clip:
    return Clip(
        id=uuid.uuid4(),
        game_id=game.id,
        player_id=None,
        action_type=ActionType.spike,
        confidence=0.8,
        highlight_score=0.7,
        start_time=10.0,
        end_time=18.0,
        clip_url="https://r2.test/clips/source-game/c1.mp4",
        thumbnail_url="https://r2.test/clips/source-game/c1.jpg",
        labels=["spike"],
        visibility=None,
        created_at=NOW,
    )


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class StubDB:
    """Holds a game and its clips; records every write the handlers make."""

    def __init__(self, game: Game, clips: list[Clip]):
        self.game = game
        self.clips = clips
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.executed: list[object] = []
        self.commits = 0

    async def get(self, model, pk):
        if model is Game and pk == self.game.id:
            return self.game
        if model is Clip:
            return next((c for c in self.clips if c.id == pk), None)
        return None

    async def execute(self, stmt):
        self.executed.append(stmt)
        text = str(stmt)
        if "FROM corrections" in text:
            return _Rows([])
        if "FROM clips JOIN games" in text:
            return _Rows([(c, self.game) for c in self.clips])
        return _Rows(list(self.clips))

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass


@pytest.fixture
def deleted_keys(monkeypatch):
    keys: list[str] = []
    monkeypatch.setattr(storage, "delete_file", keys.append)
    monkeypatch.setattr(storage, "abort_multipart", lambda *a: None)
    return keys


@pytest.fixture(autouse=True)
def public_posting_on(monkeypatch):
    # On, so a 409 below cannot be the posting flag's refusal in disguise.
    monkeypatch.setattr(settings, "public_posting_enabled", True)


def _setup(*, is_sample: bool):
    game = _game(is_sample=is_sample)
    clip = _clip(game)
    return game, clip, StubDB(game, [clip])


# ── delete paths never touch the shared objects ─────────────────────────────


def test_deleting_a_sample_game_removes_rows_and_no_objects(deleted_keys):
    game, _clip_row, db = _setup(is_sample=True)

    asyncio.run(games_router.delete_game(game.id, OWNER, db))

    assert db.deleted == [game], "the dismiss is the row delete"
    assert deleted_keys == [], "every other copy plays these objects"


def test_deleting_an_ordinary_game_still_removes_its_objects(deleted_keys):
    game, _clip_row, db = _setup(is_sample=False)

    asyncio.run(games_router.delete_game(game.id, OWNER, db))

    assert sorted(deleted_keys) == sorted([
        "clips/source-game/c1.mp4", "clips/source-game/c1.jpg", "raw/abc.mp4",
    ])


def test_deleting_sample_clips_removes_rows_and_no_objects(deleted_keys):
    _game_row, clip, db = _setup(is_sample=True)

    out = asyncio.run(
        clips_router.delete_clips(ClipDeleteRequest(clip_ids=[clip.id]), db, OWNER)
    )

    assert out == {"deleted": 1}
    assert db.deleted == [clip]
    assert deleted_keys == []


def test_deleting_ordinary_clips_still_removes_their_objects(deleted_keys):
    _game_row, clip, db = _setup(is_sample=False)

    asyncio.run(
        clips_router.delete_clips(ClipDeleteRequest(clip_ids=[clip.id]), db, OWNER)
    )

    assert sorted(deleted_keys) == ["clips/source-game/c1.jpg", "clips/source-game/c1.mp4"]


# ── the footage is not the owner's to publish ───────────────────────────────


@pytest.mark.parametrize("tier", [Visibility.followers, Visibility.public])
def test_a_sample_clip_cannot_be_widened(tier):
    _game_row, clip, db = _setup(is_sample=True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            clips_router.update_clip_visibility(
                clip.id, ClipVisibilityRequest(visibility=tier), db, OWNER
            )
        )

    assert exc.value.status_code == 409
    assert clip.visibility is None
    assert db.commits == 0


def test_an_ordinary_clip_can_still_be_widened():
    _game_row, clip, db = _setup(is_sample=False)

    asyncio.run(
        clips_router.update_clip_visibility(
            clip.id, ClipVisibilityRequest(visibility=Visibility.followers), db, OWNER
        )
    )

    assert clip.visibility is Visibility.followers


def test_a_sample_clip_cannot_be_posted():
    _game_row, clip, db = _setup(is_sample=True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            posts_router.create_post(
                PostCreate(clip_id=clip.id, visibility=Visibility.private), OWNER, db
            )
        )

    assert exc.value.status_code == 409
    assert db.added == []


def test_a_sample_clip_cannot_be_shared(monkeypatch):
    # The app does not offer a share link for the example. This is a product
    # signal, not access control: the owner's clip listings already carry a
    # presigned URL for the same object (sample_game.assert_not_sample).
    presigned: list[str] = []
    monkeypatch.setattr(
        storage, "presign_from_stored_url", lambda url, **_: presigned.append(url) or url
    )
    _game_row, clip, db = _setup(is_sample=True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(clips_router.share_clip(clip.id, db, OWNER))

    assert exc.value.status_code == 409
    assert presigned == [], "no link is minted for a sample"


def test_an_ordinary_clip_can_still_be_shared(monkeypatch):
    monkeypatch.setattr(storage, "presign_from_stored_url", lambda url, **_: url + "?sig")
    _game_row, clip, db = _setup(is_sample=False)

    out = asyncio.run(clips_router.share_clip(clip.id, db, OWNER))

    assert out == {"url": clip.clip_url + "?sig"}


def test_a_stranger_cannot_share_a_sample_clip_either():
    """The view check comes first: a stranger gets 404, not the 409."""
    _game_row, clip, db = _setup(is_sample=True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(clips_router.share_clip(clip.id, db, uuid.uuid4()))

    assert exc.value.status_code == 404


def test_a_sample_clip_cannot_be_downloaded(monkeypatch):
    # The attachment URL is the same presigned object as a share link, so the
    # app refuses to offer it too. Like /share, not access control.
    presigned: list[str] = []
    monkeypatch.setattr(
        storage, "presign_from_stored_url", lambda url, **_: presigned.append(url) or url
    )
    _game_row, clip, db = _setup(is_sample=True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(clips_router.download_clip(clip.id, db, OWNER))

    assert exc.value.status_code == 409
    assert presigned == [], "no link is minted for a sample"


def test_an_ordinary_clip_can_still_be_downloaded(monkeypatch):
    monkeypatch.setattr(storage, "presign_from_stored_url", lambda url, **_: url + "?sig")
    _game_row, clip, db = _setup(is_sample=False)

    out = asyncio.run(clips_router.download_clip(clip.id, db, OWNER))

    assert out == {"url": clip.clip_url + "?sig"}


def test_a_stranger_cannot_download_a_sample_clip_either():
    """The view check comes first: a stranger gets 404, not the 409."""
    _game_row, clip, db = _setup(is_sample=True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(clips_router.download_clip(clip.id, db, uuid.uuid4()))

    assert exc.value.status_code == 404


def test_a_stranger_still_gets_404_for_a_sample_clip():
    """Ownership first: the 409 must not confirm the clip exists."""
    _game_row, clip, db = _setup(is_sample=True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            posts_router.create_post(PostCreate(clip_id=clip.id), uuid.uuid4(), db)
        )

    assert exc.value.status_code == 404


# ── relabel and trim ────────────────────────────────────────────────────────


def test_relabelling_a_sample_clip_writes_no_correction():
    _game_row, clip, db = _setup(is_sample=True)

    out = asyncio.run(
        clips_router.update_clip_labels(clip.id, ClipLabelsRequest(labels=["dig"]), db, OWNER)
    )

    assert clip.labels == ["dig"], "the owner's copy is still relabelled"
    assert out.is_sample is True
    assert not any(isinstance(o, Correction) for o in db.added)
    assert not any("corrections" in str(s) for s in db.executed)


def test_relabelling_an_ordinary_clip_still_writes_a_correction():
    _game_row, clip, db = _setup(is_sample=False)

    asyncio.run(
        clips_router.update_clip_labels(clip.id, ClipLabelsRequest(labels=["dig"]), db, OWNER)
    )

    assert any(isinstance(o, Correction) for o in db.added)


def test_trimming_a_sample_clip_is_refused_with_its_own_message(monkeypatch):
    celery = MagicMock()
    monkeypatch.setattr(clips_router, "celery_app", celery)
    _game_row, clip, db = _setup(is_sample=True)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            clips_router.trim_clip(
                clip.id, ClipTrimRequest(start_delta=-2, end_delta=0), db, OWNER
            )
        )

    assert exc.value.status_code == 409, "the same status as every other example refusal"
    assert "Demo" in exc.value.detail
    assert "retention" not in exc.value.detail
    assert (clip.start_time, clip.end_time) == (10.0, 18.0)
    celery.send_task.assert_not_called()


# ── the configured source game itself ───────────────────────────────────────
#
# Not a copy: the ordinary game on the internal account that SAMPLE_GAME_ID
# names. Its objects are the ones every copy plays, so the paths that delete
# or overwrite them answer 409 while the setting names it.


@pytest.fixture
def source_configured(monkeypatch):
    def _point_at(game: Game) -> None:
        monkeypatch.setattr(settings, "sample_game_id", str(game.id))
    return _point_at


def test_deleting_the_source_game_is_refused(deleted_keys, source_configured):
    game, _clip_row, db = _setup(is_sample=False)
    source_configured(game)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games_router.delete_game(game.id, OWNER, db))

    assert exc.value.status_code == 409
    assert db.deleted == []
    assert db.commits == 0
    assert deleted_keys == []


class _MixedBatchDB(StubDB):
    """A clip delete whose batch spans two games."""

    def __init__(self, rows: list[tuple[Clip, Game]]):
        super().__init__(rows[0][1], [c for c, _ in rows])
        self.rows = rows

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _Rows(self.rows)


def test_a_clip_batch_touching_the_source_is_refused_whole(deleted_keys, source_configured):
    source, source_clip, _ = _setup(is_sample=False)
    other, other_clip, _ = _setup(is_sample=False)
    source_configured(source)
    # The ordinary clip first, so a guard checked per clip inside the delete
    # loop would already have removed it.
    db = _MixedBatchDB([(other_clip, other), (source_clip, source)])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            clips_router.delete_clips(
                ClipDeleteRequest(clip_ids=[other_clip.id, source_clip.id]), db, OWNER
            )
        )

    assert exc.value.status_code == 409
    assert db.deleted == [], "nothing in the batch is deleted"
    assert db.commits == 0
    assert deleted_keys == []


def test_trimming_a_source_clip_is_refused(monkeypatch, source_configured):
    celery = MagicMock()
    monkeypatch.setattr(clips_router, "celery_app", celery)
    game, clip, db = _setup(is_sample=False)
    source_configured(game)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            clips_router.trim_clip(
                clip.id, ClipTrimRequest(start_delta=-2, end_delta=0), db, OWNER
            )
        )

    assert exc.value.status_code == 409
    assert (clip.start_time, clip.end_time) == (10.0, 18.0)
    assert db.commits == 0
    celery.send_task.assert_not_called()


def test_the_source_guard_is_off_when_the_setting_is_empty(monkeypatch, deleted_keys):
    """The guard follows the current setting: unset, the same game deletes."""
    monkeypatch.setattr(settings, "sample_game_id", "")
    game, _clip_row, db = _setup(is_sample=False)

    asyncio.run(games_router.delete_game(game.id, OWNER, db))

    assert db.deleted == [game]
    assert "raw/abc.mp4" in deleted_keys


# ── what the client is told ─────────────────────────────────────────────────


def test_the_flag_reaches_both_response_shapes():
    game, clip, _db = _setup(is_sample=True)

    assert GameOut.model_validate(game).is_sample is True
    assert clips_router._clip_out(clip, game).is_sample is True

    plain, plain_clip, _ = _setup(is_sample=False)
    assert GameOut.model_validate(plain).is_sample is False
    assert clips_router._clip_out(plain_clip, plain).is_sample is False


class _ListRows(_Rows):
    def __iter__(self):
        return iter(self._rows)


class _CollectionStubDB:
    """`list_collection_clips` reads the collection with `get()`, then runs the
    clip page and the game lookup in that order. The clip here has no player,
    so the player lookup between them is skipped."""

    def __init__(self, collection, clips: list[Clip], games: list[Game]):
        self._collection = collection
        self._queued = [clips, games]

    async def get(self, _model, _pk):
        return self._collection

    async def execute(self, _stmt):
        return _ListRows(self._queued.pop(0))


@pytest.mark.parametrize("is_sample", [True, False])
def test_the_flag_reaches_a_clip_opened_from_a_collection(monkeypatch, is_sample):
    # A collection is one of the pages that hold only the clip, and it builds
    # its ClipOut by hand rather than through `_clip_out` — so the flag has to
    # be set there too, or Share and Post are offered on a sample and 409.
    monkeypatch.setattr(storage, "r2_configured", lambda: False)
    game, clip, _db = _setup(is_sample=is_sample)
    collection = MagicMock(id=uuid.uuid4(), owner_id=OWNER)
    db = _CollectionStubDB(collection, [clip], [game])

    out = asyncio.run(
        collections_router.list_collection_clips(collection.id, OWNER, db)
    )

    assert [c.id for c in out] == [clip.id]
    assert out[0].is_sample is is_sample


def test_an_unflushed_game_reads_as_not_a_sample():
    """The column default applies at flush; until then the attribute is None."""
    game = Game(id=uuid.uuid4(), owner_id=OWNER, title="t", status=GameStatus.ready,
                progress=0.0, condense_requested=False, created_at=NOW)
    assert game.is_sample is None
    assert GameOut.model_validate(game).is_sample is False
    assert sample_game.is_sample(game) is False


def test_only_a_real_true_counts_as_a_sample():
    assert sample_game.is_sample(object()) is False
    assert sample_game.is_sample(MagicMock()) is False, "a Mock attribute is truthy"


# ── the setting ─────────────────────────────────────────────────────────────


def test_an_unset_setting_is_off(monkeypatch):
    monkeypatch.setattr(settings, "sample_game_id", "")
    assert sample_game.configured_source_id() is None


def test_a_malformed_setting_is_logged_and_off(monkeypatch, caplog):
    monkeypatch.setattr(settings, "sample_game_id", "not-a-uuid")
    with caplog.at_level(logging.WARNING):
        assert sample_game.configured_source_id() is None
    assert "not a UUID" in caplog.text


def test_a_well_formed_setting_parses(monkeypatch):
    gid = uuid.uuid4()
    monkeypatch.setattr(settings, "sample_game_id", f"  {gid}  ")
    assert sample_game.configured_source_id() == gid


# ── the copier, without a server ────────────────────────────────────────────
# test_sample_game_pg.py checks the rows Postgres ends up holding; this checks
# the field choices wherever the suite runs, since that file skips without one.


class _CopyDB(StubDB):
    async def flush(self):
        for obj in self.added:
            if isinstance(obj, Game) and obj.id is None:
                obj.id = uuid.uuid4()


def test_the_copy_takes_the_fields_the_plan_names(monkeypatch):
    source = _game(is_sample=False)
    source.condensed_video_url = "https://r2.test/condensed/src.mp4"
    source.original_duration, source.condensed_duration = 1800.0, 900.0
    source.processed_at = NOW
    clip = _clip(source)
    clip.player_id = uuid.uuid4()
    db = _CopyDB(source, [clip])
    monkeypatch.setattr(settings, "sample_game_id", str(source.id))
    new_owner = uuid.uuid4()

    copy = asyncio.run(sample_game.copy_sample_game(db, new_owner))

    assert copy is not None and copy in db.added
    assert copy.owner_id == new_owner
    assert copy.is_sample is True
    assert copy.status is GameStatus.ready
    assert copy.title == "Demo: Varsity vs Lincoln", "unset SAMPLE_GAME_TITLE: Demo + source"
    assert copy.raw_video_url is None
    assert copy.condensed_video_url is None
    assert copy.condensed_duration is None, "cleared with the cut it describes"
    assert copy.upload_id is None
    assert copy.visibility is None, "not passed, so the column default (private) applies"
    assert (copy.original_duration, copy.processed_at) == (1800.0, NOW)

    [copied] = [o for o in db.added if isinstance(o, Clip)]
    assert copied.game_id == copy.id
    assert copied.player_id is None
    assert copied.visibility is None
    assert (copied.clip_url, copied.thumbnail_url) == (clip.clip_url, clip.thumbnail_url)
    assert (copied.start_time, copied.end_time, copied.labels) == (10.0, 18.0, ["spike"])
    assert (copied.action_type, copied.confidence, copied.highlight_score) == (
        ActionType.spike, 0.8, 0.7
    ), "the example keeps its action types and highlight ranking"


def test_a_source_that_is_not_ready_is_not_copied(monkeypatch):
    source = _game(is_sample=False)
    source.status = GameStatus.processing
    db = _CopyDB(source, [_clip(source)])
    monkeypatch.setattr(settings, "sample_game_id", str(source.id))

    assert asyncio.run(sample_game.copy_sample_game(db, uuid.uuid4())) is None
    assert db.added == []


def test_a_source_with_no_clips_is_not_copied(monkeypatch, caplog):
    source = _game(is_sample=False)
    db = _CopyDB(source, [])
    monkeypatch.setattr(settings, "sample_game_id", str(source.id))

    with caplog.at_level(logging.WARNING):
        assert asyncio.run(sample_game.copy_sample_game(db, uuid.uuid4())) is None
    assert db.added == [], "no empty example game"
    assert "has no clips" in caplog.text


def test_a_missing_source_is_not_copied(monkeypatch):
    source = _game(is_sample=False)
    db = _CopyDB(source, [])
    monkeypatch.setattr(settings, "sample_game_id", str(uuid.uuid4()))

    assert asyncio.run(sample_game.copy_sample_game(db, uuid.uuid4())) is None
    assert db.added == []


# ── the signup hook ─────────────────────────────────────────────────────────


class _Nested:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        self.session.savepoints += 1

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.session.savepoint_rollbacks += 1
        return False


class _SignupSession:
    """First read finds no user; the INSERT returns `inserted` (None = DO NOTHING)."""

    def __init__(self, inserted):
        self._inserted = inserted
        self.commits = 0
        self.savepoints = 0
        self.savepoint_rollbacks = 0

    async def scalar(self, _stmt):
        return None

    async def execute(self, _stmt):
        return _Rows([self._inserted] if self._inserted is not None else [])

    def begin_nested(self):
        return _Nested(self)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        raise AssertionError("signup must not roll back")


@pytest.fixture
def copier(monkeypatch):
    calls: list[uuid.UUID] = []

    async def record(db, user_id):
        calls.append(user_id)

    monkeypatch.setattr(sample_game, "copy_sample_game", record)
    return calls


def test_off_never_calls_the_copier(monkeypatch, copier):
    monkeypatch.setattr(settings, "sample_game_id", "")
    uid = uuid.uuid4()
    db = _SignupSession(inserted=uid)

    asyncio.run(auth._ensure_user_exists(uid, "new@example.com", db))

    assert copier == []
    assert db.savepoints == 0, "off must be byte-for-byte the old path"
    assert db.commits == 1


def test_the_request_that_created_the_user_copies(monkeypatch, copier):
    monkeypatch.setattr(settings, "sample_game_id", str(uuid.uuid4()))
    uid = uuid.uuid4()
    db = _SignupSession(inserted=uid)

    asyncio.run(auth._ensure_user_exists(uid, "new@example.com", db))

    assert copier == [uid]
    assert db.savepoints == 1
    assert db.commits == 1


def test_a_do_nothing_insert_does_not_copy(monkeypatch, copier):
    """Another request created the row between our read and our insert."""
    monkeypatch.setattr(settings, "sample_game_id", str(uuid.uuid4()))
    uid = uuid.uuid4()
    db = _SignupSession(inserted=None)

    asyncio.run(auth._ensure_user_exists(uid, "new@example.com", db))

    assert copier == []
    assert db.commits == 1


def test_a_failing_copy_costs_the_example_not_the_account(monkeypatch, caplog):
    monkeypatch.setattr(settings, "sample_game_id", str(uuid.uuid4()))

    async def boom(db, user_id):
        raise RuntimeError("R2 is on fire")

    monkeypatch.setattr(sample_game, "copy_sample_game", boom)
    uid = uuid.uuid4()
    db = _SignupSession(inserted=uid)

    with caplog.at_level(logging.ERROR):
        asyncio.run(auth._ensure_user_exists(uid, "someone.real@example.com", db))

    assert db.savepoint_rollbacks == 1
    assert db.commits == 1, "the user row still commits"
    assert "could not copy the sample game" in caplog.text
    assert "someone.real@example.com" not in caplog.text


# ── The demo's name and credit (CF-220, maintainer request) ─────────────────


def test_the_copy_is_named_by_sample_game_title_when_set(monkeypatch):
    monkeypatch.setattr(settings, "sample_game_title", "  Demo game · Javelin Ottawa  ")
    assert sample_game.copy_title("[07-04 isolated test] YT vod ~31m") == (
        "Demo game · Javelin Ottawa"
    )


def test_the_copy_falls_back_to_demo_plus_the_source_title(monkeypatch):
    monkeypatch.setattr(settings, "sample_game_title", "   ")
    assert sample_game.copy_title("Varsity vs Lincoln") == "Demo: Varsity vs Lincoln"


def test_the_copy_title_fits_the_column(monkeypatch):
    monkeypatch.setattr(settings, "sample_game_title", "")
    assert len(sample_game.copy_title("x" * 400)) == 255


def _game_out(is_sample: bool):
    return GameOut(id=uuid.uuid4(), title="t", status=GameStatus.ready,
                   created_at=NOW, is_sample=is_sample)


def test_a_demo_copy_carries_the_configured_credit(monkeypatch):
    credit = "Footage: Javelin Ottawa — https://www.youtube.com/watch?v=OnkNS1ZJ7gI"
    monkeypatch.setattr(settings, "sample_game_credit", f"  {credit} ")

    body = _game_out(is_sample=True).model_dump()

    assert body["sample_credit"] == credit


def test_an_ordinary_game_never_carries_the_credit(monkeypatch):
    monkeypatch.setattr(settings, "sample_game_credit", "Footage: Javelin Ottawa")

    assert _game_out(is_sample=False).model_dump()["sample_credit"] is None


def test_no_credit_is_sent_when_none_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "sample_game_credit", "")

    assert _game_out(is_sample=True).model_dump()["sample_credit"] is None
