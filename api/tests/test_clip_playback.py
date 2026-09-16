"""CF-319: the endpoint that re-mints a playback URL for a clip already held.

Driven by calling the router coroutine with a fake session — the house pattern
here (test_clip_download.py, test_posts_endpoints.py, test_player_routes.py) —
because what matters is which authorization path the route took, what TTL it
asked the presigner for, and whether the expiry it reports is the one it minted.
None of that needs a database or R2.

**Two traps this file exists to stay out of.**

`_rewrite_urls` presigns only when `storage.r2_configured()` is true, and in the
test environment it is false (no credentials in settings). Monkeypatching the
presigner alone would therefore capture nothing, and every "minted with the
right TTL" assertion would pass without a presign ever happening. So
`r2_configured` is patched too — and one test deliberately leaves it false to
pin the other branch.

And the suite has no async pytest plugin, so an `async def test_` is collected,
skipped, and reported green. Every test here is a plain `def` around
`asyncio.run`.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from app.models.clip import ActionType  # noqa: E402
from app.models.visibility import Visibility  # noqa: E402
from app.routers import clips as clips_router  # noqa: E402
from app.routers.clips import PLAYBACK_URL_TTL_SECONDS, refresh_clip_playback  # noqa: E402
from fastapi import HTTPException  # noqa: E402

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()


class _Game:
    def __init__(self, visibility=Visibility.private, title="Panthers vs Sharks"):
        self.id = uuid.uuid4()
        self.owner_id = OWNER
        self.visibility = visibility
        self.title = title
        self.raw_video_url = None


class _Clip:
    def __init__(self, game, visibility=None, thumbnail_url="https://r2.example/thumbs/abc.jpg"):
        self.id = uuid.uuid4()
        self.game_id = game.id
        self.player_id = None
        self.action_type = ActionType.spike
        self.confidence = 0.9
        self.labels = []
        self.start_time = 252.0
        self.end_time = 260.0
        self.visibility = visibility
        self.clip_url = "https://r2.example/clips/abc.mp4"
        self.thumbnail_url = thumbnail_url


class _FakeSession:
    """`db.get(Model, pk)` over a dict, which is all the route uses."""

    def __init__(self, *objects):
        self._by_id = {o.id: o for o in objects}

    async def get(self, _model, pk):
        return self._by_id.get(pk)


def _playback(session, clip_id, viewer_id):
    return asyncio.run(refresh_clip_playback(clip_id, session, viewer_id))


@pytest.fixture
def presigned(monkeypatch):
    """Capture every presign the route asks for, with R2 reported configured.

    Returns the list of calls rather than the last one: the route mints the clip
    and its thumbnail separately, and a regression that presigned only one of
    them would be invisible in a dict that each call overwrites.
    """
    calls: list[dict] = []

    def fake(stored_url, expires_in=3600, download_filename=None):
        calls.append(
            {
                "stored_url": stored_url,
                "expires_in": expires_in,
                "download_filename": download_filename,
            }
        )
        return f"https://r2.example/signed?k={len(calls)}"

    monkeypatch.setattr(clips_router.storage, "r2_configured", lambda: True)
    monkeypatch.setattr(clips_router.storage, "presign_from_stored_url", fake)
    return calls


class TestPlaybackEndpoint:
    def test_owner_gets_a_fresh_url(self, presigned):
        game = _Game()
        clip = _Clip(game)
        out = _playback(_FakeSession(game, clip), clip.id, OWNER)

        assert out.clip_id == clip.id
        assert out.clip_url == "https://r2.example/signed?k=1"
        assert out.thumbnail_url == "https://r2.example/signed?k=2"
        assert [c["stored_url"] for c in presigned] == [
            "https://r2.example/clips/abc.mp4",
            "https://r2.example/thumbs/abc.jpg",
        ]

    def test_it_mints_with_the_playback_ttl(self, presigned, monkeypatch):
        """The reported expiry must be the one the URL was actually signed for.

        Patched away from the real 3600 on purpose. Both the presign and the
        response read the module constant at call time, so asserting against the
        default would pass even if the two sides had been wired to different
        numbers — the exact drift the constant exists to prevent.
        """
        monkeypatch.setattr(clips_router, "PLAYBACK_URL_TTL_SECONDS", 900)
        game = _Game()
        clip = _Clip(game)
        out = _playback(_FakeSession(game, clip), clip.id, OWNER)

        assert {c["expires_in"] for c in presigned} == {900}
        assert out.expires_in == 900

    def test_the_default_ttl_is_an_hour(self, presigned):
        game = _Game()
        clip = _Clip(game)
        out = _playback(_FakeSession(game, clip), clip.id, OWNER)

        assert PLAYBACK_URL_TTL_SECONDS == 3600
        assert out.expires_in == 3600
        assert {c["expires_in"] for c in presigned} == {3600}

    def test_expires_at_is_timezone_aware_and_roughly_now_plus_the_ttl(self, presigned):
        """A naive datetime here would be read as local time by a client.

        Every datetime in api/ is `datetime.now(timezone.utc)`; a client
        computing "refresh in N seconds" off a naive value would be wrong by the
        server's UTC offset — silently right in London and hours out elsewhere.
        """
        before = datetime.now(timezone.utc)
        game = _Game()
        clip = _Clip(game)
        out = _playback(_FakeSession(game, clip), clip.id, OWNER)
        after = datetime.now(timezone.utc)

        assert out.expires_at is not None
        assert out.expires_at.tzinfo is not None
        assert out.expires_at.utcoffset() == timedelta(0)
        assert (
            before + timedelta(seconds=PLAYBACK_URL_TTL_SECONDS)
            <= out.expires_at
            <= after + timedelta(seconds=PLAYBACK_URL_TTL_SECONDS)
        )

    def test_a_clip_without_a_thumbnail_is_not_an_error(self, presigned):
        game = _Game()
        clip = _Clip(game, thumbnail_url=None)
        out = _playback(_FakeSession(game, clip), clip.id, OWNER)

        assert out.thumbnail_url is None
        assert out.clip_url == "https://r2.example/signed?k=1"
        assert len(presigned) == 1, "must not presign a thumbnail that does not exist"

    def test_unconfigured_r2_returns_the_stored_url_and_no_expiry(self, monkeypatch):
        """The dev path: no credentials, so the stored public URL is served.

        That URL carries no signature and so has no expiry, and saying otherwise
        would have a client refresh a URL that never dies — and teach it that a
        non-null expiry means "signed", which is the one thing this field is
        for. Deliberately does NOT use the `presigned` fixture: the point is the
        branch where `r2_configured()` is false.
        """
        monkeypatch.setattr(clips_router.storage, "r2_configured", lambda: False)
        game = _Game()
        clip = _Clip(game)
        out = _playback(_FakeSession(game, clip), clip.id, OWNER)

        assert out.clip_url == "https://r2.example/clips/abc.mp4"
        assert out.thumbnail_url == "https://r2.example/thumbs/abc.jpg"
        assert out.expires_in is None
        assert out.expires_at is None

    def test_a_stranger_is_refused_on_a_private_clip(self, presigned):
        # Authorization parity with /share: same helper, same 404-not-403, so
        # the endpoint cannot confirm a clip exists to anyone probing.
        game = _Game(visibility=Visibility.private)
        clip = _Clip(game)
        with pytest.raises(HTTPException) as exc:
            _playback(_FakeSession(game, clip), clip.id, STRANGER)

        assert exc.value.status_code == 404
        assert presigned == [], "must not mint a URL for a refused viewer"

    def test_a_missing_clip_is_a_404_not_a_crash(self, presigned):
        game = _Game()
        with pytest.raises(HTTPException) as exc:
            _playback(_FakeSession(game), uuid.uuid4(), OWNER)

        assert exc.value.status_code == 404
        assert presigned == []

    def test_a_stranger_may_refresh_a_public_clip(self, presigned):
        # The documented asymmetry (services/access.py): an override publishes
        # *that clip*, so a public clip inside a private game is reachable by
        # direct link — and a link that cannot be refreshed is the dead player
        # this card is about.
        game = _Game(visibility=Visibility.private)
        clip = _Clip(game, visibility=Visibility.public)
        out = _playback(_FakeSession(game, clip), clip.id, STRANGER)

        assert out.clip_url == "https://r2.example/signed?k=1"

    def test_it_reuses_the_shared_read_gate(self):
        """Structural, because the failure mode is a *second* implementation.

        The card's second acceptance criterion is that authorization goes
        through `_get_viewable_clip` rather than growing a parallel check here.
        A behavioural test cannot tell a correct copy from a reuse — it can only
        catch a copy once it has already drifted, which is after the leak.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(clips_router))
        fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "refresh_clip_playback"
        )
        called = {
            node.func.id
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "_get_viewable_clip" in called
        assert "_get_owned_clip" not in called, (
            "a refresh is a read — gating it on ownership would make a public "
            "clip unrefreshable by the anonymous viewer /share hands it to"
        )
