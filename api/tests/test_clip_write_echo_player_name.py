"""CF-566: the label, trim and visibility echoes must carry the clip's player_name.

All three handlers returned `_clip_out(clip, game)` with no name, so a tagged
clip came back `player_name: null`. The web client replaces its copy of the
clip with the response, so relabelling, trimming or changing the visibility of
a tagged clip erased its tag on screen until the next reload.

The lookup rule is the one `list_clips` already applies (CF-263, pinned by
test_public_player_name.py): a bare id lookup, no ownership filter. That is why
the legacy case below exists — a clip tagged before CF-234 with another
tenant's player shows that name in the listing, so a write must echo it too
rather than blank it.

Each case runs against all three endpoints. The untagged case is pinned beside
the tagged ones so a fix that invents a name cannot pass.

House pattern from test_clip_tag_access.py: a stand-in session and direct calls
into the router, no TestClient and no database.
"""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi.encoders import jsonable_encoder  # noqa: E402

from app.models.clip import ActionType  # noqa: E402
from app.models.visibility import Visibility  # noqa: E402
from app.routers import clips  # noqa: E402
from app.schemas.clip import (  # noqa: E402
    ClipLabelsRequest,
    ClipTrimRequest,
    ClipVisibilityRequest,
)
from app.services import storage  # noqa: E402

OWNER = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()


class _Team:
    def __init__(self, owner_id):
        self.id = uuid.uuid4()
        self.owner_id = owner_id
        self.name = "Team"


class _Player:
    def __init__(self, team, name):
        self.id = uuid.uuid4()
        self.name = name
        self.team_id = team.id


class _Game:
    """Not a sample copy (no `is_sample`), and with a source to re-cut from."""

    def __init__(self, owner_id):
        self.id = uuid.uuid4()
        self.owner_id = owner_id
        self.raw_video_url = "raw/x.mp4"
        self.visibility = Visibility.private


class _Clip:
    """The columns ClipOut reads plus the ones the routes write."""

    def __init__(self, game, player_id=None):
        self.id = uuid.uuid4()
        self.game_id = game.id
        self.player_id = player_id
        self.action_type = ActionType.spike
        self.confidence = 0.8
        self.highlight_score = 0.5
        self.start_time = 10.0
        self.end_time = 16.0
        self.clip_url = "https://example.invalid/clip.mp4"
        self.thumbnail_url = None
        self.labels = ["spike"]
        self.created_at = datetime.now(timezone.utc)
        self.visibility = None


class _NoRow:
    def scalar_one_or_none(self):
        return None


class _FakeSession:
    """Dispatches get() by model name off whatever rows the test set up.

    `execute` exists only for the labels route's Correction upsert lookup,
    which finds no existing row and so adds one."""

    def __init__(self, *rows):
        self._rows = {(type(r).__name__.lstrip("_"), r.id): r for r in rows}
        self.added = []
        self.committed = False

    async def get(self, model, pk):
        return self._rows.get((model.__name__, pk))

    async def execute(self, _stmt):
        return _NoRow()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, _obj):
        return None


class _Celery:
    def __init__(self):
        self.sent = []

    def send_task(self, name, args):
        self.sent.append((name, args))


@pytest.fixture(autouse=True)
def _no_r2(monkeypatch):
    """Keep `_rewrite_urls` on the unsigned branch whatever the env holds."""
    monkeypatch.setattr(storage, "r2_configured", lambda: False)


@pytest.fixture
def celery(monkeypatch):
    fake = _Celery()
    monkeypatch.setattr(clips, "celery_app", fake)
    return fake


def _labels(session, clip):
    return asyncio.run(
        clips.update_clip_labels(clip.id, ClipLabelsRequest(labels=["dig"]), session, OWNER)
    )


def _trim(session, clip):
    return asyncio.run(
        clips.trim_clip(
            clip.id, ClipTrimRequest(start_delta=-2.0, end_delta=2.0), session, OWNER
        )
    )


def _visibility(session, clip):
    # `followers` needs no deployment flag; `public` would be gated by
    # PUBLIC_POSTING_ENABLED, which is not what these tests are about.
    return asyncio.run(
        clips.update_clip_visibility(
            clip.id, ClipVisibilityRequest(visibility=Visibility.followers), session, OWNER
        )
    )


WRITES = pytest.mark.parametrize(
    "write", [_labels, _trim, _visibility], ids=["labels", "trim", "visibility"]
)


@WRITES
def test_tagged_clip_keeps_its_player_name(write, celery):
    team = _Team(OWNER)
    player = _Player(team, "My Player")
    game = _Game(OWNER)
    clip = _Clip(game, player_id=player.id)
    session = _FakeSession(game, clip, player, team)

    out = write(session, clip)

    assert session.committed is True, "the write itself must still happen"
    assert out.player_id == player.id
    assert out.player_name == "My Player"


@WRITES
def test_untagged_clip_echoes_no_name(write, celery):
    team = _Team(OWNER)
    bystander = _Player(team, "Not Tagged")
    game = _Game(OWNER)
    clip = _Clip(game)
    session = _FakeSession(game, clip, bystander, team)

    out = write(session, clip)

    assert out.player_id is None
    assert out.player_name is None


@WRITES
def test_legacy_cross_tenant_tag_echoes_the_name_list_clips_shows(write, celery):
    """A row tagged before CF-234 with another tenant's player. `list_clips`
    names it (no ownership filter, CF-263), so the echo must match the listing
    rather than blank it on every edit."""
    foreign_team = _Team(OTHER_TENANT)
    foreign = _Player(foreign_team, "Legacy Tag")
    game = _Game(OWNER)
    clip = _Clip(game, player_id=foreign.id)
    session = _FakeSession(game, clip, foreign, foreign_team)

    out = write(session, clip)

    assert out.player_name == "Legacy Tag"


@WRITES
def test_dangling_player_id_echoes_no_name(write, celery):
    """A player_id whose row is gone answers None rather than raising."""
    game = _Game(OWNER)
    clip = _Clip(game, player_id=uuid.uuid4())
    session = _FakeSession(game, clip)

    out = write(session, clip)

    assert out.player_name is None


@WRITES
def test_player_name_survives_serialisation(write, celery):
    """What reaches the client is the serialised body, not the model object."""
    team = _Team(OWNER)
    player = _Player(team, "My Player")
    game = _Game(OWNER)
    clip = _Clip(game, player_id=player.id)
    session = _FakeSession(game, clip, player, team)

    out = write(session, clip)

    assert out.model_dump()["player_name"] == "My Player"
    assert jsonable_encoder(out)["player_name"] == "My Player"


def test_trim_still_queues_the_recut(celery):
    """The name lookup sits after the Celery dispatch; it must not displace it."""
    team = _Team(OWNER)
    player = _Player(team, "My Player")
    game = _Game(OWNER)
    clip = _Clip(game, player_id=player.id)
    session = _FakeSession(game, clip, player, team)

    _trim(session, clip)

    assert [name for name, _ in celery.sent] == ["recut_clip"]
