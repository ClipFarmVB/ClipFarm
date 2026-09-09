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
* **`Clip.visibility`: written only in the files named below.** The tier is
  gated on `PUBLIC_POSTING_ENABLED` in both of them (`services/publishing.py`);
  a third path would reach `public` with the flag off and nothing would notice.
* `Post.visibility` is the feature and was always exempt.

`_DECLARED_CLIP_WRITERS` is the whole exemption. Adding to it is a real
decision — the same one this file's predecessor existed to force — and the
right way to make it is to add the file here, in a diff, alongside a test that
the new path is gated.

The scope is the whole `app/` package, not `routers/`, for the reason the
original gave: CF-109 put the visibility ladder in `services/access.py`, so a
setter is *more* likely to be written as a service helper than as router-local
code. A guard that misses the idiomatic home of the thing it guards is worse
than none, because the green check is what stops anyone looking.

**What it can and cannot see.** It reads the AST for attribute writes,
`setattr`, and Core `update(...).values(visibility=...)`. Raw SQL in a string
would still get past, which is visible in review in a way an ordinary
`clip.visibility = ...` is not.
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
            targets += node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets.append(node.target)

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
        if isinstance(name, ast.Constant) and name.value == _COLUMN:
            owner = _owner_name(node.args[0])
            judge(owner, node.lineno, f'setattr({owner}, "visibility", ...)')

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
    strict = _attribute_writes(tree, clip_allowed=False) + _core_update_writes(
        tree, clip_allowed=False
    )
    lenient = _attribute_writes(tree, clip_allowed=True) + _core_update_writes(
        tree, clip_allowed=True
    )
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
        "If this is a deliberate new write path, add the file to "
        "_DECLARED_CLIP_WRITERS in the same PR, with a test that the new path "
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
