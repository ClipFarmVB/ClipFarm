"""Is the dev set installed? Exit 0 to run api/tests; exit 1 with why on stdout.

Consumed by `.hooks/pre-commit`, which gates its `pytest api/tests` step on
this script: non-zero means the step is skipped (not failed), with this
script's stdout printed as the reason. It lives here rather than inline in the
hook so `ruff check api/` lints it and `api/tests/test_dev_dependencies.py`
tests it — the probe it replaced (`python -c "import fakeredis"`) rotted
silently for exactly the lack of both. Not type-checked, though: `mypy api/app`
covers the app package only, so the annotations below are documentation rather
than an enforced contract (same footing as api/scripts/auto_migrate.py).

What it checks: every `name==version` pin reachable from
api/requirements-dev.txt, following its `-r` include in all three spellings
pip accepts (`-r file`, `-rfile`, `--requirement file`). Following the include
matters — the dev file's own pins are all test-only, so checking just those
would pass on a machine with pytest, PyYAML and numpy but none of the app's
dependencies, and the suite would then die at collection importing fastapi.

Presence is the bar, for every pin: an absent dependency kills collection,
which is the case this gate exists to decline. It deliberately does NOT gate
on version equality — doing so made any unrelated drift in the same
virtualenv (a tool bumping a patch release, numpy resolved elsewhere by other
work) skip api/tests while the hook exited 0, the silent-green shape this gate
replaced. Version consistency across the files that matter is
`test_pipeline_deps_are_pinned_to_one_version_everywhere`'s job, and CI runs
the suite in an environment built exactly from requirements-dev.txt.

Scope, stated precisely: `name==version` lines, with or without an extras
spec (`uvicorn[standard]==0.34.0` counts, as uvicorn) and with or without
whitespace around the `==`. Range constraints and environment markers are
skipped, so adding one of those weakens the gate silently — prefer a plain
pin, or extend the pattern. Worth knowing because it is a live temptation:
numpy 1.26.4 ships no wheels for 3.13, so the obvious
`; python_version < "3.13"` would drop numpy from this check entirely.
Everything here runs on 3.11 (ci.yml, Dockerfile.api), so the plain pin is
correct today.
"""
import pathlib
import re
import sys
from importlib.metadata import PackageNotFoundError, version

API_DIR = pathlib.Path(__file__).resolve().parents[1]

INCLUDE = re.compile(r"(?:-r\s*|--requirement[\s=])\s*(\S+)")
PIN = re.compile(r"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*==\s*([A-Za-z0-9_.\-]+)")


def parse(path: pathlib.Path, seen: set[pathlib.Path]) -> dict[str, str]:
    """The `name==version` pins reachable from `path`, following `-r` includes."""
    if path in seen:
        return {}
    seen.add(path)
    own: dict[str, str] = {}
    inherited: dict[str, str] = {}
    if not path.is_file():
        # An include that does not exist is a broken requirements set, not a
        # crash: report it as the reason rather than letting a FileNotFoundError
        # out, which would leave the reason empty and print a traceback.
        print(f"missing requirements file: {path}")
        raise SystemExit(1)
    # Explicit encoding: both requirements files contain em dashes, and under a
    # strict-ASCII locale the default would raise here, degrading a correct dev
    # install to the generic skip. The tests reading these files say utf-8 too.
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        # All three include spellings pip accepts. Handling only `-r <file>`
        # would drop `-rfile` and `--requirement file` silently, and dropping
        # an include is how this gate stopped checking app dependencies in the
        # first place.
        inc = INCLUDE.fullmatch(line)
        if inc:
            inherited.update(parse((path.parent / inc.group(1)).resolve(), seen))
            continue
        m = PIN.fullmatch(line)
        if m:
            own[m.group(1)] = m.group(2)
    # A file's own pins beat what it includes, wherever the `-r` line sits.
    # Merging in line order let an include placed below a local pin override it,
    # inverting the precedence this file's comments describe.
    return {**inherited, **own}


def installed(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def main() -> int:
    pins = parse(API_DIR / "requirements-dev.txt", set())

    # No pins parsed means the file moved or changed shape — report the set as
    # absent rather than running the suite against who knows what.
    if not pins:
        print("no pins parsed from api/requirements-dev.txt")
        return 1

    missing = [f"{n}=={v}" for n, v in sorted(pins.items()) if installed(n) is None]
    if missing:
        # Name them: "run the install you already ran" is a useless thing to be
        # told. Capped, because on a machine with no dev install at all this is
        # every pin in the set — twenty names is a wall, and the first few plus
        # a count say the same thing.
        shown = ", ".join(missing[:4])
        if len(missing) > 4:
            shown += f", and {len(missing) - 4} more"
        print("missing: " + shown)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
