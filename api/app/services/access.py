"""Centralized read authorization (CF-108).

Every router used to assert `owner_id == user_id` for itself. That was correct
while owners were the only readers, but it puts the rule in six places — and the
moment one of them is allowed to return someone else's content, the others are
the leak surface. This module is the single place that answers "may this viewer
read this?", for both single-object fetches and list queries.

Three entry points, deliberately kept in step:

* ``can_view_game`` / ``can_view_clip`` / ``can_view_post`` — for an object
  already loaded.
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
from sqlalchemy import ColumnElement, Select, and_, or_, select

from app.models.clip import Clip
from app.models.follow import Follow, FollowStatus
from app.models.game import Game
from app.models.post import Post
from app.models.visibility import Visibility


def accepted_follow_exists(viewer_id: uuid.UUID, owner_col) -> ColumnElement[bool]:
    """SQL half of the `followers` tier: does `viewer_id` follow `owner_col`?

    An EXISTS correlated to the outer query, so list endpoints resolve the tier
    in the database rather than by pulling rows into Python — which is the same
    rule as everywhere else here, for the same reason.

    `status = 'accepted'` is the security-relevant part. A pending request must
    grant nothing: until the target approves, a requester sees exactly what a
    stranger sees, which is what makes "private by default" more than a label.
    """
    return (
        select(Follow.id)
        .where(
            Follow.follower_id == viewer_id,
            Follow.followee_id == owner_col,
            Follow.status == FollowStatus.accepted,
        )
        .exists()
    )


def effective(clip: Clip | None, game: Game | None) -> Visibility | None:
    """A clip's visibility, resolving NULL as "inherit from the game".

    Public because the read paths need the resolved tier to decide whether a
    follow lookup is worth issuing at all — asking `access` beats each router
    re-deriving `clip.visibility or game.visibility` and getting the inherit
    rule subtly wrong.
    """
    if clip is None or game is None:
        return None
    return _effective(clip, game)


def _effective(clip: Clip, game: Game) -> Visibility:
    """Non-optional form, for callers that have already narrowed both."""
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


def may_read(
    viewer_id: uuid.UUID | None,
    owner_id: uuid.UUID,
    level: Visibility,
    viewer_follows_owner: bool = False,
) -> bool:
    """The tier ladder itself, for any object that carries an owner and a level.

    Public rather than private because posts need the same ladder, and the only
    thing worse than exporting it is having it written out a second time.

    `viewer_follows_owner` is resolved by the caller (see
    `services.follow_graph`) rather than looked up here: this module stays
    synchronous and database-free, which is what lets it be tested as pure logic
    and reused from anywhere. **It defaults to False**, so a caller that forgets
    to resolve the edge grants *less* access, not more.
    """
    if viewer_id is not None and viewer_id == owner_id:
        return True  # the owner always sees their own content
    if level is Visibility.public:
        return True  # including signed-out visitors
    if level is Visibility.followers:
        return viewer_follows_owner
    return False  # private


def can_view_game(
    viewer_id: uuid.UUID | None,
    game: Game | None,
    *,
    viewer_follows_owner: bool = False,
) -> bool:
    """`viewer_id` is None for a signed-out visitor."""
    if game is None:
        return False
    return may_read(viewer_id, game.owner_id, game.visibility, viewer_follows_owner)


def can_view_clip(
    viewer_id: uuid.UUID | None,
    clip: Clip | None,
    game: Game | None,
    *,
    viewer_follows_owner: bool = False,
) -> bool:
    """The parent game is required — it carries the owner, and the clip's own
    visibility may be NULL meaning "inherit"."""
    if clip is None or game is None or clip.game_id != game.id:
        return False
    return may_read(
        viewer_id, game.owner_id, _effective(clip, game), viewer_follows_owner
    )


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
        # Anonymous viewers get no `followers` branch at all — they can't follow
        # anyone, so an always-false EXISTS on every signed-out read is pure
        # cost with no effect on the result.
        clauses.append(
            and_(
                Game.visibility == Visibility.followers,
                accepted_follow_exists(viewer_id, Game.owner_id),
            )
        )
    return or_(*clauses)


def _clips_predicate(viewer_id: uuid.UUID | None) -> ColumnElement[bool]:
    """The clip visibility rule. Only valid where `Game` is already joined —
    which is why the only public way to get it is `apply_clip_visibility`."""
    inherited_public = and_(Clip.visibility.is_(None), Game.visibility == Visibility.public)
    clauses = [Clip.visibility == Visibility.public, inherited_public]
    if viewer_id is not None:
        clauses.append(Game.owner_id == viewer_id)
        # `followers` either on the clip or inherited from its game. Written as
        # one EXISTS over both cases rather than one per branch: the two differ
        # only in which column carries the tier, and the subquery is identical.
        clauses.append(
            and_(
                or_(
                    Clip.visibility == Visibility.followers,
                    and_(
                        Clip.visibility.is_(None),
                        Game.visibility == Visibility.followers,
                    ),
                ),
                accepted_follow_exists(viewer_id, Game.owner_id),
            )
        )
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
    *,
    viewer_follows_author: bool = False,
    viewer_follows_owner: bool = False,
) -> bool:
    """Two gates, both required.

    The post's own tier decides whether it was published to this viewer; the
    clip's decides whether the footage behind it is still theirs to see. A clip
    that goes private after being posted must take its post with it, so a post
    can never be readable on its own say-so.
    """
    if post is None or clip is None or post.clip_id != clip.id:
        return False
    # Two principals, two flags. The post's tier belongs to its author and the
    # clip's to the game's owner, and they are the same person only because
    # create_post refuses to publish someone else's footage — an invariant held
    # by one write path and by nothing in the schema. Collapsing them into one
    # boolean was correct today and silently wrong the moment anything transfers
    # a game, which would authorize a post read against a follow edge to the
    # wrong account. Callers that know they coincide pass the same value twice.
    return may_read(
        viewer_id, post.author_id, post.visibility, viewer_follows_author
    ) and can_view_clip(viewer_id, clip, game, viewer_follows_owner=viewer_follows_owner)


def _posts_predicate(viewer_id: uuid.UUID | None) -> ColumnElement[bool]:
    """The post's own tier. Private, like the clip one — a post query is only
    correct with the clip gate alongside it, which `apply_post_visibility` is
    what guarantees."""
    clauses = [Post.visibility == Visibility.public]
    if viewer_id is not None:
        clauses.append(Post.author_id == viewer_id)
        clauses.append(
            and_(
                Post.visibility == Visibility.followers,
                accepted_follow_exists(viewer_id, Post.author_id),
            )
        )
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


def assert_can_view_game(
    viewer_id: uuid.UUID | None,
    game: Game | None,
    *,
    viewer_follows_owner: bool = False,
) -> Game:
    """Return the game or raise 404.

    404 rather than 403 on purpose: a 403 confirms the object exists, which
    leaks which game ids are real to anyone probing. This mirrors what the
    routers already did for owner-only content.
    """
    if not can_view_game(viewer_id, game, viewer_follows_owner=viewer_follows_owner):
        raise HTTPException(status_code=404, detail="Game not found")
    assert game is not None  # narrowed by can_view_game
    return game
