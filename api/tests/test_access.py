"""CF-108: read authorization matrix.

The acceptance criterion is a table over {owner, follower, stranger, anonymous}
x {private, followers, public}, for games and clips, in both single-object and
list form. These are the security boundary — if one cell flips, someone's
footage becomes readable by someone it shouldn't be.

Pure logic: `services/access.py` imports only SQLAlchemy and the models, so no
database is needed. The list-filter cases compile the predicate and assert on
the SQL rather than executing it.
"""
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.models.visibility import Visibility  # noqa: E402
from app.services import access  # noqa: E402

OWNER = uuid.uuid4()
FOLLOWER = uuid.uuid4()
STRANGER = uuid.uuid4()
ANONYMOUS = None


class _Game:
    def __init__(self, visibility, owner_id=OWNER):
        self.id = uuid.uuid4()
        self.owner_id = owner_id
        self.visibility = visibility


class _Clip:
    def __init__(self, game, visibility=None):
        self.id = uuid.uuid4()
        self.game_id = game.id
        self.visibility = visibility


# viewer, game visibility, expected
GAME_MATRIX = [
    (OWNER,     Visibility.private,   True),
    (OWNER,     Visibility.followers, True),
    (OWNER,     Visibility.public,    True),
    (FOLLOWER,  Visibility.private,   False),
    # False until CF-110 builds the follow graph — is_follower() is stubbed to
    # False so `followers` content stays owner-only rather than leaking.
    (FOLLOWER,  Visibility.followers, False),
    (FOLLOWER,  Visibility.public,    True),
    (STRANGER,  Visibility.private,   False),
    (STRANGER,  Visibility.followers, False),
    (STRANGER,  Visibility.public,    True),
    (ANONYMOUS, Visibility.private,   False),
    (ANONYMOUS, Visibility.followers, False),
    (ANONYMOUS, Visibility.public,    True),
]


@pytest.mark.parametrize("viewer,level,expected", GAME_MATRIX)
def test_game_visibility_matrix(viewer, level, expected):
    assert access.can_view_game(viewer, _Game(level)) is expected


@pytest.mark.parametrize("viewer,level,expected", GAME_MATRIX)
def test_clip_inherits_its_games_visibility(viewer, level, expected):
    """clip.visibility is NULL for every clip the pipeline produces, so this is
    the path that actually runs in production."""
    game = _Game(level)
    assert access.can_view_clip(viewer, _Clip(game), game) is expected


@pytest.mark.parametrize("viewer,level,expected", GAME_MATRIX)
def test_clip_override_beats_the_games_level(viewer, level, expected):
    """A per-clip value wins over the game's — here the game is private and the
    clip carries the tier under test."""
    game = _Game(Visibility.private)
    assert access.can_view_clip(viewer, _Clip(game, visibility=level), game) is expected


def test_private_clip_inside_a_public_game_stays_private():
    """The override must be able to restrict, not only widen."""
    game = _Game(Visibility.public)
    clip = _Clip(game, visibility=Visibility.private)
    assert access.can_view_clip(STRANGER, clip, game) is False
    assert access.can_view_clip(OWNER, clip, game) is True


def test_missing_objects_are_never_viewable():
    assert access.can_view_game(OWNER, None) is False
    assert access.can_view_clip(OWNER, None, None) is False
    assert access.can_view_clip(OWNER, _Clip(_Game(Visibility.public)), None) is False


def test_a_clip_cannot_borrow_another_games_permissions():
    """Guards against passing a mismatched game to widen a clip's access."""
    private_game = _Game(Visibility.private)
    other_public_game = _Game(Visibility.public, owner_id=STRANGER)
    clip = _Clip(private_game)
    assert access.can_view_clip(STRANGER, clip, other_public_game) is False


# ── list filters (applied in SQL) ────────────────────────────────────────────


def _sql(predicate) -> str:
    return str(predicate.compile(compile_kwargs={"literal_binds": True}))


def _clip_sql(viewer) -> str:
    """The SQL a clip list endpoint actually issues, join included."""
    from sqlalchemy import select

    from app.models.clip import Clip

    return _sql(access.apply_clip_visibility(select(Clip.id), viewer))


def test_game_filter_lets_anonymous_see_only_public():
    sql = _sql(access.visible_games_filter(ANONYMOUS))
    assert "'public'" in sql
    assert "owner_id" not in sql, "anonymous has no owned-content clause"


def test_game_filter_adds_the_viewers_own_content():
    assert "owner_id" in _sql(access.visible_games_filter(OWNER))


def test_clip_filter_resolves_inherited_visibility():
    """A NULL clip.visibility must be resolved against the game, or every
    pipeline-produced clip (all NULL) would be invisible."""
    sql = _clip_sql(ANONYMOUS)
    assert "IS NULL" in sql.upper()
    assert "games.visibility" in sql


def test_clip_filter_never_leaks_followers_tier_before_cf110():
    """The predicate must not contain a clause that admits `followers` while
    is_follower() is still stubbed False — the two would disagree."""
    for viewer in (OWNER, STRANGER, ANONYMOUS):
        assert "'followers'" not in _clip_sql(viewer)


def test_follower_stub_is_closed():
    """Documents the seam CF-110 opens. If this starts failing, the matrix rows
    marked False for FOLLOWER need revisiting in the same change."""
    assert access.is_follower(FOLLOWER, OWNER) is False


def test_clip_visibility_always_joins_the_game():
    """The regression guard for a fail-open predicate.

    The clip rule references `games.visibility`, so without a join the query
    compiles to `FROM clips, games` — a cartesian product returning every
    NULL-visibility clip as long as one public game exists, with no warning from
    SQLAlchemy. `apply_clip_visibility` owns the join so a caller cannot omit
    it; this asserts it stays that way.
    """
    for viewer in (OWNER, STRANGER, ANONYMOUS):
        sql = _clip_sql(viewer)
        assert "JOIN games" in sql, f"no join for viewer={viewer}: {sql}"
        assert "FROM clips, games" not in sql, "cartesian product — fails open"


def test_clip_visibility_survives_an_existing_join():
    """The collections endpoint joins CollectionClip first. The helper has to
    compose with that rather than assuming it owns the whole statement."""
    from sqlalchemy import select

    from app.models.clip import Clip
    from app.models.collection import CollectionClip

    stmt = select(Clip.id).join(CollectionClip, CollectionClip.clip_id == Clip.id)
    sql = _sql(access.apply_clip_visibility(stmt, OWNER))
    assert "JOIN collection_clips" in sql
    assert "JOIN games" in sql


def test_game_visibility_applier_matches_the_predicate():
    from sqlalchemy import select

    from app.models.game import Game

    applied = _sql(access.apply_game_visibility(select(Game.id), ANONYMOUS))
    assert "'public'" in applied
    assert "owner_id" not in applied


# ── the public-clip-in-a-private-game asymmetry (deliberate) ────────────────
#
# An override publishes *that clip*, not the right to enumerate its game's
# contents. Direct link and collection listing return it; the game's clip list
# 404s because the game itself isn't viewable. Pinned so the split stays a
# decision rather than something that drifts per endpoint.


def test_public_clip_in_private_game_is_viewable_directly():
    game = _Game(Visibility.private)
    clip = _Clip(game, Visibility.public)
    assert access.can_view_clip(STRANGER, clip, game) is True
    assert access.can_view_clip(ANONYMOUS, clip, game) is True


def test_public_clip_in_private_game_matches_the_list_filter():
    """What `GET /collections/{id}/clips` uses — the clip's own 'public' wins."""
    sql = _clip_sql(ANONYMOUS)
    assert "clips.visibility" in sql, "the clip's own level must be consulted"


def test_public_clip_in_private_game_still_hides_the_parent_game():
    """And so `GET /games/{id}/clips` 404s: assert_can_view_game runs first."""
    game = _Game(Visibility.private)
    assert access.can_view_game(STRANGER, game) is False
    with pytest.raises(Exception):
        access.assert_can_view_game(STRANGER, game)


# ── optional authentication ─────────────────────────────────────────────────

def test_optional_auth_degrades_an_unusable_token_to_anonymous():
    """A stale bearer must not deny access to content that works signed out.

    The browser attaches whatever token it has. If an expired one 401s, a user
    with a long-open tab clicking a public link is refused content that loads
    fine in a private window — while the route's own contract is "identify the
    caller if they're signed in", and an expired token is not signed in.
    """
    import asyncio

    from fastapi import HTTPException, status

    from app import auth

    async def _reject(**kwargs):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="expired")

    original = auth.get_current_user_id
    auth.get_current_user_id = _reject
    try:
        result = asyncio.run(
            auth.get_optional_user_id(credentials=object(), db=None)  # type: ignore[arg-type]
        )
    finally:
        auth.get_current_user_id = original

    assert result is None


def test_optional_auth_still_surfaces_a_real_failure():
    """Only 401 degrades. A JWKS outage is a 5xx and must not become a silent
    404 for every signed-in reader."""
    import asyncio

    from fastapi import HTTPException

    from app import auth

    async def _explode(**kwargs):
        raise HTTPException(status_code=503, detail="jwks unavailable")

    original = auth.get_current_user_id
    auth.get_current_user_id = _explode
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                auth.get_optional_user_id(credentials=object(), db=None)  # type: ignore[arg-type]
            )
        assert exc.value.status_code == 503
    finally:
        auth.get_current_user_id = original
