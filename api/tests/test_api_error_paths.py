"""CF-303: five api error paths that reported the wrong thing, or blocked.

Nine of the eleven fail against `main` and pass with their own fix — measured,
by running this file against the base commit: 9 failed, 2 passed. The other two
are controls that by design cannot fail on a revert, and are named as such where
they sit. They are kept in one file because the card is one cleanup pass rather
than five unrelated changes — the shared theme is what the caller is told when something
goes wrong, and what it costs everyone else while it happens.

Follows the house pattern from test_profile_routes.py / test_clip_tag_access.py
— a stand-in session and direct calls into the router, no TestClient and no
database. None of these five needs Postgres, so none of them is in the eight
that skip without one.
"""
import asyncio
import threading
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.models.game import GameStatus  # noqa: E402
from app.models.visibility import Visibility  # noqa: E402

OWNER = uuid.uuid4()


class _Game:
    def __init__(self, owner_id=OWNER, condensed=None, title="Game"):
        self.id = uuid.uuid4()
        self.owner_id = owner_id
        self.title = title
        self.raw_video_url = "raw/x.mp4"
        self.condensed_video_url = condensed
        self.condense_requested = condensed is not None
        self.visibility = Visibility.private
        self.status = GameStatus.ready
        self.created_at = datetime.now(timezone.utc)
        self.duration_sec = 60.0
        self.size_bytes = 1024
        self.upload_id = None
        self.clip_count = 0


# ── 1. an unknown action_type is the caller's mistake, not a server error ────

def test_an_unknown_action_type_is_a_client_error():
    """`action_type` is a plain `str` so the comma-separated form works, which
    moves enum validation into the body. It was an unhandled ValueError, so a
    client typo — or a stale value from an older client — rendered as a 500 and
    paged whoever watches 5xx."""
    from app.routers import clips

    game = _Game()

    class _DB:
        async def get(self, _model, _pk):
            return game

    with pytest.raises(HTTPException) as exc:
        asyncio.run(clips.list_clips(game.id, _DB(), viewer_id=OWNER, action_type="spke"))

    assert exc.value.status_code == 422
    assert "spke" in str(exc.value.detail)
    # The message has to say what IS allowed, or the caller cannot act on it.
    assert "spike" in str(exc.value.detail)


def test_a_valid_action_type_still_filters():
    """The negative case alone would pass against a route that rejects
    everything, so the accepting case is pinned beside it."""
    from app.routers import clips

    game = _Game()
    seen = {}

    class _Result:
        def scalar_one(self):
            return 0

        def scalars(self):
            return self

        def all(self):
            return []

    class _DB:
        async def get(self, _model, _pk):
            return game

        async def execute(self, q):
            seen["q"] = q
            return _Result()

    asyncio.run(clips.list_clips(game.id, _DB(), viewer_id=OWNER, action_type="spike"))
    assert "q" in seen  # reached the query rather than raising


# ── 5. /health must report a dependency down, not fall over ─────────────────

def test_a_malformed_redis_url_reports_down_rather_than_raising(monkeypatch):
    """`from_url` validates the scheme eagerly, so a bad REDIS_URL raised at
    construction — which sat outside the guard, in the one route whose whole
    job is to report a dependency as unreachable."""
    from app import main
    from app.config import settings

    monkeypatch.setattr(settings, "redis_url", "http://not-a-redis-url")

    assert asyncio.run(main._check_redis()) is False


def test_the_health_route_degrades_on_a_malformed_redis_url(monkeypatch):
    """The route, not just the checker: `asyncio.gather` has no
    `return_exceptions`, so a raising redis check discarded the database
    signal too and the whole endpoint 500'd."""
    from app import main
    from app.config import settings

    monkeypatch.setattr(settings, "redis_url", "http://not-a-redis-url")

    async def _db_ok():
        return True

    monkeypatch.setattr(main, "_check_db", _db_ok)

    class _Response:
        status_code = 200

    response = _Response()
    body = asyncio.run(main.health(response))

    assert response.status_code == 503
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] == "down"
    # The database answer survives the redis failure.
    assert body["checks"]["database"] == "ok"


# ── 2. best-effort R2 cleanup must not block the event loop ─────────────────

def _thread_recording_delete(recorder):
    def _delete(key):
        recorder.append(threading.current_thread())
    return _delete


def test_deleting_a_game_does_not_delete_from_the_event_loop(monkeypatch):
    """A game with N clips is 2N+2 blocking boto3 calls. On the single-worker
    api each one stalls every other request, and the route a few lines above
    already goes through the threadpool — this was drift, not a decision."""
    from app.routers import games
    from app.services import storage

    threads: list = []
    monkeypatch.setattr(storage, "delete_file", _thread_recording_delete(threads))

    game = _Game()

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class _DB:
        async def get(self, _model, _pk):
            return game

        async def execute(self, _q):
            return _Result()

        async def delete(self, _obj):
            return None

        async def commit(self):
            return None

    game.raw_video_url = "https://example.invalid/raw/x.mp4"
    asyncio.run(games.delete_game(game.id, OWNER, _DB()))

    assert threads, "the route deleted nothing, so this proves nothing"
    main_thread = threading.main_thread()
    assert all(t is not main_thread for t in threads), (
        "storage.delete_file ran on the event loop thread"
    )


def test_bulk_deleting_clips_does_not_delete_from_the_event_loop(monkeypatch):
    """The card names two call sites and this is the second. `clips.py` did not
    even import `run_in_threadpool`, so covering only the games route would
    have left the missing import unexercised."""
    from app.routers import clips
    from app.services import storage

    threads: list = []
    monkeypatch.setattr(storage, "delete_file", _thread_recording_delete(threads))

    game = _Game()
    clip = type("_Clip", (), {
        "id": uuid.uuid4(), "game_id": game.id,
        "clip_url": "https://example.invalid/clips/a.mp4",
        "thumbnail_url": None,
    })()

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

    assert threads, "the route deleted nothing, so this proves nothing"
    main_thread = threading.main_thread()
    assert all(t is not main_thread for t in threads), (
        "storage.delete_file ran on the event loop thread"
    )


# ── 3. a stored URL against a private bucket is not a usable URL ────────────

def test_renaming_a_game_presigns_the_condensed_url(monkeypatch):
    """`get_game` presigned it and `rename_game` did not, so the same field
    came back loadable from one route and dead from the other."""
    from app.routers import games

    game = _Game(condensed="https://pub.invalid/condensed/x.mp4")
    monkeypatch.setattr(
        games.storage, "presign_from_stored_url",
        lambda url, download_filename=None: "https://signed.invalid/x.mp4?sig=1",
    )

    class _DB:
        async def get(self, _model, _pk):
            return game

        async def commit(self):
            return None

        async def refresh(self, _obj):
            return None

    class _Body:
        title = "Renamed"

    out = asyncio.run(games.rename_game(game.id, _Body(), OWNER, _DB()))
    assert out.condensed_video_url == "https://signed.invalid/x.mp4?sig=1"


def test_the_game_list_presigns_the_condensed_url(monkeypatch):
    """The list returned the stored form, which is a dead URL against a private
    bucket and indistinguishable from a live one. It presigns like every other
    route that returns this field."""
    from app.routers import games

    game = _Game(condensed="https://pub.invalid/condensed/x.mp4")
    monkeypatch.setattr(
        games.storage, "presign_from_stored_url",
        lambda url, download_filename=None: "https://signed.invalid/x.mp4?sig=1",
    )

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [game]

        def __iter__(self):
            return iter([])

    class _DB:
        async def execute(self, _q):
            return _Result()

    out = asyncio.run(games.list_games(OWNER, _DB()))
    assert len(out) == 1
    assert out[0].condensed_video_url == "https://signed.invalid/x.mp4?sig=1"


def test_the_game_list_signs_nothing_when_there_is_no_condensed_cut(monkeypatch):
    """The cost claim in the PR body rests on this: the helper is guarded, and
    `condense` is opt-in, so a library of un-condensed games signs nothing."""
    from app.routers import games

    calls = []
    monkeypatch.setattr(
        games.storage, "presign_from_stored_url",
        lambda url, download_filename=None: calls.append(url) or "x",
    )

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [_Game(), _Game(), _Game()]

        def __iter__(self):
            return iter([])

    class _DB:
        async def execute(self, _q):
            return _Result()

    out = asyncio.run(games.list_games(OWNER, _DB()))
    assert len(out) == 3
    assert calls == [], "signed a URL for a game with no condensed cut"


# ── 4. a double-click must not 500 ─────────────────────────────────────────

class _ExpiringCollection:
    """A stand-in for an ORM object that a failed commit has expired.

    `AsyncSession.rollback()` clears every loaded attribute — `expire_on_commit`
    governs commit only — so touching one afterwards emits a lazy SELECT from a
    synchronous attribute access, which inside async code is `MissingGreenlet`.
    Reproduced against SQLAlchemy 2.0.36: after a failed commit and a rollback,
    the instance `__dict__` is empty.

    A plain fake cannot show that, because it has no ORM state to expire and
    would keep answering happily — a green test for the exact 500 the handler
    exists to remove. This one raises instead, so the route may only build its
    response from values it was passed.
    """

    def __init__(self, owner_id):
        self._id = uuid.uuid4()
        self.owner_id = owner_id
        self.expired = False

    @property
    def id(self):
        if self.expired:
            raise AssertionError(
                "read an expired ORM attribute after rollback — in a real "
                "AsyncSession this is MissingGreenlet, i.e. a 500"
            )
        return self._id


def _collection_db(col, clip, game, *, settles=True):
    class _Row:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class _DB:
        def __init__(self):
            self.commits = 0
            self.rolled_back = False

        async def get(self, model, _pk):
            return {"Clip": clip, "Game": game}.get(model.__name__)

        async def execute(self, _q):
            # Before the race: nothing there. After it: whatever the
            # loser's re-check should find.
            if self.rolled_back:
                return _Row(object() if settles else None)
            return _Row(None)

        def add(self, _obj):
            return None

        async def commit(self):
            self.commits += 1
            if self.commits > 1:
                raise IntegrityError("INSERT", {}, Exception("duplicate key"))

        async def rollback(self):
            self.rolled_back = True
            col.expired = True

    return _DB()


def _add(collections, col, db, clip):
    body = type("_Body", (), {"clip_id": clip.id})()
    return asyncio.run(collections.add_clip_to_collection(col._id, body, OWNER, db))


def test_two_concurrent_adds_to_a_collection_both_succeed(monkeypatch):
    """Check-then-insert under a comment promising an upsert. Both callers read
    `None`, and the loser's commit hits the primary key — reported as a 500 for
    an outcome the endpoint says it guarantees."""
    from app.routers import collections

    game = _Game()
    clip = type("_Clip", (), {"id": uuid.uuid4(), "game_id": game.id,
                              "visibility": None})()
    col = _ExpiringCollection(OWNER)
    db = _collection_db(col, clip, game, settles=True)

    async def _owned(_cid, _uid, _db):
        return col

    monkeypatch.setattr(collections, "_get_owned_collection", _owned)

    first = _add(collections, col, db, clip)
    second = _add(collections, col, db, clip)

    assert db.commits == 2, "the second call never reached the insert"
    assert db.rolled_back, "the IntegrityError was never caught"
    assert first == second
    assert first["clip_id"] == str(clip.id)


def test_a_vanished_clip_is_not_reported_as_added(monkeypatch):
    """The catch is broad, so a clip or collection deleted in the same instant
    lands in it too. Answering 201 for a row that does not exist would be a
    lie, so the loser re-reads the table and 404s when nothing settled."""
    from app.routers import collections

    game = _Game()
    clip = type("_Clip", (), {"id": uuid.uuid4(), "game_id": game.id,
                              "visibility": None})()
    col = _ExpiringCollection(OWNER)
    db = _collection_db(col, clip, game, settles=False)

    async def _owned(_cid, _uid, _db):
        return col

    monkeypatch.setattr(collections, "_get_owned_collection", _owned)

    _add(collections, col, db, clip)          # first add wins
    with pytest.raises(HTTPException) as exc:
        _add(collections, col, db, clip)      # loses, and nothing is there

    assert exc.value.status_code == 404
