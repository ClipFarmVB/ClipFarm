"""Only the declared write paths may widen anything (CF-109b, #398).

**This is the narrowed successor to `test_no_visibility_write_path.py`**, which
CF-109b deleted. That file banned every write to a clip's or a game's
visibility anywhere under `app/`, because at the time there was no legitimate
one — and it said, correctly, that deleting it should be a decision recorded in
the setter's diff.

Deleting it *wholesale* was more than that decision justified, and this file is
the difference. CF-109b made exactly two clip write paths legitimate. It made
none for games, and it asserts in three separate places — `models/visibility.py`,
`render.yaml`, and the setter's own docstring — that a game setter is
*deliberately* absent rather than merely pending, because raising a game
publishes every clip in it. Without this file that claim is prose: adding
`game.visibility = ...` to the game-rename endpoint would silently publish
every clip in the game, and the suite would stay green.

So the rule is now two rules:

* **`Game.visibility`: written by nothing, anywhere.** Unchanged from before.
* **`Clip.visibility`: written only in the functions named below.** The tier
  is gated on `PUBLIC_POSTING_ENABLED` in both of them
  (`services/publishing.py`); a third path would reach `public` with the flag
  off and nothing would notice.
* `Post.visibility` is the feature and was always exempt.

`_DECLARED_CLIP_WRITERS` is the whole exemption. Adding to it is a real
decision — the same one this file's predecessor existed to force — and the
right way to make it is to add the (file, function) pair here, in a diff,
alongside a test that the new path is gated.

The scope is the whole `app/` package, not `routers/`, for the reason the
original gave: CF-109 put the visibility ladder in `services/access.py`, so a
setter is *more* likely to be written as a service helper than as router-local
code. A guard that misses the idiomatic home of the thing it guards is worse
than none, because the green check is what stops anyone looking.

**What it can and cannot see.** It reads the AST for attribute writes —
including the ones bound inside a tuple or list target, which it missed until a
round wrote `game.visibility, game.title = ...` and watched the suite stay
green — for `setattr`, for Core `update(...).values(visibility=...)`, and for a
row constructed wide (`Clip(visibility=...)`).

Shapes known to get past are listed below. The list is not exhaustive — this
paragraph has twice claimed coverage it did not have:

* **Raw SQL in a string.** Visible in review in a way `clip.visibility = ...` is
  not, which is the original argument and still holds.
* **A splatted mapping** — `values(**payload)`, or a dict built elsewhere and
  passed positionally. A literal positional dict is not special-cased either;
  the entity check above is what would have to grow.
* **Indirection through a variable**: `column = "visibility"` then
  `setattr(obj, column, ...)`. *Narrowly* handled — a computed `setattr` name is
  failed when the owner reads as a clip or a game, since undecidable is not the
  same as safe there — but an owner the guard cannot name still slips.
* **Binding forms other than assignment**: a `for` target
  (`for game.visibility in xs:`) and a `with cm as game.visibility:` target.
* **`obj.__setattr__("visibility", ...)`**, called as a method rather than
  through `setattr`.
* **A module-qualified constructor**, `models.Game(visibility=...)`.
* **SQLAlchemy's ORM bulk update by primary key**:
  `db.execute(update(Game), [{"id": gid, "visibility": ...}])`. The most
  plausible of these in real code, because nothing about it looks unusual.

A guard is not a proof. What it buys is that the *idiomatic* way to write this
column fails loudly in the diff that introduces it.
"""
import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# Matched exactly, which keeps `ClipOut.effective_visibility` out of this
# without an exemption: it is a derived response field and assigning it stores
# nothing.
_COLUMN = "visibility"

# A post's own tier is chosen at publish time and bounded by the clip's. That
# is the feature, and a guard that flagged it would be deleted the first time
# someone hit it, taking the clip and game protection with it.
_POST_OWNERS = {"post", "posts", "Post"}

_CLIP_OWNERS = {"clip", "clips", "Clip"}

# Only used by the two checks that need to know whether an UNDECIDABLE write is
# worth failing over: a computed `setattr` name, and a constructor keyword.
# Everything else is decided by the attribute name alone, which is why the rest
# of this file never asks what kind of thing it is looking at.
_GAME_OWNERS = {"game", "games", "Game"}

# THE ENTIRE EXEMPTION: (path relative to `app/`, enclosing function).
#
# Per FUNCTION, not per file, and that is the difference between a guard and a
# gesture. A file-level exemption would let any clip write anywhere in
# `routers/clips.py` through — the tag endpoint, the trim endpoint, the delete
# endpoint — and those do not gate the tier on PUBLIC_POSTING_ENABLED. Only
# these two functions call `publishing.assert_tier_allowed`, so only these two
# may write.
_DECLARED_CLIP_WRITERS = {
    ("routers/clips.py", "update_clip_visibility"),  # PATCH /clips/{id}/visibility
    ("routers/posts.py", "create_post"),  # POST /posts, raise_clip_visibility
}


def _owner_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _assigned_names(target: ast.AST) -> list[ast.AST]:
    """Every name a single assignment target binds.

    `a.visibility = x` hands back one node; `a.visibility, a.title = x, y` hands
    back two, and `[a.visibility, *rest] = xs` the same. Without this the tuple
    form was invisible: the target is an `ast.Tuple`, not an `ast.Attribute`, so
    the check below skipped it and never looked inside. A review round wrote the
    game-rename exhibit as `game.visibility, game.title = "public", body.title`
    and the suite stayed green -- the exact publish-every-clip-in-the-game
    regression this file exists to make impossible, in the spelling a reviewer
    is least likely to read twice.
    """
    if isinstance(target, (ast.Tuple, ast.List)):
        out: list[ast.AST] = []
        for elt in target.elts:
            out += _assigned_names(elt)
        return out
    if isinstance(target, ast.Starred):
        return _assigned_names(target.value)
    return [target]


def _attribute_writes(tree: ast.AST, *, clip_allowed: bool) -> list[tuple[int, str]]:
    """`<something>.visibility = ...` and `setattr(<something>, "visibility", …)`."""
    found: list[tuple[int, str]] = []

    def judge(owner: str, lineno: int, rendered: str) -> None:
        if owner in _POST_OWNERS:
            return
        if owner in _CLIP_OWNERS and clip_allowed:
            return
        found.append((lineno, rendered))

    targets: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                targets += _assigned_names(target)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets += _assigned_names(node.target)

    for t in targets:
        if not (isinstance(t, ast.Attribute) and t.attr == _COLUMN):
            continue
        owner = _owner_name(t.value)
        judge(owner, t.lineno, f"{owner}.visibility = ...")

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "setattr" or len(node.args) < 2:
            continue
        name = node.args[1]
        owner = _owner_name(node.args[0])
        if isinstance(name, ast.Constant) and name.value == _COLUMN:
            judge(owner, node.lineno, f'setattr({owner}, "visibility", ...)')
        elif owner in _CLIP_OWNERS | _GAME_OWNERS:
            # A computed attribute name on something that looks like a clip or a
            # game. The guard cannot read a variable, so it cannot prove this is
            # not the write it forbids -- and `setattr(clip, column, value)` is
            # both a natural refactor and a complete bypass. Undecidable fails.
            # Narrowed to those owners on purpose: `routers/players.py` already
            # does `setattr(player, k, v)` in a patch loop, which is none of this
            # file's business, and a guard that fires there gets deleted.
            judge(owner, node.lineno, f"setattr({owner}, <computed>, ...)")

    return found


def _constructor_writes(tree: ast.AST, *, clip_allowed: bool) -> list[tuple[int, str]]:
    """`Clip(visibility=…)` / `Game(visibility=…)` — a row born wide.

    Not reachable through an assignment or a `values()` call, so nothing above
    sees it. Today nothing in `app/` passes the column to either constructor
    (`routers/games.py` builds a Game without it, `workers/_sync_db.py` a Clip),
    so this costs nothing and closes the shape where a widening path arrives as
    a new row rather than an edit to one.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id not in _CLIP_OWNERS | _GAME_OWNERS:
            continue
        if not any(kw.arg == _COLUMN for kw in node.keywords):
            continue
        if node.func.id in _CLIP_OWNERS and clip_allowed:
            continue
        found.append((node.lineno, f"{node.func.id}(visibility=...)"))
    return found


def _core_update_writes(tree: ast.AST, *, clip_allowed: bool) -> list[tuple[int, str]]:
    """`update(Clip).values(visibility=…)` — the writer an ast.Assign misses."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "values":
            continue
        if not any(kw.arg == _COLUMN for kw in node.keywords):
            continue
        entity = ""
        for inner in ast.walk(node.func.value):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "update"
                and inner.args
                and isinstance(inner.args[0], ast.Name)
            ):
                entity = inner.args[0].id
        if entity == "Post":
            continue
        if entity == "Clip" and clip_allowed:
            continue
        # An entity this cannot read is flagged: fail closed, because an
        # unrecognised `update(...).values(visibility=…)` is exactly the shape
        # a setter written some other way would take.
        if entity in {"Clip", "Game"} or entity == "":
            found.append((node.lineno, f"update({entity or '?'}).values(visibility=…)"))
    return found


def _enclosing_function(tree: ast.AST) -> list[tuple[int, int, str]]:
    """(start, end, name) for every function, innermost sorted last by span."""
    spans = [
        (node.lineno, node.end_lineno or node.lineno, node.name)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    # Smallest span first, so a nested def wins over the one containing it.
    return sorted(spans, key=lambda s: s[1] - s[0])


def _function_at(spans: list[tuple[int, int, str]], line: int) -> str:
    for start, end, name in spans:
        if start <= line <= end:
            return name
    return ""


def offending_writes(rel: str, tree: ast.AST) -> list[tuple[int, str]]:
    """Every visibility write in `tree` that the exemption does not cover.

    The whole rule, in one function, so the self-tests below exercise the same
    code the scan runs rather than a restatement of it. A guard whose tests
    reimplement its logic cannot catch the logic being loosened.
    """
    spans = _enclosing_function(tree)

    # Scanned twice: once assuming a clip write is allowed, once not. The
    # difference is the set of clip writes, which are then judged by the
    # function they sit in.
    def scan(*, clip_allowed: bool) -> list[tuple[int, str]]:
        return (
            _attribute_writes(tree, clip_allowed=clip_allowed)
            + _core_update_writes(tree, clip_allowed=clip_allowed)
            + _constructor_writes(tree, clip_allowed=clip_allowed)
        )

    strict = scan(clip_allowed=False)
    lenient = scan(clip_allowed=True)
    clip_writes = [w for w in strict if w not in lenient]

    # Game writes and anything unrecognised are banned outright.
    writes = list(lenient)
    for write in clip_writes:
        if (rel, _function_at(spans, write[0])) not in _DECLARED_CLIP_WRITERS:
            writes.append(write)
    return sorted(writes)


@pytest.mark.parametrize(
    "path",
    sorted(APP.rglob("*.py")),
    ids=lambda p: str(p.relative_to(APP)).replace("\\", "/"),
)
def test_only_the_declared_functions_widen_a_clip_and_nothing_widens_a_game(path):
    rel = path.relative_to(APP).as_posix()
    writes = offending_writes(rel, ast.parse(path.read_text(encoding="utf-8")))

    assert not writes, (
        f"{rel} writes a clip's or a game's visibility: {writes}.\n"
        "A GAME write is not allowed anywhere: raising a game publishes every "
        "clip in it, which is the silent side effect create_post's 409 exists "
        "to prevent, and models/visibility.py and render.yaml both state that "
        "no such path exists.\n"
        "A CLIP write is allowed only inside "
        f"{sorted(_DECLARED_CLIP_WRITERS)} — those two functions, not those two "
        "files — because only they gate the tier on PUBLIC_POSTING_ENABLED "
        "(services/publishing.py). A third path would reach `public` with the "
        "flag off.\n"
        "If this is a deliberate new write path, add its (file, function) pair "
        "to _DECLARED_CLIP_WRITERS in the same PR, with a test that the new path "
        "is gated — so the decision is in a diff rather than in a silenced test."
    )


def test_the_declared_writers_actually_write():
    """The exemption must describe reality, or it is a hole nobody is using.

    A function that stops writing visibility and stays on the list leaves an
    exemption open for whatever is added to it next.
    """
    for rel, func in _DECLARED_CLIP_WRITERS:
        tree = ast.parse((APP / rel).read_text(encoding="utf-8"))
        spans = _enclosing_function(tree)
        writes = [
            w
            for w in _attribute_writes(tree, clip_allowed=False)
            if _function_at(spans, w[0]) == func
        ]
        assert writes, (
            f"{rel}::{func} is exempted but no longer writes a clip's "
            "visibility; drop it from _DECLARED_CLIP_WRITERS"
        )


def test_the_guard_reads_the_whole_package_including_services_and_workers():
    """The scope, pinned so it cannot quietly narrow again.

    `services/access.py` is where the visibility ladder lives, so it is where a
    setter would most naturally go — and it is exactly what a routers-only glob
    would miss.
    """
    scanned = {p.relative_to(APP).as_posix() for p in APP.rglob("*.py")}
    assert "services/access.py" in scanned
    assert "services/publishing.py" in scanned
    assert "routers/games.py" in scanned
    assert any(f.startswith("workers/") for f in scanned), (
        "the worker writes game rows too; it must not fall outside the scan"
    )


def test_the_guard_would_notice(tmp_path):
    """Only worth having if it fires. Checked against the three shapes a setter
    would actually take, in a file that is not on the exemption list."""
    setter = tmp_path / "fake_service.py"
    setter.write_text(
        "async def set_vis(clip, game, db):\n"
        "    clip.visibility = 'public'\n"
        "    setattr(game, 'visibility', 'public')\n"
        "    await db.execute(update(Clip).values(visibility='public'))\n",
        encoding="utf-8",
    )
    tree = ast.parse(setter.read_text(encoding="utf-8"))
    hits = _attribute_writes(tree, clip_allowed=False) + _core_update_writes(
        tree, clip_allowed=False
    )
    assert len(hits) == 3, f"the guard missed a setter shape: {hits}"


def test_a_game_write_is_flagged_even_inside_a_declared_clip_writer():
    """The exemption is for clips only.

    `routers/posts.py` and `routers/clips.py` may set a clip's tier; neither
    may set a game's. Without this the two rules would collapse into one the
    moment someone added a game write to a file already on the list — which is
    the likeliest place for it to appear, since that is where visibility is
    already being handled.
    """
    tree = ast.parse(
        "def update_clip_visibility(clip, game):\n"
        "    clip.visibility = 'public'\n"
        "    game.visibility = 'public'\n"
    )
    hits = offending_writes("routers/clips.py", tree)
    assert [h[1] for h in hits] == ["game.visibility = ..."]


def test_the_exemption_is_per_function_not_per_file():
    """Weakening this guard to file granularity must itself fail.

    `routers/clips.py` holds the tag, labels, trim and delete endpoints
    alongside the visibility setter, and none of those four gates the tier on
    PUBLIC_POSTING_ENABLED. A file-level exemption would wave a clip write in
    any of them straight through — which is the shape a third write path would
    most plausibly take, since that file is already where clips are edited.

    Driven through the same helpers the parametrised test uses, so it fails if
    the exemption is loosened rather than only if a real write appears.
    """
    source = (
        "async def update_clip_visibility(clip, body):\n"
        "    clip.visibility = body.visibility\n"
        "\n"
        "async def tag_clip(clip, body):\n"
        "    clip.visibility = 'public'\n"
    )
    tree = ast.parse(source)
    assert len(_attribute_writes(tree, clip_allowed=False)) == 2, (
        "both writes must be seen before the exemption is applied"
    )

    flagged = offending_writes("routers/clips.py", tree)
    assert len(flagged) == 1, (
        "the tag endpoint's write must survive the exemption; a file-level one "
        f"would let it through — got {flagged}"
    )
    assert _function_at(_enclosing_function(tree), flagged[0][0]) == "tag_clip"


def test_a_post_setting_its_own_tier_is_never_flagged():
    tree = ast.parse("def publish(post, body):\n    post.visibility = body.visibility\n")
    assert _attribute_writes(tree, clip_allowed=False) == []


def test_a_write_bound_inside_a_tuple_target_is_still_a_write():
    """The spelling that walked straight past this guard.

    A review round wrote the game-rename exhibit as a two-name assignment and
    the suite stayed green: `ast.Assign.targets` held one `ast.Tuple`, the check
    asked whether that target was an `ast.Attribute`, and it is not. Renaming a
    game would have published every clip in it, through a line no reviewer reads
    twice because the eye goes to the title.

    All three bindings are checked, not just the tuple: a list target and a
    starred one are the same node shape wearing different brackets, and fixing
    only the case the round happened to write is how the next spelling gets in.
    """
    for source in (
        "def rename_game(game, body):\n"
        "    game.visibility, game.title = 'public', body.title\n",
        "def rename_game(game, body):\n"
        "    [game.visibility, game.title] = ['public', body.title]\n",
        "def rename_game(game, body):\n"
        "    game.visibility, *rest = 'public', body.title\n",
    ):
        tree = ast.parse(source)
        hits = offending_writes("routers/games.py", tree)
        assert [h[1] for h in hits] == ["game.visibility = ..."], (
            f"the guard missed a tuple-bound write: {source!r} -> {hits}"
        )


def test_a_computed_setattr_name_on_a_clip_or_game_is_undecidable_and_fails():
    """`setattr(clip, column, value)` is a natural refactor and a total bypass.

    The guard cannot read a variable, so it cannot prove this is not the write
    it forbids. Undecidable fails -- but only where the owner reads as a clip or
    a game, because `routers/players.py` already does `setattr(player, k, v)` in
    an ordinary patch loop, and a guard that fires on that gets deleted, taking
    the real protection with it. Both halves are asserted here; pinning only the
    first is what would make the second regress silently.
    """
    flagged = offending_writes(
        "routers/games.py",
        ast.parse("def patch(game, column, value):\n    setattr(game, column, value)\n"),
    )
    assert [h[1] for h in flagged] == ["setattr(game, <computed>, ...)"]

    ignored = offending_writes(
        "routers/players.py",
        ast.parse("def patch(player, k, v):\n    setattr(player, k, v)\n"),
    )
    assert ignored == [], f"the players patch loop must not be flagged: {ignored}"


def test_a_row_constructed_wide_is_a_write_path_too():
    """A widening path can arrive as a new row rather than an edit to one.

    No assignment, no `values()` call, nothing for the other two checks to see.
    Nothing in `app/` passes the column to either constructor today, so this
    costs nothing now and closes the shape before it is used.
    """
    hits = offending_writes(
        "workers/_sync_db.py",
        ast.parse("def sync(row):\n    clip = Clip(id=row['id'], visibility='public')\n"),
    )
    assert [h[1] for h in hits] == ["Clip(visibility=...)"]

    game = offending_writes(
        "routers/games.py",
        ast.parse("def create(body):\n    game = Game(title=body.title, visibility='public')\n"),
    )
    assert [h[1] for h in game] == ["Game(visibility=...)"]

    # ...and the declared writer may still construct one, or the exemption
    # would be narrower than the rule it implements.
    allowed = offending_writes(
        "routers/posts.py",
        ast.parse("def create_post(body):\n    clip = Clip(visibility=body.visibility)\n"),
    )
    assert allowed == [], f"a declared clip writer may construct one: {allowed}"
