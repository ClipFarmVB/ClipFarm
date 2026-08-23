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

    assert _default(str(dev["mem_limit"])) == memory, (
        f"clipfarm-worker is on `{plan}` ({memory}) but the dev worker's mem_limit "
        "defaults to something else — move them together (docker-compose.yml)"
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
        v["value"] for v in prod["envVars"] if v.get("key") == "FFMPEG_THREADS"
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
