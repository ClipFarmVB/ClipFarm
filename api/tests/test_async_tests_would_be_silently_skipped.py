"""No test in this suite may be an `async def`, because one would pass without running.

Found while writing CF-186's tests (#189). This suite installs no
pytest-asyncio, no anyio, no trio plugin — check `requirements-dev.txt`. Given
an `async def test_...`, pytest collects it, declines to run it, emits a
`PytestUnhandledCoroutineWarning`, and reports the file GREEN. Fourteen tests
of the new rate limiter were written that way and reported "5 passed, 14
skipped" — which reads like an ordinary skip and is not: the assertions never
executed.

Nothing in the repo caught it. There were no async test functions in
`api/tests/` before this card, so the trap was latent rather than broken, and
the first person to reach for the obvious shape would have shipped tests that
assert nothing. `services/access.py` and `routers/` are full of coroutines, so
that person arrives sooner or later.

The house pattern is `asyncio.run(...)` inside a plain `def` —
`test_clip_download.py` and `test_ratelimit.py` both do it. This guard says so
in the failure message rather than leaving the next person to rediscover the
warning in the noise.

**Adding an async plugin in auto mode is the other legitimate fix**, and it
deletes this file: pin pytest-asyncio in `requirements-dev.txt`, set
`asyncio_mode = auto`, and the shape becomes safe. Note that installing the
plugin alone would NOT be enough — its default strict mode still wants a
`@pytest.mark.asyncio` on every test, so an unmarked one goes on silently
skipping. That is a real decision, and it is not this card's to make.
"""
import ast
import pathlib

import pytest

TESTS = pathlib.Path(__file__).resolve().parent

# What would actually make a bare `async def test_` run is an async plugin in an
# AUTO mode — pytest-asyncio's `asyncio_mode = auto`, or trio/tornasync's
# equivalents. Mere installation is not enough and neither is anyio: anyio ships
# with starlette and is therefore always importable here, but its pytest plugin
# only runs tests explicitly marked `@pytest.mark.anyio`, and pytest-asyncio's
# default strict mode likewise wants `@pytest.mark.asyncio`. So the thing to
# detect is configuration, not a package.
AUTO_MODE_KEYS = ("asyncio_mode", "trio_mode")
CONFIG_FILES = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")


def _async_test_functions(tree: ast.AST) -> list[str]:
    """Only functions pytest would collect — a nested `async def` is fine."""
    return [
        f"{node.name} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_")
    ]


def _auto_mode_configured() -> str | None:
    root = TESTS.parent
    for candidate in CONFIG_FILES:
        for path in (root / candidate, root.parent / candidate):
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for key in AUTO_MODE_KEYS:
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith(key) and "auto" in stripped:
                        return f"{path.name}: {stripped}"
    return None


def test_no_auto_async_mode_is_configured_so_the_guard_below_is_needed():
    """Fails loudly the day someone enables one, rather than leaving a dead guard.

    A guard nobody can trip is worse than no guard: it keeps passing while the
    reason for it has gone, and the next reader treats the shape it forbids as
    forbidden on principle.
    """
    configured = _auto_mode_configured()
    assert configured is None, (
        f"an auto async mode is configured ({configured}), so `async def test_` "
        "now runs properly. Delete this file — its whole premise is that nothing "
        "runs them."
    )


@pytest.mark.parametrize(
    "path", sorted(TESTS.glob("test_*.py")), ids=lambda p: p.name
)
def test_a_test_file_declares_no_async_test_functions(path):
    offenders = _async_test_functions(ast.parse(path.read_text(encoding="utf-8")))
    assert not offenders, (
        f"{path.name} defines async test functions: {', '.join(offenders)}.\n"
        "This suite has no async plugin, so pytest SKIPS them and still reports "
        "the file as passing — the assertions never run.\n"
        "Use a plain `def` and drive the coroutine with `asyncio.run(...)`, as "
        "test_clip_download.py and test_ratelimit.py do."
    )
