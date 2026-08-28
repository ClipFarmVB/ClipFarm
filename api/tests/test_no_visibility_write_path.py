"""The precondition `render.yaml` turns SOCIAL_ENABLED on against (CF-106/109).

Production runs the social surface with CF-186 (rate limiting, #189) and CF-116
(abuse/moderation) still open. The argument for doing that is one sentence:
**nothing user-generated can be public.** Games and clips both default to
`private`, no endpoint can widen either, so a post can only ever be private,
`create_post` refuses wider tiers regardless, and the anonymous read endpoints
reach the database and 404 — a load question rather than a disclosure one.

That argument is sound today. What it was not, until this file, is *enforced*.
It lived in a comment in `render.yaml` and a paragraph in `services/access.py`,
and it stops being true the moment someone adds a visibility setter — which is
a normal-looking feature PR, plausibly written by someone who never opens
either file. `access.py`'s own docstring says as much: "that last sentence
expires with CF-109". The failure mode is not a bug in the new endpoint; it is
that shipping it silently converts four unthrottled anonymous routes from
"always 404" into "serves real footage", `/clips/{id}/download` included.

So the constraint is a test. It fails the day a write path appears, which is
the day rate limiting stops being a parallel task and becomes a blocker — and
it fails in the PR that adds it, in front of the person who can weigh that,
rather than in production.

**Deleting this test is a legitimate thing to do.** It is not a claim that a
visibility setter is wrong; it is a claim that landing one is a decision about
CF-186's ordering. Whoever makes that decision deletes this file in the same
PR, and the diff is where the argument gets recorded.

**What it can and cannot see.** It reads the routers' AST for writes to a Clip
or Game `visibility` attribute and for Core `update()` statements naming the
column. It would not catch a write funnelled through a helper in `services/`
that the routers merely call, nor raw SQL in a string. Both are visible in
review in a way an ordinary `clip.visibility = ...` is not, and a guard that
catches the shape people actually write is worth more than none.
"""
import ast
import pathlib

import pytest

ROUTERS = pathlib.Path(__file__).resolve().parents[1] / "app" / "routers"

# The response-shaping attribute CF-109 added to ClipOut. It is derived from the
# clip and the game and sent to clients; assigning it changes no stored row.
_DERIVED = {"effective_visibility"}

# Objects whose own visibility a router may legitimately set. A post's tier is
# chosen at publish time and bounded by the clip's — that is the feature.
_WRITABLE_OWNERS = {"post", "posts", "Post"}


def _owner_name(node: ast.AST) -> str:
    """Best-effort name of whatever is being written to."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _attribute_writes(tree: ast.AST) -> list[tuple[int, str]]:
    """`<something>.visibility = ...` and `setattr(<something>, "visibility", …)`."""
    found: list[tuple[int, str]] = []

    targets: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets += node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets.append(node.target)

    for t in targets:
        if not (isinstance(t, ast.Attribute) and t.attr == "visibility"):
            continue
        if t.attr in _DERIVED:
            continue
        owner = _owner_name(t.value)
        if owner in _WRITABLE_OWNERS:
            continue
        found.append((t.lineno, f"{owner}.visibility = ..."))

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "setattr" or len(node.args) < 2:
            continue
        name = node.args[1]
        if isinstance(name, ast.Constant) and name.value == "visibility":
            owner = _owner_name(node.args[0])
            if owner not in _WRITABLE_OWNERS:
                found.append((node.lineno, f'setattr({owner}, "visibility", ...)'))

    return found


def _core_update_writes(tree: ast.AST) -> list[tuple[int, str]]:
    """`update(Clip).values(visibility=…)` — the writer an ast.Assign misses."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "values":
            continue
        if not any(kw.arg == "visibility" for kw in node.keywords):
            continue
        # Which entity? Walk back down the chain for update(<Entity>).
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
        if entity in {"Clip", "Game"} or entity == "":
            found.append((node.lineno, f"update({entity or '?'}).values(visibility=…)"))
    return found


@pytest.mark.parametrize("path", sorted(ROUTERS.glob("*.py")), ids=lambda p: p.name)
def test_no_router_widens_a_clip_or_a_game(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    writes = _attribute_writes(tree) + _core_update_writes(tree)

    assert not writes, (
        f"{path.name} writes a clip's or a game's visibility: {writes}. That is "
        "the change render.yaml's SOCIAL_ENABLED comment names as the one which "
        "makes 'public' reachable — and with it the unthrottled anonymous read "
        "endpoints, /clips/{id}/download among them. CF-186 (#189, rate "
        "limiting) has to land first. If it has, or the ordering has been "
        "reconsidered, delete this file in the same PR so the decision is in "
        "the diff rather than in a silenced test."
    )


def test_the_guard_would_notice(tmp_path):
    """The guard is only worth having if it fires. Asserted, not assumed.

    A structural test that quietly stops matching is indistinguishable from a
    codebase that stays clean, and this one is guarding a production exposure —
    so it checks itself against the three shapes a visibility setter would
    actually take.
    """
    setter = tmp_path / "fake_router.py"
    setter.write_text(
        "async def set_vis(clip, game, db):\n"
        "    clip.visibility = 'public'\n"
        "    setattr(game, 'visibility', 'public')\n"
        "    await db.execute(update(Clip).values(visibility='public'))\n",
        encoding="utf-8",
    )
    tree = ast.parse(setter.read_text(encoding="utf-8"))

    hits = _attribute_writes(tree) + _core_update_writes(tree)
    assert len(hits) == 3, f"the guard missed a setter shape: {hits}"


def test_a_post_setting_its_own_tier_is_not_flagged():
    """The exemption has to be real, or the guard is a blanket ban on the feature.

    Publishing chooses the post's tier — bounded by the clip's, which is what
    `create_post`'s 409 enforces. A guard that flagged this would be deleted the
    first time someone hit it, taking the clip and game protection with it.
    """
    tree = ast.parse("def publish(post, body):\n    post.visibility = body.visibility\n")
    assert _attribute_writes(tree) == []
