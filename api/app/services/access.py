"""Centralized read authorization (CF-108).

Every router used to assert `owner_id == user_id` for itself. That was correct
while owners were the only readers, but it puts the rule in six places — and the
moment one of them is allowed to return someone else's content, the others are
the leak surface. This module is the single place that answers "may this viewer
read this?", for both single-object fetches and list queries.

Two forms, deliberately kept in step:

* ``can_view_game`` / ``can_view_clip`` — for an object already loaded.
* ``visible_games_filter`` / ``apply_clip_visibility`` — so list endpoints
  filter **in SQL**. Post-filtering a page
  in Python silently breaks pagination (ask for 50, get 11) and reads rows the
  viewer may not see. They take the whole statement rather than handing back a
  predicate because the clip rule needs a ``Game`` join, and a caller who
  forgets it gets a cartesian product that fails *open* with no warning.

Writes are NOT covered here. Creating, editing and deleting stay owner-only, and
the routers keep their own ownership checks for those paths.

**Unauthenticated surface.** Allowing anonymous reads means ``GET /games/{id}``,
``GET /games/{id}/clips`` and ``GET /clips/{id}/share`` now reach the database
without a credential, joining ``GET /users/{handle}`` from CF-107 — four
unthrottled endpoints where there were none. Nothing can be public yet, so all
of that traffic 404s today, making this a load question rather than a disclosure
one. Rate limiting is tracked in CF-186 (#189) and needs to land before anything
is actually publishable; the 404-not-403 choice below means none of them is an
existence oracle in the meantime.

**One deliberate asymmetry.** A public clip inside a private game is reachable
by direct link (``GET /clips/{id}/share``) and through a collection, but
``GET /games/{id}/clips`` 404s the whole endpoint because the game itself isn't
viewable. That is intended: an override publishes *that clip*, not the right to
enumerate its game's contents. `test_public_clip_in_private_game_*` pins all
three paths so the split stays a decision rather than an accident.
"""
import uuid

from fastapi import HTTPException
from sqlalchemy import ColumnElement, Select, and_, or_

from app.models.clip import Clip
from app.models.game import Game
from app.models.visibility import Visibility


def is_follower(viewer_id: uuid.UUID | None, owner_id: uuid.UUID) -> bool:
    """Whether `viewer_id` is an accepted follower of `owner_id`.

    Always False until CF-110 (#140) builds the follow graph. That is the
    fail-closed answer: `followers`-tier content stays owner-only until there is
    a real follow relationship to check, rather than being readable by everyone
    in the meantime.

    CF-110 replaces the body with a lookup against `follows` where
    `status = 'accepted'`, and swaps the filter helpers below to an EXISTS
    subquery so list queries stay in SQL.
    """
    return False


def _effective(clip: Clip, game: Game) -> Visibility:
    """A clip's visibility, resolving NULL as "inherit from the game"."""
    return clip.visibility or game.visibility


def _may_read(viewer_id: uuid.UUID | None, owner_id: uuid.UUID, level: Visibility) -> bool:
    if viewer_id is not None and viewer_id == owner_id:
        return True  # the owner always sees their own content
    if level is Visibility.public:
        return True  # including signed-out visitors
    if level is Visibility.followers:
        return is_follower(viewer_id, owner_id)
    return False  # private


def can_view_game(viewer_id: uuid.UUID | None, game: Game | None) -> bool:
    """`viewer_id` is None for a signed-out visitor."""
    if game is None:
        return False
    return _may_read(viewer_id, game.owner_id, game.visibility)


def can_view_clip(viewer_id: uuid.UUID | None, clip: Clip | None, game: Game | None) -> bool:
    """The parent game is required — it carries the owner, and the clip's own
    visibility may be NULL meaning "inherit"."""
    if clip is None or game is None or clip.game_id != game.id:
        return False
    return _may_read(viewer_id, game.owner_id, _effective(clip, game))


def visible_games_filter(viewer_id: uuid.UUID | None) -> ColumnElement[bool]:
    """Predicate over `Game` alone — safe in any query where Game is the entity
    being selected, so it needs no join and no wrapper.

    No production caller yet: `list_games` stays owner-scoped and `get_game`
    goes through `assert_can_view_game`. CF-111's discovery listing is the first
    consumer. Kept rather than deleted because the CF-108 acceptance matrix
    covers games in list form, and `test_game_filter_*` pins it.
    """
    clauses = [Game.visibility == Visibility.public]
    if viewer_id is not None:
        clauses.append(Game.owner_id == viewer_id)
    # No `followers` clause: is_follower() is False for everyone until CF-110,
    # and emitting a clause that can never be true would only mislead a reader
    # of the generated SQL. CF-110 adds an EXISTS against `follows` here.
    return or_(*clauses)


def _clips_predicate(viewer_id: uuid.UUID | None) -> ColumnElement[bool]:
    """The clip visibility rule. Only valid where `Game` is already joined —
    which is why the only public way to get it is `apply_clip_visibility`."""
    inherited_public = and_(Clip.visibility.is_(None), Game.visibility == Visibility.public)
    clauses = [Clip.visibility == Visibility.public, inherited_public]
    if viewer_id is not None:
        clauses.append(Game.owner_id == viewer_id)
    return or_(*clauses)


def apply_clip_visibility(stmt: Select, viewer_id: uuid.UUID | None) -> Select:
    """Join `Game` and filter a clip query to what `viewer_id` may read.

    The join is done here rather than left to the caller because forgetting it
    fails *open*, silently. A bare `select(Clip).where(<predicate>)` compiles to

        FROM clips, games WHERE clips.visibility = 'public' OR ...

    — a cartesian product that returns every NULL-visibility clip in the table
    as long as one public game row exists anywhere, and SQLAlchemy emits no
    warning. For a predicate guarding someone's footage, "the caller has to
    remember" is not a strong enough guarantee, and the feed and discovery
    queries in CF-109/CF-111 are exactly where a fresh `select(Clip)` gets
    written.

    The caller supplies the rest of the query; do not join `Game` yourself.
    """
    return stmt.join(Game, Clip.game_id == Game.id).where(_clips_predicate(viewer_id))


def assert_can_view_game(viewer_id: uuid.UUID | None, game: Game | None) -> Game:
    """Return the game or raise 404.

    404 rather than 403 on purpose: a 403 confirms the object exists, which
    leaks which game ids are real to anyone probing. This mirrors what the
    routers already did for owner-only content.
    """
    if not can_view_game(viewer_id, game):
        raise HTTPException(status_code=404, detail="Game not found")
    assert game is not None  # narrowed by can_view_game
    return game
