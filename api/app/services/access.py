"""Centralized read authorization (CF-108).

Every router used to assert `owner_id == user_id` for itself. That was correct
while owners were the only readers, but it puts the rule in six places — and the
moment one of them is allowed to return someone else's content, the others are
the leak surface. This module is the single place that answers "may this viewer
read this?", for both single-object fetches and list queries.

Four entry points, deliberately kept in step:

* ``can_view_game`` / ``can_view_clip`` / ``can_view_post`` — for an object
  already loaded.
* ``can_identify`` — may a caller attach the game's title and its players'
  names. Not the same question as reading the clip; see the asymmetry below.
  Unlike the others this one is a single endpoint's gate rather than a rule the
  system keeps — its docstring says which, and CF-283 (#330) settles it.
* ``visible_games_filter`` — a predicate for a game list. The rule is over
  ``Game`` alone, so it needs no join and there is nothing to get wrong.
* ``apply_clip_visibility`` / ``apply_post_visibility`` — take the whole
  statement and return it joined and filtered. A predicate would not be safe
  here: the clip rule reads ``games.visibility``, and a caller who forgets the
  join gets a cartesian product that fails *open* with no warning from
  SQLAlchemy.

Both list forms filter **in SQL**. Post-filtering a page in Python silently
breaks pagination (ask for 50, get 11) and reads rows the viewer may not see.

**Posts live here too, not in the posts router.** A post is the one object
whose whole purpose is serving someone else's footage, so a second copy of the
private/followers/public ladder next to it is the most expensive place in the
codebase for the two copies to drift. CF-110 swaps ``is_follower`` for a real
lookup *and* the list filters for an EXISTS; a router-local predicate would have
picked up the first half and silently missed the second, leaving the followers
tier for posts filtering in Python.

**`users.is_private` does not enter into any of this**, and that is a decision
rather than an oversight. Visibility is a property of the content: a private
account's `public` post is readable by a signed-out stranger, because the author
chose `public` for that post. The account flag governs one thing only — whether
following requires approval.

The alternative reading is that the account switch should be an outer bound
clamping every post beneath it, and CF-110 took that view for follower *lists*
(owner-only when the account is private, because who follows you is information
about you). The two are reconcilable — a list is about other people, a post is
about the thing you deliberately published — but the asymmetry is worth knowing
before either is changed. `test_account_privacy_does_not_clamp_post_visibility`
pins the current behaviour so flipping it has to be deliberate; CF-116 is where
that argument belongs if it is had.

Writes are NOT covered here. Creating, editing and deleting stay owner-only, and
the routers keep their own ownership checks for those paths.

**Unauthenticated surface.** Allowing anonymous reads means ``GET /games/{id}``,
``GET /games/{id}/clips``, ``GET /clips/{id}/share`` and
``GET /clips/{id}/download`` now reach the database without a credential,
joining ``GET /users/{handle}`` from CF-107 — five unthrottled endpoints where
there were none. The download one is the most expensive: it mints an attachment
URL for the full clip, so an unthrottled caller can pull the bytes rather than
just a row. Nothing can be public yet, so all of that traffic 404s today, making
this a load question rather than a disclosure one.

**That last sentence expires with CF-109.** The safety here is not the 404
choice, it is that no row can be set `public` — so the moment CF-109 lands the
visibility setter, an unauthenticated caller can walk ``/clips/{id}/download``
and pull full clip bytes, unthrottled, with an egress bill attached. That makes
rate limiting (CF-186, #189) a blocker on CF-109 rather than a parallel task,
and this endpoint is what changed the severity of that ordering. The dependency
is recorded on CF-109 (#139) too — a paragraph in a module nobody has to open
is not an ordering constraint. The 404-not-403 choice below keeps none of them
an existence oracle in the meantime.

**Player names ride along with a viewable clip, by design (CF-263).** The
listings attach ``player_name`` with a bare id lookup and no ownership filter,
so whoever may read a clip may read the name tagged on it — including an
anonymous viewer of a public one. That is the intended product behaviour:
publishing a clip publishes it *with* its attribution, which is what makes a
share link or a feed entry worth anything. It is written down here, and pinned
by ``test_public_player_name.py``, because a missing ``where`` is otherwise
indistinguishable from an oversight and the next reader will "fix" it.

Note what this does *not* license. The name is readable through a clip the
viewer may already see; it is not enumerable, and no write path accepts a
foreign ``player_id`` (CF-234 closed the one that did). The orphan case — a
player whose ``team_id`` is NULL is visible here yet unmanageable by its owner,
because every editing route 404s on a null team — is a real problem and a
separate one, tracked in CF-238 (#241).

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
from app.models.post import Post
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


# Least → most visible. Exported because publishing needs to *compare* two
# tiers, not just evaluate one: a post may never be wider than the clip behind
# it. The comparison lived in the posts router next to a second copy of
# `clip.visibility or game.visibility`, which is the duplication this module's
# docstring argues against — the readability ladder moved and the ordering one
# did not.
_RANK = {Visibility.private: 0, Visibility.followers: 1, Visibility.public: 2}


def at_most(requested: Visibility, allowed: Visibility) -> bool:
    """Is `requested` no wider than `allowed`?

    A *write*-side rule, unlike everything else here, and deliberately so: it is
    the same ladder, and a second ordering of the same three values is exactly
    what drifts when a tier is added.
    """
    return _RANK[requested] <= _RANK[allowed]


def widest_allowed(clip: Clip | None, game: Game | None) -> Visibility:
    """The most permissive tier a post over this clip may take.

    Named separately from `effective` because the API hands this to clients so
    the composer can grey out what it cannot offer, and "the ceiling for a post"
    is the thing being published rather than an internal detail of the clip.
    Falls back to `private` when either side is missing — fail closed.
    """
    if clip is None or game is None:
        return Visibility.private
    return _effective(clip, game)


def may_read(viewer_id: uuid.UUID | None, owner_id: uuid.UUID, level: Visibility) -> bool:
    """The tier ladder itself, for any object that carries an owner and a level.

    Public rather than private because posts need the same ladder, and the only
    thing worse than exporting it is having it written out a second time.
    """
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
    return may_read(viewer_id, game.owner_id, game.visibility)


def can_view_clip(viewer_id: uuid.UUID | None, clip: Clip | None, game: Game | None) -> bool:
    """The parent game is required — it carries the owner, and the clip's own
    visibility may be NULL meaning "inherit"."""
    if clip is None or game is None or clip.game_id != game.id:
        return False
    return may_read(viewer_id, game.owner_id, _effective(clip, game))


def can_identify(viewer_id: uuid.UUID | None, game: Game | None) -> bool:
    """Whether a caller may attach the game's title and its players' names.

    **Scope: this is one endpoint's gate, not a module-wide invariant.** Unlike
    its neighbours it does not describe how the system behaves — `collections.py`
    hands `player_name` to every clip surviving `apply_clip_visibility`, which
    gates on the *clip*, so for a public clip in a private game one signed-in
    viewer gets two answers:

        GET /clips/{id}/download        -> withholds the player's name
        GET /collections/{id}/clips     -> returns it

    CF-283 (#330) picks one. Read what follows as the argument for the answer
    this side took, not as the rule in force.

    The argument: identification is a different question from readability, and
    it differs in exactly the asymmetric case above. A public clip inside a
    private game is readable by direct link, but the game's title and the
    tagged player's real name are not — /share discloses neither and list_clips
    is gated on the game — so naming the file after them would hand both to a
    caller who could not otherwise reach either, on footage of named young
    people. That argues for the game's own predicate, which is what this
    returns.

    It is named rather than inlined because the question recurs: CF-100's
    download filename asks it (the name rides in the presigned URL in
    cleartext), CF-101's zip entries will ask it again, and a rule living
    inline in one router is one the second caller re-derives — differently.
    Whichever way CF-283 settles, it settles here.
    """
    return can_view_game(viewer_id, game)


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


def can_view_post(
    viewer_id: uuid.UUID | None,
    post: Post | None,
    clip: Clip | None,
    game: Game | None,
) -> bool:
    """Two gates, both required.

    The post's own tier decides whether it was published to this viewer; the
    clip's decides whether the footage behind it is still theirs to see. A clip
    that goes private after being posted must take its post with it, so a post
    can never be readable on its own say-so.
    """
    if post is None or clip is None or post.clip_id != clip.id:
        return False
    return may_read(viewer_id, post.author_id, post.visibility) and can_view_clip(
        viewer_id, clip, game
    )


def _posts_predicate(viewer_id: uuid.UUID | None) -> ColumnElement[bool]:
    """The post's own tier. Private, like the clip one — a post query is only
    correct with the clip gate alongside it, which `apply_post_visibility` is
    what guarantees."""
    clauses = [Post.visibility == Visibility.public]
    if viewer_id is not None:
        clauses.append(Post.author_id == viewer_id)
    # No `followers` clause until CF-110, for the same reason as games above.
    return or_(*clauses)


def apply_post_visibility(stmt: Select, viewer_id: uuid.UUID | None) -> Select:
    """Join `Clip` and `Game` and filter a post query to what `viewer_id` may read.

    Both gates land in the WHERE clause, which is the point: filtering posts in
    Python after a `LIMIT` breaks the page rather than the query. An author with
    60 private posts followed by 20 public ones would return an *empty* page to
    a stranger asking for 50 — and no amount of paging would ever reach the
    readable rows, because the limit counted rows the viewer can't see.

    The caller supplies the rest of the query; do not join `Clip` or `Game`
    yourself.
    """
    return (
        stmt.join(Clip, Post.clip_id == Clip.id)
        .join(Game, Clip.game_id == Game.id)
        .where(_posts_predicate(viewer_id), _clips_predicate(viewer_id))
    )


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
