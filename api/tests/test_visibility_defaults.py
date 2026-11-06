"""The visibility columns' defaults are pinned here, because nothing else can see them (CF-413, #537).

`app/models/visibility.py` states the privacy model in one sentence: nothing
becomes readable by a non-owner without a deliberate act. `Game.visibility`
carries that as two declarations, an ORM `default=` and a SQL
`server_default=`, both `private`; `Clip.visibility` carries it as the absence
of one, where NULL means "inherit whatever the parent game says"
(`clip.py:43-48`).

The write-path guard, `test_visibility_write_paths_are_declared.py`, is an AST
scan over code that *assigns* the column. A default is not an assignment, so a
one-token edit to either declaration widens every new game, or every new clip,
with no failing test — measured on the ticket: `default=Visibility.public` on
the Game column leaves the whole api suite green on a machine without Postgres.
With one, `test_sample_game_pg.py` would catch it indirectly, through the
sample-game copier's round trip, but that test is about the copier and could be
refactored to pass `visibility=` explicitly without anyone noticing it was the
only pin.

So this reads the column objects off the mapped tables and asserts the
declarations directly. Off the tables, not off instances: a default is applied
at flush, and this suite has no session to flush through. The SQL default is
exercised by nothing locally either — the ORM always sends its Python default,
so the `server_default` only ever fires for a row inserted outside the ORM —
which is why the metadata pin is the only one it gets. The migration that
created the column (`alembic/versions/011_content_visibility.py`) is applied
history and is deliberately not pinned here.
"""
import pytest

pytest.importorskip("sqlalchemy")

from app.models.clip import Clip  # noqa: E402
from app.models.game import Game  # noqa: E402
from app.models.visibility import Visibility  # noqa: E402


def _column(model, name):
    # `__table__.c[name]` raises KeyError on a rename, so the test errors rather
    # than passing vacuously against an attribute that is no longer there.
    return model.__table__.c[name]


def test_game_visibility_orm_default_is_private():
    default = _column(Game, "visibility").default
    assert default is not None, "Game.visibility has no ORM default"
    assert default.is_scalar and not default.is_callable, (
        f"Game.visibility's ORM default is {default!r}, not a plain value"
    )
    # `is`, not `==`: Visibility is a str enum, so `==` would also accept the
    # spelling `default="private"`. A red here from a respelling is this pin
    # being literal about the member, not a widening — fix the spelling.
    assert default.arg is Visibility.private, (
        f"Game.visibility's ORM default is {default.arg!r}; new games must be "
        "private (CF-108, epic decision 1)"
    )


def test_game_visibility_server_default_is_private():
    server_default = _column(Game, "visibility").server_default
    assert server_default is not None, "Game.visibility has no server default"
    # A DefaultClause holds the plain string, so `==` against the member's value
    # is the right comparison; it also rejects `server_default=Visibility.public`.
    assert server_default.arg == Visibility.private.value, (
        f"Game.visibility's server default is {server_default.arg!r}; a row "
        "inserted outside the ORM must come up private too"
    )


def test_game_visibility_is_not_nullable():
    # NULL on a game would mean nothing — there is no parent to inherit from —
    # and a nullable column with a default is one `visibility=None` away from
    # a row no filter in services/access.py has an answer for.
    assert _column(Game, "visibility").nullable is False


def test_clip_visibility_defaults_to_null_meaning_inherit_the_game():
    """NULL is the override's "not set"; any default, Python or SQL, breaks it.

    `clip.py:43-48` says why: copying the game's value onto each clip at
    creation goes stale the moment the game's visibility changes. A
    `server_default` would do the same for rows inserted outside the ORM,
    so it is pinned absent alongside the Python default.
    """
    col = _column(Clip, "visibility")
    assert col.default is None, (
        f"Clip.visibility has an ORM default ({col.default.arg!r}); it must be "
        "None so a new clip inherits its game"
    )
    assert col.server_default is None, (
        f"Clip.visibility has a server default ({col.server_default.arg!r}); it "
        "must have none so a new clip inherits its game"
    )
    assert col.nullable is True, "Clip.visibility must allow NULL — that is the inherit value"
