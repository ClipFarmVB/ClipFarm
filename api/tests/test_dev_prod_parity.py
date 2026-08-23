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


def _worker_services():
    yaml = pytest.importorskip("yaml")

    render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    prod = next(s for s in render["services"] if s["name"] == "clipfarm-worker")

    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    return prod, compose["services"]["worker"]


_UNITS = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def _bytes(size: str) -> int:
    """`2g` / `2048m` / `2147483648` -> bytes.

    Compared as a size rather than a string on purpose: `2048m` is the same
    ceiling as `2g`, and a test that fails on the spelling teaches people to
    match the spelling instead of the size.
    """
    size = size.strip().lower().rstrip("b") or "0"
    if size[-1] in _UNITS:
        return int(float(size[:-1]) * _UNITS[size[-1]])
    return int(size)


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
    assert _bytes(_default(str(dev["memswap_limit"]))) == _bytes(
        _default(str(dev["mem_limit"]))
    ), (
        "memswap_limit must equal mem_limit — that is how you say `no swap` to "
        "Docker. Anything larger hands the container swap production does not have"
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
    yaml = pytest.importorskip("yaml")

    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    evaluation = compose["services"]["eval"]

    assert "mem_limit" not in evaluation and "cpus" not in evaluation, (
        "the eval service exists precisely to run unconstrained (CF-241) — if it "
        "needs production's limits, run the sweep on `worker` instead"
    )
    assert "eval" in evaluation.get("profiles", []), (
        "eval must stay profile-gated so `docker compose up` does not start a "
        "second, unlimited copy of the worker image alongside the real one"
    )
    assert "FFMPEG_THREADS" not in str(
        evaluation["environment"]["FFMPEG_THREADS"]
    ).replace("EVAL_FFMPEG_THREADS", ""), (
        "eval must read its own EVAL_FFMPEG_THREADS, not the worker's "
        "FFMPEG_THREADS — sharing the variable means retuning the worker silently "
        "retunes eval, which is the mis-benchmark this service exists to prevent"
    )
    assert evaluation.get("command"), (
        "eval needs an explicit command: without one it inherits Dockerfile.api's "
        "CMD, so a bare `run eval` starts a second uvicorn instead of failing"
    )
