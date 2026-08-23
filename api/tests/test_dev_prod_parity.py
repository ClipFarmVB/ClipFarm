"""The dev worker must be the production worker's size (CF-241).

CF-224 shipped an OOM that could not be reproduced locally: the container had
the whole host, so x264 reading 16 cores and using 16 cores was *correct* here
and fatal on a 0.5-CPU Render instance. `docker-compose.yml` now pins the dev
worker to whatever `render.yaml` gives the production one — and a comment
saying so is exactly the kind of thing that rots, so it is a test instead.

These read the two files as text/YAML rather than starting anything; there is
no Docker in CI.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Render plan → (memory, CPUs). Only the plans this repo actually uses; an
# unknown one should fail loudly rather than silently skip the check.
RENDER_PLANS = {
    "starter": ("512m", "0.5"),
    "standard": ("2g", "1.0"),
    "pro": ("4g", "2.0"),
}


# Every Compose field that caps a container's resources. The dev worker sets
# some of these; the VPS overlay must clear whichever it sets. Adding a new one
# to docker-compose.yml means adding it here, which is the point — the list is
# what makes "did you reset it" answerable rather than remembered.
RESOURCE_KEYS = (
    "mem_limit",
    "memswap_limit",
    "mem_reservation",
    "mem_swappiness",
    "cpus",
    "cpu_count",
    "cpu_percent",
    "cpu_shares",
    "cpu_period",
    "cpu_quota",
    "cpuset",
    "pids_limit",
    "shm_size",
    "oom_kill_disable",
    "blkio_config",
    "ulimits",
    "deploy",
)


class _Reset:
    """A `!reset` tag, kept distinct from a plain `null`.

    They parse to the same value and mean different things: `!reset` clears the
    base file's value, a bare null does not.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "!reset"


def _load_compose(path: Path):
    """Parse a Compose file, keeping its `!override` / `!reset` merge tags.

    `yaml.safe_load` refuses unknown tags outright, and the passthrough in
    test_pose_modal.py drops which tag was used — here that distinction is the
    whole assertion.
    """
    yaml = pytest.importorskip("yaml")

    class ComposeLoader(yaml.SafeLoader):
        pass

    def passthrough(loader, tag_suffix, node):
        if tag_suffix == "reset":
            return _Reset()
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node, deep=True)
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node, deep=True)
        return loader.construct_scalar(node)

    ComposeLoader.add_multi_constructor("!", passthrough)
    return yaml.load(path.read_text(encoding="utf-8"), Loader=ComposeLoader)


def _render_worker():
    yaml = pytest.importorskip("yaml")

    render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    worker = next(
        (s for s in render["services"] if s.get("name") == "clipfarm-worker"), None
    )
    assert worker is not None, (
        "render.yaml has no service named `clipfarm-worker` — it was renamed or "
        "removed, and every parity check in this file keys off that name"
    )
    return worker


def _worker_services():
    prod = _render_worker()
    compose = _load_compose(REPO_ROOT / "docker-compose.yml")
    return prod, compose["services"]["worker"]


# Compose accepts `2g`, `2gb`, `2048m`, and a bare byte count.
_UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3}


def _bytes(size: str) -> int:
    """`2g` / `2gb` / `2048m` / `2147483648` -> bytes.

    Compared as a size rather than a string on purpose: `2048m` is the same
    ceiling as `2g`, and a test that fails on the spelling teaches people to
    match the spelling instead of the size.
    """
    size = size.strip().lower()
    if size.endswith("b") and len(size) > 1 and size[-2] in _UNITS:
        size = size[:-1]          # `2gb` -> `2g`; a bare `1024b` keeps its digits
    if size and size[-1] in _UNITS:
        return int(float(size[:-1]) * _UNITS[size[-1]])
    return int(size.rstrip("b"))


def _default(expr: str) -> str:
    """`${NAME:-value}` → `value`. The default is the parity claim; the override
    is the escape hatch (reproducing a *smaller* box on purpose), so it is the
    default this checks.
    """
    assert expr.startswith("${") and ":-" in expr and expr.endswith("}"), (
        f"expected an overridable `${{NAME:-default}}` in docker-compose.yml, got {expr!r} "
        "— a hardcoded value cannot be lowered to reproduce a smaller instance"
    )
    return expr[: -1].split(":-", 1)[1]


def test_dev_worker_memory_and_cpu_track_the_render_plan():
    prod, dev = _worker_services()

    assert "plan" in prod, (
        "render.yaml no longer gives clipfarm-worker a `plan` — Render then picks "
        "its own default and the dev worker has nothing to track (CF-241)"
    )
    plan = prod["plan"]
    assert plan in RENDER_PLANS, (
        f"render.yaml puts clipfarm-worker on an unmapped plan `{plan}` — add its "
        "memory/CPU to RENDER_PLANS here so the dev worker can track it"
    )
    memory, cpus = RENDER_PLANS[plan]

    assert _bytes(_default(str(dev["mem_limit"]))) == _bytes(memory), (
        f"clipfarm-worker is on `{plan}` ({memory}) but the dev worker's mem_limit "
        "defaults to a different size — move them together (docker-compose.yml)"
    )
    assert "memswap_limit" in dev, (
        "the dev worker sets mem_limit without memswap_limit, so Docker allows it "
        f"{memory} of memory PLUS {memory} of swap. Render has no swap, so the OOM "
        "this environment exists to reproduce would not fire here (docker-compose.yml)"
    )
    assert str(dev["memswap_limit"]) == str(dev["mem_limit"]), (
        "memswap_limit and mem_limit must be the *same expression*, not merely the "
        "same default — that is how you say `no swap` to Docker, and it has to hold "
        "under override too. Split onto separate variables, `WORKER_MEM_LIMIT=8g` "
        "asks for 8 GB of memory inside a 2 GB memory+swap ceiling and Docker "
        "refuses to create the container (docker-compose.yml)"
    )
    assert float(_default(str(dev["cpus"]))) == float(cpus), (
        f"clipfarm-worker is on `{plan}` ({cpus} CPU) but the dev worker's cpus "
        "default differs — move them together (docker-compose.yml)"
    )


def test_dev_worker_ffmpeg_threads_match_production():
    """The limits above are worth little if the process that OOMs under them is
    configured differently. FFMPEG_THREADS is the one setting CF-224 turned on.
    """
    prod, dev = _worker_services()

    prod_threads = next(
        (v["value"] for v in prod["envVars"] if v.get("key") == "FFMPEG_THREADS"),
        None,
    )
    assert prod_threads is not None, (
        "render.yaml no longer sets FFMPEG_THREADS on clipfarm-worker, so production "
        "falls back to the code default (2) while the dev worker still pins a number. "
        "Set it there again, or drop the pin here and retire this test — CF-224's "
        "whole point is that the value must be chosen, not inherited"
    )
    dev_threads = _default(str(dev["environment"]["FFMPEG_THREADS"]))

    assert dev_threads == prod_threads, (
        f"production runs FFMPEG_THREADS={prod_threads} and the dev worker defaults "
        f"to {dev_threads} — parity is the point (CF-241); raise it per-run with the "
        "env var, or use the unconstrained `eval` service"
    )


def test_eval_service_is_unconstrained_and_not_started_by_default():
    """CF-241's known tension, resolved: correctness sweeps get the whole host,
    but only on request, so nobody benchmarks at one core by accident.
    """
    compose = _load_compose(REPO_ROOT / "docker-compose.yml")
    evaluation = compose["services"]["eval"]

    constrained = [k for k in RESOURCE_KEYS if k in evaluation]
    assert not constrained, (
        f"the eval service sets {', '.join(constrained)}; it exists precisely to run "
        "unconstrained (CF-241) — if a sweep needs production's limits, run it on "
        "`worker` instead. A stray memswap_limit without mem_limit is worse still: "
        "Docker refuses to create the container"
    )
    assert "eval" in evaluation.get("profiles", []), (
        "eval must stay profile-gated so `docker compose up` does not start a "
        "second, unlimited copy of the worker image alongside the real one"
    )
    assert "FFMPEG_THREADS" not in evaluation.get("environment", {}), (
        "eval pins FFMPEG_THREADS, which nothing on any eval path reads: "
        "Settings.ffmpeg_threads reaches only recut_clip_task and "
        "process_game_task, and the eval entry points stop at _track_ball_cached. "
        "Config that looks load-bearing and is not is worse than none. If eval "
        "grows an encode, give it its own variable rather than the worker's"
    )
    assert evaluation.get("command"), (
        "eval needs an explicit command: without one it inherits Dockerfile.api's "
        "CMD, so a bare `run eval` starts a second uvicorn instead of failing"
    )

    reachable = evaluation.get("environment") or {}
    assert not {"CELERY_BROKER_URL", "REDIS_URL"} & set(reachable), (
        "eval must not carry the broker URLs: with them, `run --rm eval celery ...` "
        "drains the real queue and runs those games unconstrained. Nothing on an "
        "eval path reads them, and without them Settings falls back to localhost "
        "inside a container with no redis — impossible beats discouraged"
    )

    worker = compose["services"]["worker"]
    assert evaluation.get("image") and evaluation["image"] == worker.get("image"), (
        "eval and worker must name the same `image:`. Sharing only `build:` makes "
        "Compose tag them separately and build them independently, from whatever "
        "the tree held at each build — two images that merely look alike in "
        "docker-compose.yml. `eval` is only a fair stand-in for the worker while "
        "it is literally the same image"
    )


def test_the_vps_overlay_clears_every_dev_resource_limit():
    """The dev worker's limits must not reach the VPS — the bug this branch
    shipped and had to fix.

    `docker-compose.prod.yml` layers over the dev file, and Compose carries any
    field the overlay does not mention. The VPS is a 2-3 vCPU / 2-4 GB box
    running redis, api and worker together (DEPLOY.md), so inheriting Render's
    2 GB / 1 CPU caps holds the whole backend to one core. Asserted rather than
    left to the overlay's comment, because the failure is silent: it looks like
    a slow box, not a misconfiguration.
    """
    dev = _load_compose(REPO_ROOT / "docker-compose.yml")["services"]["worker"]
    vps = _load_compose(REPO_ROOT / "docker-compose.prod.yml")["services"]["worker"]

    for key in RESOURCE_KEYS:
        if key not in dev:
            continue
        assert isinstance(vps.get(key), _Reset), (
            f"docker-compose.yml caps the dev worker's `{key}` and "
            "docker-compose.prod.yml does not clear it, so the VPS inherits it. "
            f"Add `{key}: !reset null` to the prod overlay's worker — a plain null "
            "will not do it, only the tag clears a base value"
        )


def test_the_vps_overlay_pins_its_own_ffmpeg_threads():
    """The dev default is Render's 1. The VPS is a different box and was running
    4 before CF-241, so the overlay has to say so or the dev pin silently
    retunes it.
    """
    vps = _load_compose(REPO_ROOT / "docker-compose.prod.yml")["services"]["worker"]
    env = vps.get("environment") or {}

    assert "FFMPEG_THREADS" in env, (
        "docker-compose.prod.yml must pin FFMPEG_THREADS: the dev file now defaults "
        "it to Render's 1, and the VPS would inherit that silently"
    )
    dev_threads = _default(str(_worker_services()[1]["environment"]["FFMPEG_THREADS"]))
    assert _default(str(env["FFMPEG_THREADS"])) != dev_threads, (
        "the VPS pin matches the dev worker's, which makes it indistinguishable from "
        "having inherited it — if they really should be equal, say so here"
    )
