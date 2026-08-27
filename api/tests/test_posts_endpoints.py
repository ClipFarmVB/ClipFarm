"""CF-109: the posts endpoints, actually invoked.

`test_posts.py` covers the visibility *rules* as pure logic, which is the right
shape for them. What it could not cover is whether `create_post` and friends
apply those rules — a review finding, and a fair one: a table asserting
`RANK[post] <= RANK[clip]` is the implementation's own expression evaluated
against a description of itself, and it passes just as happily if the router
never consults it.

So these drive the endpoint coroutines with a stub session. No database: the
schema is Postgres-specific (native enums, UUID columns) and sqlite cannot hold
it, while a real Postgres would make the suite conditional on a service that
isn't there in the pre-commit hook. The stub is a dict keyed by (model, id),
which is exactly the surface these handlers touch — `get`, `add`, `commit`,
`refresh`, `delete` — so the branch coverage is real even though the storage
isn't.

`asyncio.run` rather than pytest-asyncio, deliberately: the api suite has no
async plugin today, and adding one to land these is a dependency the whole repo
then carries.
"""
import asyncio
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")
pytest.importorskip("boto3")

from fastapi import HTTPException  # noqa: E402

from app.models.clip import ActionType  # noqa: E402
from app.models.visibility import Visibility  # noqa: E402
from app.routers import posts as posts_router  # noqa: E402
from app.schemas.post import PostCreate, PostUpdate  # noqa: E402


class Game:
    def __init__(self, owner_id, visibility=Visibility.private):
        self.id = uuid.uuid4()
        self.owner_id = owner_id
        self.visibility = visibility


class Clip:
    def __init__(self, game, visibility=None):
        self.id = uuid.uuid4()
        self.game_id = game.id
        self.visibility = visibility
        self.clip_url = "s3://bucket/clip.mp4"
        self.thumbnail_url = None
        self.start_time = 10.0
        self.end_time = 18.0
        self.action_type = ActionType.spike
        self.highlight_score = 0.8


class User:
    def __init__(self, username="alice", generated=False):
        self.id = uuid.uuid4()
        self.username = username
        self.username_is_generated = generated
        self.display_name = "Alice"
        self.avatar_url = None


class StubSession:
    """Enough AsyncSession for these handlers, and no more.

    Keyed on the model *name* the router asks for, and the stub classes above
    are named to match — which is what lets `db.get(Clip, id)` inside the real
    handler find the fake clip and the handler itself run unmodified.
    """

    def __init__(self, *objects):
        self.rows: dict[tuple[str, uuid.UUID], object] = {}
        for obj in objects:
            self.rows[(type(obj).__name__, obj.id)] = obj
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.commits = 0

    async def get(self, model, pk):
        return self.rows.get((model.__name__, pk))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        # The server defaults a real database would have filled in.
        for field in ("like_count", "comment_count"):
            if getattr(obj, field, None) is None:
                setattr(obj, field, 0)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)

    async def delete(self, obj):
        self.deleted.append(obj)


@pytest.fixture(autouse=True)
def no_r2(monkeypatch):
    """Pass stored URLs through instead of presigning — otherwise every test
    here would need R2 credentials to exercise logic that has nothing to do
    with storage."""
    monkeypatch.setattr(posts_router.storage, "r2_configured", lambda: False)


def _run(coro):
    return asyncio.run(coro)


def _post(author, clip, visibility=Visibility.public):
    from app.models.post import Post

    post = Post(
        author_id=author.id,
        clip_id=clip.id,
        caption="original",
        visibility=visibility,
    )
    post.id = uuid.uuid4()
    post.like_count = post.comment_count = 0
    post.created_at = datetime.now(timezone.utc)
    return post


# -- create ------------------------------------------------------------------


def test_creating_a_public_post_over_a_private_clip_is_refused():
    """The rule the card carries, through the endpoint rather than beside it."""
    author = User()
    game = Game(author.id, Visibility.private)
    clip = Clip(game)  # NULL visibility: inherits private
    db = StubSession(game, clip, author)

    with pytest.raises(HTTPException) as exc:
        _run(
            posts_router.create_post(
                PostCreate(clip_id=clip.id, visibility=Visibility.public), author.id, db
            )
        )

    assert exc.value.status_code == 409
    assert "private" in exc.value.detail
    assert not db.added, "nothing may be written when the check refuses"


def test_the_409_names_the_ceiling_and_prescribes_nothing_impossible():
    """No write path for a clip's or a game's visibility exists yet, so the old
    message's "change the clip's visibility first" named a remedy the product
    does not have. It states the constraint instead."""
    author = User()
    game = Game(author.id, Visibility.followers)
    clip = Clip(game)
    db = StubSession(game, clip, author)

    with pytest.raises(HTTPException) as exc:
        _run(
            posts_router.create_post(
                PostCreate(clip_id=clip.id, visibility=Visibility.public), author.id, db
            )
        )
    assert "followers" in exc.value.detail, "the ceiling has to be in the message"
    assert "Change the clip" not in exc.value.detail


def test_a_post_at_or_below_the_clips_tier_is_created():
    author = User()
    game = Game(author.id, Visibility.public)
    clip = Clip(game)
    db = StubSession(game, clip, author)

    out = _run(
        posts_router.create_post(
            PostCreate(
                clip_id=clip.id, caption="  nice dig  ", visibility=Visibility.followers
            ),
            author.id,
            db,
        )
    )

    assert db.commits == 1 and len(db.added) == 1
    assert out.visibility is Visibility.followers
    assert out.caption == "nice dig", "the caption is stripped on the way in"
    assert out.playback.clip_url == clip.clip_url, "playback resolves from the clip"


def test_a_whitespace_only_caption_becomes_null_rather_than_blank():
    author = User()
    game = Game(author.id, Visibility.public)
    clip = Clip(game)
    db = StubSession(game, clip, author)

    out = _run(
        posts_router.create_post(
            PostCreate(clip_id=clip.id, caption="   ", visibility=Visibility.private),
            author.id,
            db,
        )
    )
    assert out.caption is None


def test_posting_someone_elses_clip_is_404_not_403():
    """Publishing is an owner action. 404 rather than 403 so the response does
    not confirm a clip id is real — the same choice the read path makes."""
    stranger = User()
    owner = User("bob")
    game = Game(owner.id, Visibility.public)
    clip = Clip(game)
    db = StubSession(game, clip, owner, stranger)

    with pytest.raises(HTTPException) as exc:
        _run(
            posts_router.create_post(
                PostCreate(clip_id=clip.id, visibility=Visibility.public), stranger.id, db
            )
        )
    assert exc.value.status_code == 404
    assert not db.added


def test_a_generated_handle_never_reaches_the_create_response():
    """The oracle, checked where a client actually sees it. Three separate
    paths have now had to withhold this, which is why it is pinned at the
    endpoint and not only at the schema."""
    author = User("johnsmith", generated=True)
    game = Game(author.id, Visibility.public)
    clip = Clip(game)
    db = StubSession(game, clip, author)

    out = _run(
        posts_router.create_post(
            PostCreate(clip_id=clip.id, visibility=Visibility.public), author.id, db
        )
    )
    assert out.author.username is None
    assert out.author.display_name == "Alice", "the rest of the card still renders"


# -- read, edit, delete ------------------------------------------------------


def test_a_stranger_cannot_read_a_private_post():
    author = User()
    game = Game(author.id, Visibility.public)
    clip = Clip(game)
    post = _post(author, clip, Visibility.private)
    db = StubSession(game, clip, author, post)

    with pytest.raises(HTTPException) as exc:
        _run(posts_router.get_post(post.id, db, uuid.uuid4()))
    assert exc.value.status_code == 404


def test_an_anonymous_reader_gets_a_public_post():
    author = User()
    game = Game(author.id, Visibility.public)
    clip = Clip(game)
    post = _post(author, clip, Visibility.public)
    db = StubSession(game, clip, author, post)

    assert _run(posts_router.get_post(post.id, db, None)).id == post.id


def test_a_public_post_over_a_clip_that_went_private_is_withdrawn():
    """Both gates, through the endpoint. The stored `visibility` says public and
    is not trusted — the clip is re-derived on every read, which is what makes
    create_post's check a UX guarantee rather than the security boundary."""
    author = User()
    game = Game(author.id, Visibility.public)
    clip = Clip(game, visibility=Visibility.private)
    post = _post(author, clip, Visibility.public)
    db = StubSession(game, clip, author, post)

    with pytest.raises(HTTPException) as exc:
        _run(posts_router.get_post(post.id, db, None))
    assert exc.value.status_code == 404

    # ...and its author still reads it.
    assert _run(posts_router.get_post(post.id, db, author.id)).id == post.id


def test_editing_someone_elses_post_404s_without_writing():
    author = User()
    game = Game(author.id, Visibility.public)
    clip = Clip(game)
    post = _post(author, clip)
    db = StubSession(game, clip, author, post)

    with pytest.raises(HTTPException) as exc:
        _run(
            posts_router.update_post(
                post.id, PostUpdate(caption="mine now"), uuid.uuid4(), db
            )
        )
    assert exc.value.status_code == 404
    assert db.commits == 0, "the 404 must come before the write, not after it"
    assert post.caption == "original"


def test_a_null_caption_leaves_it_alone_and_an_empty_one_clears_it():
    """The PATCH convention `PostUpdate` documents, asserted rather than
    described."""
    author = User()
    game = Game(author.id, Visibility.public)
    clip = Clip(game)
    post = _post(author, clip)
    db = StubSession(game, clip, author, post)

    _run(posts_router.update_post(post.id, PostUpdate(caption=None), author.id, db))
    assert post.caption == "original"

    _run(posts_router.update_post(post.id, PostUpdate(caption=""), author.id, db))
    assert post.caption is None


def test_delete_removes_the_post_and_only_the_post():
    author = User()
    game = Game(author.id, Visibility.public)
    clip = Clip(game)
    post = _post(author, clip)
    db = StubSession(game, clip, author, post)

    _run(posts_router.delete_post(post.id, author.id, db))
    assert db.deleted == [post]
    assert clip not in db.deleted, "unpublishing must not touch the footage"


def test_deleting_someone_elses_post_404s():
    author = User()
    game = Game(author.id, Visibility.public)
    clip = Clip(game)
    post = _post(author, clip)
    db = StubSession(game, clip, author, post)

    with pytest.raises(HTTPException) as exc:
        _run(posts_router.delete_post(post.id, uuid.uuid4(), db))
    assert exc.value.status_code == 404
    assert not db.deleted
