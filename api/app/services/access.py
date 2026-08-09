"""Centralized read authorization (CF-108).

Every router used to assert `owner_id == user_id` for itself. That was correct
while owners were the only readers, but it puts the rule in six places — and the
moment one of them is allowed to return someone else's content, the others are
the leak surface. This module is the single place that answers "may this viewer
read this?", for both single-object fetches and list queries.

Two forms, deliberately kept in step:

* ``can_view_game`` / ``can_view_clip`` — for an object already loaded.
* ``visible_games_filter`` / ``visible_clips_filter`` — SQLAlchemy predicates so
  list endpoints filter **in SQL**. Post-filtering a page in Python silently
  breaks pagination (ask for 50, get 11) and reads rows the viewer may not see.

Writes are NOT covered here. Creating, editing and deleting stay owner-only, and
the routers keep their own ownership checks for those paths.
"""
import uuid

from sqlalchemy import ColumnElement, and_, or_

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
    """Predicate for `select(Game).where(...)`."""
    clauses = [Game.visibility == Visibility.public]
    if viewer_id is not None:
        clauses.append(Game.owner_id == viewer_id)
    # No `followers` clause: is_follower() is False for everyone until CF-110,
    # and emitting a clause that can never be true would only mislead a reader
    # of the generated SQL. CF-110 adds an EXISTS against `follows` here.
    return or_(*clauses)


def visible_clips_filter(viewer_id: uuid.UUID | None) -> ColumnElement[bool]:
    """Predicate for a `select(Clip).join(Game)` list query.

    Requires the Game to be joined — a clip's own visibility can be NULL, and
    resolving that needs the game's value, exactly like `_effective` above.
    """
    inherited_public = and_(Clip.visibility.is_(None), Game.visibility == Visibility.public)
    clauses = [Clip.visibility == Visibility.public, inherited_public]
    if viewer_id is not None:
        clauses.append(Game.owner_id == viewer_id)
    return or_(*clauses)


def assert_can_view_game(viewer_id: uuid.UUID | None, game: Game | None) -> Game:
    """Return the game or raise 404.

    404 rather than 403 on purpose: a 403 confirms the object exists, which
    leaks which game ids are real to anyone probing. This mirrors what the
    routers already did for owner-only content.
    """
    from fastapi import HTTPException

    if not can_view_game(viewer_id, game):
        raise HTTPException(status_code=404, detail="Game not found")
    assert game is not None  # narrowed by can_view_game
    return game
