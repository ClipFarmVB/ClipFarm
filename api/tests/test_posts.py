"""CF-109: post visibility rules.

The security-relevant part of posting is that a post can never widen access to
the footage behind it, and that withdrawing the clip withdraws the post. Both
are pure predicate logic, so they're testable without a database.
"""
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")
# The router pulls in services.storage for presigned playback URLs, which needs
# boto3. Present in CI (api/requirements-dev.txt); guarded so a bare
# environment skips instead of erroring at collection.
pytest.importorskip("boto3")

from app.models.visibility import Visibility  # noqa: E402
from app.routers import posts as posts_router  # noqa: E402
from app.services import access  # noqa: E402

AUTHOR = uuid.uuid4()
STRANGER = uuid.uuid4()
ANONYMOUS = None
RANK = access._RANK


class _Game:
    def __init__(self, visibility, owner_id=AUTHOR):
        self.id = uuid.uuid4()
        self.owner_id = owner_id
        self.visibility = visibility


class _Clip:
    def __init__(self, game, visibility=None):
        self.id = uuid.uuid4()
        self.game_id = game.id
        self.visibility = visibility


class _Post:
    def __init__(self, visibility, author_id=AUTHOR):
        self.id = uuid.uuid4()
        self.author_id = author_id
        self.visibility = visibility
        self.clip_id = None


# ── a post may never be wider than its clip ─────────────────────────────────


@pytest.mark.parametrize(
    "clip_level,post_level,allowed",
    [
        (Visibility.private, Visibility.private, True),
        (Visibility.private, Visibility.followers, False),
        (Visibility.private, Visibility.public, False),
        (Visibility.followers, Visibility.private, True),
        (Visibility.followers, Visibility.followers, True),
        (Visibility.followers, Visibility.public, False),
        (Visibility.public, Visibility.private, True),
        (Visibility.public, Visibility.followers, True),
        (Visibility.public, Visibility.public, True),
    ],
)
def test_post_cannot_exceed_its_clips_visibility(clip_level, post_level, allowed):
    """The rule create_post enforces: publishing must not be a back door to
    exposing footage the clip itself keeps private.

    Asserted against `access.at_most`, the function the router actually calls,
    rather than by re-evaluating `RANK[a] <= RANK[b]` — which was the
    implementation's own expression checked against a table of what that
    expression does, and passed just as well if nothing consulted it.
    `test_posts_endpoints.py` covers the same matrix through `create_post`.
    """
    assert access.at_most(post_level, clip_level) is allowed


def test_rank_covers_every_visibility_value():
    """A new tier added to the enum without a rank would raise KeyError inside
    `at_most` — at request time, not at import."""
    assert set(RANK) == set(Visibility)


# ── reading a post ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "viewer,post_level,expected",
    [
        (STRANGER, Visibility.public, True),
        (STRANGER, Visibility.followers, False),  # False until CF-110
        (STRANGER, Visibility.private, False),
        (ANONYMOUS, Visibility.public, True),
        (ANONYMOUS, Visibility.followers, False),
        (ANONYMOUS, Visibility.private, False),
        (AUTHOR, Visibility.private, True),  # your own post is always yours
    ],
)
def test_post_readability(viewer, post_level, expected):
    game = _Game(Visibility.public)
    clip = _Clip(game)
    post = _Post(post_level)
    post.clip_id = clip.id
    assert access.can_view_post(viewer, post, clip, game) is expected


def test_the_ladder_is_not_duplicated_in_the_router():
    """Review finding: a router-local copy of the private/followers/public
    ladder is a second place for it to drift, on the one table whose whole
    purpose is serving other people's footage. CF-110 changes both the
    object-level answer and the SQL one; a router copy would have picked up the
    first and silently missed the second.

    Asserts what the router *imports*, not that some removed name is absent.
    The previous version checked `not hasattr(posts_router, "_post_readable")`
    — the name of a thing already deleted — while the module still carried
    `_RANK` and its own `clip.visibility or game.visibility`. A test named for
    a duplicate it reaches into two lines earlier is not guarding anything.

    Both ladders now live in `access`: `may_read` decides who may read a tier,
    `at_most` orders two, and `widest_allowed`/`effective` resolve the inherit
    rule. The router calls all three and defines none.
    """
    import inspect

    src = inspect.getsource(posts_router)
    assert "access.at_most" in src, "the ordering ladder must be the shared one"
    assert "access.widest_allowed" in src, "and so must the inherit rule"
    assert "_RANK = {" not in src, "no second copy of the tier order"
    assert "clip.visibility or game.visibility" not in src, (
        "re-deriving the inherit rule here is the drift this test exists for"
    )
    for name in ("can_view_post", "apply_post_visibility", "at_most", "widest_allowed"):
        assert hasattr(access, name)


def test_followers_tier_now_admits_an_accepted_follower():
    """CF-110: the edge is resolved by the caller (follow_graph) and threaded
    in. Pending requests resolve False upstream, so `followers` posts stay
    author-only until a request is actually accepted."""
    game = _Game(Visibility.public)
    clip = _Clip(game)
    post = _Post(Visibility.followers)
    post.clip_id = clip.id
    assert access.can_view_post(STRANGER, post, clip, game) is False
    # The post's own tier is the author's to grant, so the *author* flag is what
    # opens it. Passing only the owner flag must not — that conflation is what
    # the CF-110 review found, and this is the assertion that keeps it fixed.
    assert access.can_view_post(
        STRANGER, post, clip, game, viewer_follows_owner=True
    ) is False
    assert access.can_view_post(
        STRANGER, post, clip, game, viewer_follows_author=True
    ) is True


# ── the page must be filtered in SQL, not after the LIMIT ───────────────────


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def test_post_visibility_is_applied_in_sql():
    """Review finding: applying `limit` in SQL and visibility in Python means
    the limit counts rows the viewer can't see. An author with 60 private posts
    then 20 public ones returns an *empty* page to a stranger, and no amount of
    paging reaches the readable rows."""
    from sqlalchemy import select

    from app.models.post import Post as PostModel

    sql = _sql(access.apply_post_visibility(select(PostModel), STRANGER)).upper()
    assert "WHERE" in sql
    assert "POSTS.VISIBILITY" in sql, "the post gate must be in the WHERE clause"
    assert "CLIPS.VISIBILITY" in sql, "the clip gate must be too"


def test_the_post_query_owns_its_joins():
    """Both gates read `clips` and `games`, so an unjoined query is a cartesian
    product that fails *open* — the same trap CF-108 fixed for clips, which is
    why this takes the statement rather than handing back a predicate."""
    from sqlalchemy import select

    from app.models.post import Post as PostModel

    sql = _sql(access.apply_post_visibility(select(PostModel), STRANGER)).upper()
    assert "JOIN CLIPS" in sql
    assert "JOIN GAMES" in sql
    assert "FROM POSTS, CLIPS" not in sql, "cartesian product — join missing"


def test_the_list_endpoint_does_not_filter_after_the_limit():
    """Pins the fix at the call site, not just in the helper.

    Asserted on the compiled SQL rather than on the source. The previous version
    checked `"continue" not in src`, which is brittle in both directions: it
    passes for a list comprehension with an `if`, which reintroduces exactly the
    post-LIMIT filtering it exists to prevent, and it would fail on an unrelated
    `continue` somewhere else in the function.

    What actually matters is that both gates reach the WHERE clause, so the
    LIMIT counts only rows this viewer may see.
    """
    import inspect

    src = inspect.getsource(posts_router.list_user_posts)
    assert "apply_post_visibility" in src

    from sqlalchemy import select

    from app.models.post import Post as PostModel

    sql = _sql(access.apply_post_visibility(select(PostModel), STRANGER)).lower()
    where_at = sql.index("where ")
    where = sql[where_at:]
    assert "posts.visibility" in where, "the post gate must be in the WHERE clause"
    assert "clips.visibility" in where, "and the clip gate too"


def test_a_public_post_over_a_private_clip_is_still_unreadable():
    """The regression this design exists to prevent.

    Even if a public post row somehow exists over a private clip — a bad
    migration, a direct DB edit, a future code path that skips create_post's
    check — the read path gates on the clip too, so the footage stays private.
    """
    game = _Game(Visibility.private)
    clip = _Clip(game)
    post = _Post(Visibility.public)
    post.clip_id = clip.id

    # The post's own tier says yes...
    assert access.may_read(STRANGER, post.author_id, post.visibility) is True
    # ...and the clip gate is what actually decides:
    assert access.can_view_clip(STRANGER, clip, game) is False
    assert access.can_view_post(STRANGER, post, clip, game) is False


def test_author_reads_their_own_private_post():
    game = _Game(Visibility.private)
    clip = _Clip(game)
    assert access.can_view_clip(AUTHOR, clip, game) is True


# ── account privacy vs content visibility ───────────────────────────────────


def test_account_privacy_does_not_clamp_post_visibility():
    """Pins a decision, not a preference.

    `users.is_private` governs whether *following* needs approval; it is not an
    outer bound on content. So a private account's `public` post stays readable
    by a stranger, because the author picked `public` for that post.

    Worth pinning because CF-110 read the same flag the other way for follower
    lists, and because the failure mode if it silently changed is the expensive
    direction: a user who set their account private and reasonably assumed it
    covered everything underneath. If this test ever fails, that is the argument
    being had — make sure it is being had on purpose.
    """
    game = _Game(Visibility.public)
    clip = _Clip(game)
    post = _Post(Visibility.public)
    post.clip_id = clip.id

    # The predicate never receives the User at all, which is the structural
    # reason the flag cannot leak in by accident.
    import inspect

    assert "is_private" not in inspect.getsource(access.can_view_post)
    assert access.can_view_post(STRANGER, post, clip, game) is True


def test_the_profile_posts_route_hides_generated_handles():
    """Review finding: `GET /posts?username=...` resolved the email-derived
    handles that `/u/{handle}` deliberately 404s, which is the same existence
    oracle through a second door.

    CF-109 fixed it with a local resolver because services/profiles.py did not
    exist yet; CF-110 lifted that module, so the assertion is that this route
    uses the shared one rather than keeping a second copy of the rule.
    """
    import inspect

    from app.services import profiles as profile_service

    assert "username_is_generated" in inspect.getsource(profile_service.by_handle)
    assert "profiles.by_handle" in inspect.getsource(posts_router.list_user_posts)
    assert not hasattr(posts_router, "_findable_author"), "the duplicate should be gone"


def test_the_profile_posts_route_does_not_lowercase_by_hand():
    """`.lower()` misses the strip that `handles.normalize` does, so the two
    routes disagreed about whether `' matt '` is a handle."""
    import inspect

    assert "username.lower()" not in inspect.getsource(posts_router)


def test_the_two_principals_are_not_interchangeable():
    """A post is gated on its author's tier and its clip's owner's, and those
    are the same person only by convention (`create_post` refuses to publish
    someone else's footage) rather than by any constraint. So the predicate
    takes two flags, and neither one answers the other's question."""
    import inspect

    game = _Game(Visibility.public, owner_id=STRANGER)   # owner != author
    clip = _Clip(game, visibility=Visibility.followers)  # clip tier is the owner's
    post = _Post(Visibility.public)                      # post tier is the author's
    post.clip_id = clip.id

    # Following the author opens the post but not the footage behind it, which
    # belongs to a different account.
    viewer = uuid.uuid4()
    assert access.can_view_post(
        viewer, post, clip, game, viewer_follows_author=True
    ) is False
    # Following both is what it takes.
    assert access.can_view_post(
        viewer, post, clip, game, viewer_follows_author=True, viewer_follows_owner=True
    ) is True

    sig = inspect.signature(access.can_view_post)
    assert sig.parameters["viewer_follows_author"].default is False
    assert sig.parameters["viewer_follows_owner"].default is False

# ── a generated handle must not ride out on a post ──────────────────────────


class _Author:
    def __init__(self, username, generated):
        self.id = AUTHOR
        self.username = username
        self.display_name = "Someone"
        self.avatar_url = None
        self.username_is_generated = generated


def test_a_generated_handle_is_withheld_from_the_post_author():
    """The third door.

    `get_profile` and `_findable_author` both refuse to resolve a generated
    handle, because the CF-107 backfill derives them from email local parts —
    `john.smith@…` becomes `johnsmith`. But `create_post` never requires a
    claimed handle, so a backfilled user can post publicly, and serializing the
    author straight from the row handed that handle to an anonymous
    `GET /posts/{id}`.
    """
    from app.schemas.post import PostAuthor

    generated = PostAuthor.from_author(_Author("johnsmith", True))
    assert generated.username is None, "a generated handle must not be published"
    assert generated.display_name == "Someone", "the rest of the card still renders"

    chosen = PostAuthor.from_author(_Author("matt", False))
    assert chosen.username == "matt", "a claimed handle is exactly what should show"


def test_no_renderer_serializes_an_author_the_raw_way():
    """`model_validate` skips the withholding entirely, so the constructor is
    the only supported path.

    Checked across every module that renders a post, not just this router. The
    feed was the second one, it used `model_validate`, and this test could not
    see it — which is the reason the body now lives in one place
    (`services/post_view`) and the reason this asserts over a set.
    """
    import inspect

    from app.routers import feed as feed_router
    from app.services import post_view

    assert "PostAuthor.from_author" in inspect.getsource(post_view)
    for module in (posts_router, feed_router, post_view):
        assert "PostAuthor.model_validate" not in inspect.getsource(module), (
            f"{module.__name__}: model_validate copies username verbatim, "
            "generated or not"
        )


def test_every_clip_response_resolves_its_derived_fields():
    """CF-109: `effective_visibility` must not depend on an echo remembering it.

    It is derived from the *game*, so `ClipOut.model_validate(clip)` alone
    always leaves it at its `private` default. The three PATCH handlers did
    exactly that while setting `source_available` beside it, so toggling a label
    on a clip in a public game made the composer grey out the wider tiers with
    "this clip is private" until a reload — the dead end the field exists to
    remove, reintroduced through the write path.

    Asserted structurally rather than by behaviour because the failure is an
    *omission*: there is no wrong value to catch, only a missing line at a call
    site that does not exist yet. One constructor is the invariant.

    Parsed with `ast` rather than grepped: the previous round rightly objected
    to substring assertions, and this module's own docstring names the call it
    forbids — a text count sees two and fails on prose.
    """
    import ast
    import inspect

    from app.routers import clips as clips_router

    tree = ast.parse(inspect.getsource(clips_router))
    builders = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(fn)
        if isinstance(node, ast.Attribute)
        and node.attr == "model_validate"
        and isinstance(node.value, ast.Name)
        and node.value.id == "ClipOut"
    }
    assert builders == {"_clip_out"}, (
        f"ClipOut is built in {sorted(builders)}; it must be built only in "
        "_clip_out, because every other field on it is derived from the game "
        "and an echo will forget one"
    )
