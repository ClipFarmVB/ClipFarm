"""CF-113: likes and comments — the invariants that hold without a database.

What is checkable here: that the metadata carries what migration 020 built
(the drift this stack has been burned by three times), that every write goes
through the shared post gate rather than re-deriving it, that each counter
write is gated on the row write actually landing, and that the queries the
routers run have the shape they claim. The counters under concurrency, the
visibility matrix against real rows and the cursor walk are exercised live
in `test_engagement_pg.py`.

Fake sessions and `asyncio.run`, like `test_follows.py`: the routers are
driven directly, the statements they issue are captured and compiled, and
nothing here needs Postgres.
"""
import ast
import asyncio
import pathlib
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from app.models.post import Post  # noqa: E402
from app.models.post_comment import PostComment  # noqa: E402
from app.models.post_like import PostLike  # noqa: E402
from app.routers import engagement as r  # noqa: E402
from app.routers import feed as feed_router  # noqa: E402
from app.routers import posts as posts_router  # noqa: E402
from app.services import cursors, engagement, post_read  # noqa: E402

VIEWER = uuid.uuid4()
AUTHOR = uuid.uuid4()
STRANGER = uuid.uuid4()


def _sql(stmt) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


# ── metadata agrees with migration 020 ──────────────────────────────────────


def test_the_engagement_tables_reach_the_metadata():
    """Every object 020 built, declared, so `create_all` and `--autogenerate`
    both see it. Three earlier migrations in this stack each left something
    in the database that the models did not mention."""
    from sqlalchemy import Index

    assert [c.name for c in PostLike.__table__.primary_key.columns] == ["post_id", "user_id"]
    assert {i.name for i in PostLike.__table__.indexes} == {"ix_post_likes_user_id"}

    comment_indexes = {i.name: i for i in PostComment.__table__.indexes}
    assert set(comment_indexes) == {"ix_post_comments_post_created", "ix_post_comments_author_id"}
    partial = comment_indexes["ix_post_comments_post_created"]
    assert isinstance(partial, Index)
    assert str(partial.dialect_options["postgresql"]["where"]) == "deleted_at IS NULL"
    names = {c.name for c in PostComment.__table__.constraints if c.name}
    assert "ck_post_comments_body_length" in names

    post_checks = {c.name for c in Post.__table__.constraints if c.name}
    assert {"ck_posts_like_count_non_negative", "ck_posts_comment_count_non_negative"} <= post_checks

    for table in (PostLike.__table__, PostComment.__table__):
        for fk in table.foreign_keys:
            assert fk.ondelete == "CASCADE", f"{table.name}.{fk.parent.name}"


def test_migration_020_names_every_object_the_models_declare():
    """The cheap guard against a fourth drift: every named index and
    constraint on the three tables appears, by name, in the migration text."""
    from pathlib import Path

    src = (Path(__file__).parent.parent / "alembic" / "versions" / "020_post_engagement.py").read_text()
    wanted = {
        "ix_post_likes_user_id",
        "ix_post_comments_post_created",
        "ix_post_comments_author_id",
        "ck_post_comments_body_length",
        "ck_posts_like_count_non_negative",
        "ck_posts_comment_count_non_negative",
    }
    declared = (
        {i.name for i in PostLike.__table__.indexes}
        | {i.name for i in PostComment.__table__.indexes}
        | {c.name for c in PostComment.__table__.constraints if c.name and c.name.startswith("ck_")}
        | {c.name for c in Post.__table__.constraints if c.name and c.name.startswith("ck_posts_")}
    )
    assert declared == wanted
    for name in wanted:
        assert name in src, f"{name} is declared on a model and absent from 020"


# ── the shared gate ──────────────────────────────────────────────────────────


class _Explodes:
    """A session no write may reach before the gate has answered."""

    async def execute(self, *a, **k):
        raise AssertionError("SQL issued before the post gate")

    async def get(self, *a, **k):
        raise AssertionError("SQL issued before the post gate")


def test_every_engagement_write_goes_through_the_shared_post_gate(monkeypatch):
    """The card: only users who can view a post may like or comment on it,
    through CF-108's helper rather than a re-derived rule. Pinned by making
    the helper say no and the session refuse everything else."""

    async def _denied(post_id, viewer_id, db):
        raise HTTPException(status_code=404, detail="Post not found")

    monkeypatch.setattr(post_read, "load_for_read", _denied)
    pid = uuid.uuid4()

    for call in (
        r.like_post(pid, VIEWER, _Explodes()),
        r.unlike_post(pid, VIEWER, _Explodes()),
        r.create_comment(pid, r.CommentCreate(body="hi"), VIEWER, _Explodes()),
        r.list_comments(pid, _Explodes(), VIEWER),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(call)
        assert exc.value.status_code == 404


# ── counters move only when the row write landed ─────────────────────────────


class _Result:
    def __init__(self, rowcount=0, scalar=None, rows=()):
        self.rowcount = rowcount
        self._scalar = scalar
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _Session:
    """Answers each `execute` from a script, records every statement."""

    def __init__(self, results):
        self.results = list(results)
        self.statements = []
        self.committed = self.rolled_back = 0

    async def execute(self, stmt, *a, **k):
        self.statements.append(stmt)
        return self.results.pop(0)

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1

    def add(self, obj):
        pass

    async def refresh(self, obj, *a):
        pass


def _post(author_id=AUTHOR, like_count=3, comment_count=1):
    return SimpleNamespace(
        id=uuid.uuid4(), author_id=author_id, like_count=like_count, comment_count=comment_count
    )


def _gate(monkeypatch, post):
    async def _ok(post_id, viewer_id, db):
        return post, SimpleNamespace(), SimpleNamespace()

    monkeypatch.setattr(post_read, "load_for_read", _ok)


def _updates(db):
    return [s for s in db.statements if _sql(s).startswith("UPDATE")]


def test_a_like_moves_the_counter_only_when_the_insert_landed(monkeypatch):
    """`ON CONFLICT DO NOTHING` plus rowcount: the tap that inserted the row
    increments; the double-tap (or the race's loser) does not, and is
    answered with the state that exists."""
    post = _post()
    _gate(monkeypatch, post)

    # Landed: INSERT rowcount 1, then the UPDATE ... RETURNING.
    db = _Session([_Result(rowcount=1), _Result(scalar=4)])
    out = asyncio.run(r.like_post(post.id, VIEWER, db))
    assert (out.liked, out.like_count) == (True, 4)
    assert len(_updates(db)) == 1 and "like_count + 1" in _sql(_updates(db)[0])
    assert "ON CONFLICT (post_id, user_id) DO NOTHING" in _sql(db.statements[0])
    assert db.committed == 1

    # Did not land: no UPDATE at all, the existing count is read back.
    db = _Session([_Result(rowcount=0), _Result(scalar=3)])
    out = asyncio.run(r.like_post(post.id, VIEWER, db))
    assert (out.liked, out.like_count) == (True, 3)
    assert _updates(db) == []
    assert db.rolled_back == 1 and db.committed == 0


def test_an_unlike_is_gated_on_the_delete_and_floors_the_decrement(monkeypatch):
    post = _post(like_count=1)
    _gate(monkeypatch, post)

    db = _Session([_Result(rowcount=1), _Result(scalar=0)])
    out = asyncio.run(r.unlike_post(post.id, VIEWER, db))
    assert (out.liked, out.like_count) == (False, 0)
    sql = _sql(_updates(db)[0]).lower()
    assert "greatest(posts.like_count + -1, 0)" in sql, "floored in SQL, not caught by the CHECK"

    # Nothing to delete → nothing to decrement; the count is reported as is.
    db = _Session([_Result(rowcount=0)])
    out = asyncio.run(r.unlike_post(post.id, VIEWER, db))
    assert (out.liked, out.like_count) == (False, 1)
    assert _updates(db) == []


def test_a_comment_delete_is_a_conditional_update(monkeypatch):
    """Soft delete, gated: `WHERE deleted_at IS NULL`, and the decrement only
    when that matched — so a second tap decrements nothing."""
    post = _post(comment_count=2)
    comment = SimpleNamespace(id=uuid.uuid4(), post_id=post.id, author_id=VIEWER, deleted_at=None)

    class _Db(_Session):
        async def get(self, model, key):
            return {PostComment: comment, Post: post}[model]

    db = _Db([_Result(rowcount=1), _Result(scalar=1)])
    asyncio.run(r.delete_comment(comment.id, VIEWER, db))
    hide, bump = db.statements
    assert "deleted_at IS NULL" in _sql(hide)
    assert "comment_count" in _sql(bump) and "greatest(" in _sql(bump).lower()

    db = _Db([_Result(rowcount=0)])
    asyncio.run(r.delete_comment(comment.id, VIEWER, db))
    assert len(db.statements) == 1, "a delete that matched nothing must not decrement"


def test_comment_deletion_is_author_or_post_owner():
    post = _post(author_id=AUTHOR)
    live = SimpleNamespace(id=uuid.uuid4(), post_id=post.id, author_id=VIEWER, deleted_at=None)
    gone = SimpleNamespace(id=uuid.uuid4(), post_id=post.id, author_id=VIEWER, deleted_at=object())

    class _Db(_Session):
        def __init__(self, comment):
            super().__init__([_Result(rowcount=1), _Result(scalar=0)])
            self.comment = comment

        async def get(self, model, key):
            return {PostComment: self.comment, Post: post}[model]

    for who in (VIEWER, AUTHOR):  # the author of the comment, the owner of the post
        asyncio.run(r.delete_comment(live.id, who, _Db(live)))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.delete_comment(live.id, STRANGER, _Db(live)))
    assert exc.value.status_code == 404, "404 not 403 — a 403 confirms the id"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.delete_comment(gone.id, VIEWER, _Db(gone)))
    assert exc.value.status_code == 404


def test_a_comment_whose_post_vanished_is_a_404_not_a_500(monkeypatch):
    """The FK violation surfaces inside the guard, via `_bump`'s autoflush,
    and nothing reads an ORM attribute after the rollback."""
    from sqlalchemy.exc import IntegrityError

    post = _post()
    _gate(monkeypatch, post)

    class _Db(_Session):
        async def execute(self, stmt, *a, **k):
            raise IntegrityError("insert", {}, Exception("fk"))

        async def get(self, model, key):
            return SimpleNamespace(id=VIEWER, username="v", display_name=None,
                                   avatar_url=None, username_is_generated=False)

    db = _Db([])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.create_comment(post.id, r.CommentCreate(body="hi"), VIEWER, db))
    assert exc.value.status_code == 404
    assert db.rolled_back == 1


def test_a_like_on_a_post_that_vanished_is_a_404_not_a_500(monkeypatch):
    """The same race as the comment above, on the other write.

    The author deletes the post between `load_for_read` and the insert, so
    `post_likes.post_id` has nothing to point at. Every sibling path answers
    404 for this — `create_comment` catches the IntegrityError, `_bump` raises
    when its RETURNING comes back empty — and `like_post` was the one that did
    not, so it answered 500.
    """
    from sqlalchemy.exc import IntegrityError

    post = _post()
    _gate(monkeypatch, post)

    class _Db(_Session):
        async def execute(self, stmt, *a, **k):
            raise IntegrityError("insert", {}, Exception("fk"))

    db = _Db([])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.like_post(post.id, VIEWER, db))
    assert exc.value.status_code == 404
    assert db.rolled_back == 1


def test_no_orm_attribute_is_read_after_a_rollback():
    """`follows.py`'s lesson, pinned the same way: after `rollback()` every
    loaded row is expired and the next attribute access is a `MissingGreenlet`
    from an `AsyncSession`. Ids are captured as locals first."""
    import inspect

    for fn in (r.like_post, r.create_comment):
        src = inspect.getsource(fn)
        after = src.split("db.rollback()", 1)
        if len(after) == 2:
            tail = after[1]
            assert not re.search(r"\b(post|comment|author)\.\w+", tail), fn.__name__


# ── the queries have the shape they claim ────────────────────────────────────


def test_the_liked_column_is_scoped_to_both_ends_and_absent_for_anonymous():
    sql = _sql(engagement.viewer_liked_column(VIEWER))
    assert "post_likes.post_id = posts.id" in sql, "correlated to the outer post"
    assert f"post_likes.user_id = '{VIEWER}'" in sql, "and to this viewer"
    assert "EXISTS" in sql
    assert "EXISTS" not in _sql(engagement.viewer_liked_column(None)).upper()


def test_the_feed_and_profile_queries_carry_the_liked_column():
    """`viewer_has_liked` is a column on the page query, not a second round
    trip — the property `test_the_page_costs_one_query` counts live."""
    assert "post_likes" in _sql(feed_router.feed_query(VIEWER))
    from app.services import access
    from sqlalchemy import select
    from app.models.clip import Clip

    anon = access.apply_post_visibility(
        select(Post, Clip, engagement.viewer_liked_column(None).label("viewer_has_liked")), None
    )
    assert "post_likes" not in _sql(anon), "anonymous pays for no subquery"


def test_comments_page_through_the_shared_cursor_window():
    pid = uuid.uuid4()
    sql = _sql(r.comments_query(pid, limit=7))
    assert "post_comments.deleted_at IS NULL" in sql
    assert "ORDER BY post_comments.created_at DESC, post_comments.id DESC" in sql
    assert "LIMIT 8" in sql, "limit + 1, discarded by split_page"
    assert "JOIN users" in sql

    from app.services import cursors
    from datetime import datetime, timezone

    token = cursors.encode(datetime(2026, 9, 1, tzinfo=timezone.utc), uuid.uuid4())
    paged = _sql(r.comments_query(pid, cursor=token))
    assert "(post_comments.created_at, post_comments.id) <" in paged, "row-value cursor"

    with pytest.raises(HTTPException) as exc:
        r.comments_query(pid, cursor="")
    assert exc.value.status_code == 400, "an empty cursor is malformed, not page 1"


def test_a_blank_or_oversized_comment_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        r.CommentCreate(body="   ")
    with pytest.raises(ValidationError):
        r.CommentCreate(body="x" * 501)
    assert r.CommentCreate(body="  ok  ").body == "ok"


def test_a_generated_handle_is_withheld_but_the_comment_stays():
    """`from_author`, not `_findable`: the comment renders with a null handle
    rather than vanishing, so `comment_count` and the visible list agree."""
    comment = SimpleNamespace(id=uuid.uuid4(), post_id=uuid.uuid4(), body="hi",
                              created_at=None)
    from datetime import datetime, timezone
    comment.created_at = datetime.now(timezone.utc)
    backfill = SimpleNamespace(id=uuid.uuid4(), username="johnsmith", display_name=None,
                               avatar_url=None, username_is_generated=True)
    out = r._render_comment(comment, backfill, r2_ready=False)
    assert out.body == "hi"
    assert out.author.username is None


def test_the_comment_page_is_scoped_to_the_post_it_was_asked_for():
    """The gate proves the *post* is readable; the query is what makes the page
    be about that post.

    Drop the `post_id` predicate and `GET /posts/{A}/comments` starts returning
    every live comment in the table, on every post, private ones included —
    and nothing else notices, because `load_for_read` still passes. The
    cursor-window test above asserts the ordering, the soft-delete filter, the
    limit and the join, and would stay green through exactly that change.
    """
    wanted, other = uuid.uuid4(), uuid.uuid4()
    sql = _sql(r.comments_query(wanted))
    assert f"post_comments.post_id = '{wanted}'" in sql
    assert str(other) not in sql

    # And the predicate must survive paging, which rebuilds the where clause.
    token = cursors.encode(datetime(2026, 8, 1, tzinfo=timezone.utc), uuid.uuid4())
    assert f"post_comments.post_id = '{wanted}'" in _sql(
        r.comments_query(wanted, cursor=token)
    )


def test_an_anonymous_reader_is_never_told_they_liked_something():
    """`false()`, not an unconstrained EXISTS or a bare literal `true`.

    A regression here tells every signed-out visitor they have liked every
    post in the feed — visible on the first page load, and invisible to the
    rest of the suite, which always passes a real viewer.
    """
    assert _sql(engagement.viewer_liked_column(None)).strip().lower() == "false"

    # The point-lookup form takes the same branch and must not query at all: a
    # signed-out reader has no row to find, and asking is a round trip per read.
    class _Explodes:
        async def execute(self, *_a, **_k):
            raise AssertionError("anonymous must not reach the database")

    assert asyncio.run(engagement.viewer_has_liked(_Explodes(), None, uuid.uuid4())) is False


def test_the_profile_grid_query_fills_the_liked_flag_for_a_signed_in_viewer():
    """`list_user_posts` is the other consumer of the widened row.

    The feed's copy is pinned above; this one was not, so a version that
    hardcoded `viewer_has_liked=False` on the profile grid — every post on
    your own profile showing unliked — shipped green.
    """
    sql = _sql(posts_router.user_posts_query(VIEWER, AUTHOR))
    assert "post_likes" in sql
    assert str(VIEWER) in sql
    # ...and an anonymous grid pays for no subquery, same as the feed's.
    assert "post_likes" not in _sql(posts_router.user_posts_query(None, AUTHOR))


def test_the_engagement_routes_are_registered_and_gated_with_the_rest_of_social():
    """Nothing else here touches the FastAPI wiring.

    Every test in this file drives the router coroutines directly, so deleting
    `app.include_router(engagement.router)` makes the whole feature
    unreachable with the suite still green — and so does renaming a path or
    changing a method. Both are asserted, and the registration is asserted to
    sit inside the `social_enabled` block: routed while the rest of the social
    surface is hidden, these would be the endpoints that serve other people's
    footage with nothing around them.
    """
    declared = {
        (route.path, method)
        for route in r.router.routes
        for method in getattr(route, "methods", set())
        if method != "HEAD"
    }
    assert declared == {
        ("/posts/{post_id}/like", "POST"),
        ("/posts/{post_id}/like", "DELETE"),
        ("/posts/{post_id}/comments", "GET"),
        ("/posts/{post_id}/comments", "POST"),
        ("/comments/{comment_id}", "DELETE"),
    }

    main_src = (pathlib.Path(r.__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(main_src)
    gated = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and "social_enabled" in ast.dump(node.test)
        and "engagement" in ast.dump(ast.Module(body=node.body, type_ignores=[]))
    ]
    assert gated, "engagement.router must be included under `if settings.social_enabled`"
