"""The clip-visibility write path (CF-109b item 1, #398).

This is the change that ends "a user can publish, but only to themselves".
Until now no endpoint anywhere set `Clip.visibility` or `Game.visibility`, so
every clip resolved to `private`, `create_post` refused every wider tier, and
the composer could only ever offer "Only me".
`api/tests/test_no_visibility_write_path.py` enforced that absence and is
deleted by the same change — this file is what replaces it, and the two
questions it has to answer are the ones that file was protecting:

1. **Only the clip moves, never the game.** Raising a game publishes every clip
   in it, which is the silent side effect the 409 exists to prevent.
2. **`public` stays behind its own flag**, because it is the tier that puts
   youth-sports footage in front of signed-out strangers and the anonymous read
   endpoints. `services/publishing.py` carries that argument.

Fake sessions and `asyncio.run`, matching `test_posts_endpoints.py`.
"""
import asyncio
import uuid

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.clip import ActionType  # noqa: E402
from app.models.visibility import Visibility  # noqa: E402
from app.routers import clips as clips_router  # noqa: E402
from app.routers import posts as posts_router  # noqa: E402
from app.schemas.clip import ClipVisibilityRequest  # noqa: E402
from app.schemas.post import PostCreate  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class Game:
    def __init__(self, owner_id, visibility=Visibility.private):
        self.id = uuid.uuid4()
        self.owner_id = owner_id
        self.visibility = visibility
        self.raw_video_url = None
        self.title = "Under-14 County Final"


class Clip:
    def __init__(self, game, visibility=None):
        self.id = uuid.uuid4()
        self.game_id = game.id
        self.player_id = None
        self.visibility = visibility
        self.clip_url = "s3://bucket/clip.mp4"
        self.thumbnail_url = None
        self.proxy_url = None
        self.start_time = 10.0
        self.end_time = 18.0
        self.action_type = ActionType.spike
        self.confidence = 0.9
        self.labels = []
        self.highlight_score = 0.8
        self.created_at = None


class User:
    def __init__(self):
        self.id = uuid.uuid4()
        self.username = "alice"
        self.username_is_generated = False
        self.display_name = "Alice"
        self.avatar_url = None


class StubSession:
    def __init__(self, *objects):
        self.rows = {(type(o).__name__, o.id): o for o in objects}
        self.added: list[object] = []
        self.commits = 0

    async def get(self, model, pk):
        return self.rows.get((model.__name__, pk))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        for field in ("like_count", "comment_count"):
            if getattr(obj, field, None) is None:
                setattr(obj, field, 0)
        if getattr(obj, "created_at", None) is None:
            from datetime import datetime, timezone

            obj.created_at = datetime.now(timezone.utc)

    async def execute(self, stmt):
        class _Nothing:
            def first(self):
                return None

        return _Nothing()


@pytest.fixture
def public_posting(monkeypatch):
    monkeypatch.setattr(settings, "public_posting_enabled", True)


@pytest.fixture(autouse=True)
def public_posting_off_by_default(monkeypatch):
    # Explicit rather than relying on the shipped default, so a test that needs
    # the flag on says so and one that needs it off cannot be made green by a
    # future change to that default.
    monkeypatch.setattr(settings, "public_posting_enabled", False)


def _set(db, clip_id, user_id, tier):
    return _run(
        clips_router.update_clip_visibility(
            clip_id, ClipVisibilityRequest(visibility=tier), db, user_id
        )
    )


# ── the standalone setter ────────────────────────────────────────────────────

def test_the_owner_can_widen_a_clip_to_followers():
    owner = User()
    game = Game(owner.id)
    clip = Clip(game)  # NULL: inherits the game's private
    db = StubSession(game, clip)

    out = _set(db, clip.id, owner.id, Visibility.followers)

    assert clip.visibility is Visibility.followers
    assert db.commits == 1
    # The echo carries the new ceiling, so the composer does not need a reload
    # to stop greying out the tier the user just enabled.
    assert out.effective_visibility is Visibility.followers


def test_the_owner_can_narrow_a_clip_again():
    """Widening must not be a one-way door.

    On youth-sports footage a tier you cannot take back is the wrong shape:
    deleting the post does not narrow the clip, so without this the only
    undo would be deleting the footage.
    """
    owner = User()
    game = Game(owner.id)
    clip = Clip(game, visibility=Visibility.followers)
    db = StubSession(game, clip)

    _set(db, clip.id, owner.id, Visibility.private)
    assert clip.visibility is Visibility.private


def test_it_never_touches_the_game():
    """The decision the card made, and the reason a game setter is not here.

    Raising the game publishes every clip in it. A public clip inside a private
    game is the supported state instead: reachable by direct link, while
    GET /games/{id}/clips still 404s the whole game.
    """
    owner = User()
    game = Game(owner.id, Visibility.private)
    clip = Clip(game)
    db = StubSession(game, clip)

    _set(db, clip.id, owner.id, Visibility.followers)

    assert game.visibility is Visibility.private, "the game must not move"


def test_a_stranger_cannot_set_visibility_and_learns_nothing():
    stranger = User()
    owner = User()
    game = Game(owner.id)
    clip = Clip(game)
    db = StubSession(game, clip)

    with pytest.raises(HTTPException) as exc:
        _set(db, clip.id, stranger.id, Visibility.public)

    # 404, not 403: the house rule, so the endpoint is not an existence oracle.
    assert exc.value.status_code == 404
    assert clip.visibility is None
    assert db.commits == 0


def test_public_is_refused_while_the_deployment_has_it_off():
    owner = User()
    game = Game(owner.id)
    clip = Clip(game)
    db = StubSession(game, clip)

    with pytest.raises(HTTPException) as exc:
        _set(db, clip.id, owner.id, Visibility.public)

    assert exc.value.status_code == 422
    assert "followers" in exc.value.detail, "the message names what is available"
    assert clip.visibility is None, "nothing may be written when the flag refuses"
    assert db.commits == 0


def test_public_is_accepted_once_the_deployment_turns_it_on(public_posting):
    # Both tiers are built; the flag decides which the API accepts. If this
    # stops passing, `public` has become unreachable rather than gated.
    owner = User()
    game = Game(owner.id)
    clip = Clip(game)
    db = StubSession(game, clip)

    _set(db, clip.id, owner.id, Visibility.public)
    assert clip.visibility is Visibility.public


def test_ownership_is_checked_before_the_tier():
    """A stranger asking for `public` gets 404, not the flag's 422.

    Written the other way round first, and this is what caught it: the flag's
    message names a deployment setting, so answering it to someone with no
    business here both leaks that setting and breaks the 404-not-403 rule the
    rest of the router keeps — a non-owner should only ever see "not found".
    """
    stranger, owner = User(), User()
    game = Game(owner.id)
    clip = Clip(game)
    db = StubSession(game, clip)

    with pytest.raises(HTTPException) as exc:
        _set(db, clip.id, stranger.id, Visibility.public)
    assert exc.value.status_code == 404


# ── raising the clip while publishing ────────────────────────────────────────

def _post(db, clip_id, user_id, tier, *, raise_clip=False):
    return _run(
        posts_router.create_post(
            PostCreate(
                clip_id=clip_id, visibility=tier, raise_clip_visibility=raise_clip
            ),
            user_id,
            db,
        )
    )


def test_publishing_can_raise_the_clip_with_it():
    owner = User()
    game = Game(owner.id)
    clip = Clip(game)  # private
    db = StubSession(game, clip, owner)

    out = _post(db, clip.id, owner.id, Visibility.followers, raise_clip=True)

    assert clip.visibility is Visibility.followers
    assert out.visibility is Visibility.followers
    # ONE commit: the clip's new tier and the post's INSERT go together, so a
    # post that fails to insert cannot leave the footage widened behind it.
    assert db.commits == 1


def test_publishing_raises_the_clip_no_wider_than_the_post():
    # `followers` on the post means `followers` on the clip. Nothing here
    # reaches `public` unless that is what was asked for.
    owner = User()
    game = Game(owner.id)
    clip = Clip(game)
    db = StubSession(game, clip, owner)

    _post(db, clip.id, owner.id, Visibility.followers, raise_clip=True)
    assert clip.visibility is not Visibility.public


def test_without_the_flag_publishing_still_refuses_and_leaves_the_clip_alone():
    """The 409 is the security-relevant branch and does not go away.

    A caller who did not ask to widen the clip is refused rather than silently
    widening it — the property CF-109 built the 409 for.
    """
    owner = User()
    game = Game(owner.id)
    clip = Clip(game)
    db = StubSession(game, clip, owner)

    with pytest.raises(HTTPException) as exc:
        _post(db, clip.id, owner.id, Visibility.followers, raise_clip=False)

    assert exc.value.status_code == 409
    assert clip.visibility is None, "the clip must not move"
    assert not db.added
    assert db.commits == 0


def test_raising_cannot_reach_public_while_the_deployment_has_it_off():
    """The flag binds both write paths, not just the standalone one.

    A gate on the setter alone would be worth nothing: publishing is the path
    a user actually takes.
    """
    owner = User()
    game = Game(owner.id)
    clip = Clip(game)
    db = StubSession(game, clip, owner)

    with pytest.raises(HTTPException) as exc:
        _post(db, clip.id, owner.id, Visibility.public, raise_clip=True)

    assert exc.value.status_code == 422
    assert clip.visibility is None
    assert db.commits == 0


def test_a_public_post_over_an_already_public_clip_still_needs_the_flag():
    # The flag is about publishing publicly, not about whether the clip has to
    # move for it — otherwise it would be bypassed by widening the clip first.
    owner = User()
    game = Game(owner.id)
    clip = Clip(game, visibility=Visibility.public)
    db = StubSession(game, clip, owner)

    with pytest.raises(HTTPException) as exc:
        _post(db, clip.id, owner.id, Visibility.public, raise_clip=False)
    assert exc.value.status_code == 422


def test_a_stranger_cannot_raise_someone_elses_clip_by_publishing():
    stranger, owner = User(), User()
    game = Game(owner.id)
    clip = Clip(game)
    db = StubSession(game, clip, stranger)

    with pytest.raises(HTTPException) as exc:
        _post(db, clip.id, stranger.id, Visibility.followers, raise_clip=True)

    assert exc.value.status_code == 404
    assert clip.visibility is None
