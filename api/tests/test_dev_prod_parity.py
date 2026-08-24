"""The dev worker must be the production worker's size (CF-241).

CF-224 shipped an OOM that could not be reproduced locally: the container had
the whole host, so x264 reading 16 cores and using 16 cores was *correct* here
and fatal on a 0.5-CPU Render instance. `docker-compose.yml` now pins the dev
worker to whatever `render.yaml` gives the production one — and a comment
saying so is exactly the kind of thing that rots, so it is a test instead.

These read the two files as text/YAML rather than starting anything; there is
no Docker in CI.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Render plan → (memory, CPUs), from https://render.com/pricing, checked
# 2026-08-24. Only the plans this repo actually uses; an unknown one fails
# loudly rather than silently skipping the check.
#
# This table is a copy of a vendor's spec sheet, which is the one kind of drift
# this file cannot detect: if Render redefines `standard`, every assertion below
# stays green while the dev worker stops matching the real box. Nothing here can
# fix that — re-read the pricing page when a plan changes, or when a local run
# and a production run disagree about memory for no reason either can explain.
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
)

# `deploy` is deliberately NOT in that tuple. It is a container for unrelated
# things — `replicas`, `restart_policy`, `labels` — and only `deploy.resources`
# caps anything, so treating the whole key as a limit would report "the VPS
# inherits a resource limit" at a worker that had merely gained a restart
# policy. Checked separately, by the one sub-key that means what RESOURCE_KEYS
# means.
DEPLOY_RESOURCES = ("deploy", "resources")


def _has_deploy_resources(service) -> bool:
    """Walk DEPLOY_RESOURCES rather than hardcoding the path, so the tuple above
    is the definition and not a comment that happens to agree with the code."""
    node = service
    for key in DEPLOY_RESOURCES:
        if not isinstance(node, dict):
            return False
        node = node.get(key)
    return bool(node)


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


def _env_map(service) -> dict[str, str]:
    """A service's `environment:` as a dict, whichever syntax it was written in.

    Compose takes either a mapping or a `KEY=value` list and treats them as the
    same thing. A membership test against the raw YAML does not: given the list
    form, `"REDIS_URL" in env` compares against `"REDIS_URL=redis://..."` and is
    False, so every guard below would pass while Compose resolved exactly the
    configuration they forbid (verified against `compose config`). The guards are
    the only thing making "eval cannot take the queue" true rather than merely
    intended, so they must not depend on which of two equivalent spellings
    someone reached for.
    """
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    # List form: `KEY=value`, or a bare `KEY` meaning "pass through from the host"
    # — which is still the key being set, so it counts for these assertions.
    return dict(
        (item.split("=", 1) + [""])[:2] if isinstance(item, str) else (str(item), "")
        for item in env
    )


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


def _compose_service(path: Path, name: str):
    """One service out of a Compose file, saying which file and which name when
    it is not there — a bare KeyError names neither."""
    services = _load_compose(path).get("services") or {}
    assert name in services, (
        f"{path.name} has no `{name}` service. Every parity check in this file keys "
        "off that name; if it was renamed, rename it here too rather than deleting "
        "the check"
    )
    return services[name]


def _interpolated_names(value: str) -> list[str]:
    """Every variable Compose would substitute into `value`.

    Both spellings, because Compose takes both: `${VAR}`, with or without a
    `:-default`, and a bare `$VAR`. Matching only the braced form is how this
    guard first shipped, and `FFMPEG_THREADS: "$WORKER_FFMPEG_THREADS"` slid
    straight past it into the production overlay.

    `$$` is Compose's escape for a literal dollar, so it is removed before the
    scan rather than read as the start of a name.
    """
    return re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", value.replace("$$", ""))


def _assert_sets(filename: str, service, keys) -> None:
    """Presence, before anything indexes these.

    Without it a deleted `mem_limit` reports `KeyError: 'mem_limit'` and the
    message written for exactly that case never prints — the failure mode
    `_compose_service` and `_render_worker` exist to remove.
    """
    for key in keys:
        assert key in service, (
            f"{filename} no longer sets `{key}` on the worker, so the overlay "
            "silently inherits the base file's value and stops being the profile "
            "it exists to be"
        )


def _assert_no_interpolation(filename: str, service, prefix: str | None = None) -> None:
    """An override overlay may not read variables another file also reads.

    Every documented command passes `--env-file .env.docker`, which makes that
    file an interpolation source — so a `WORKER_MEM_LIMIT` parked there for some
    earlier experiment reaches any overlay that reads the same name.

    Two strictnesses, because the two overlays are different kinds of thing.
    `prefix=None` forbids interpolation outright: docker-compose.repro.yml's
    numbers *are* the measurement, and a resized repro passes, which gets read
    as "already fixed". A `prefix` instead requires every variable to be that
    overlay's own — docker-compose.fast.yml needs a real escape hatch, because
    `cpus: 4` cannot be created on an engine with two, and nothing may be
    claimed from a fast run anyway.
    """
    # RESOURCE_KEYS, not a hand-list: an overlay reopens this hole on whichever
    # field it interpolates, so the guard has to cover every field that caps
    # anything, not the three we happen to use today. `str()` on the whole node
    # is what makes the nested ones (`ulimits`, `blkio_config`,
    # `deploy.resources`) count — a `${...}` anywhere inside stringifies too.
    fields = {k: service.get(k) for k in RESOURCE_KEYS if k in service}
    if "deploy" in service:
        fields["deploy"] = service["deploy"]
    fields.update(_env_map(service))
    for key, value in fields.items():
        names = _interpolated_names(str(value))
        if prefix is None:
            assert not names, (
                f"{filename} interpolates `{key}`: {value!r}. Every documented "
                "command passes --env-file .env.docker, so a value set there would "
                "silently redefine this overlay and the run would prove nothing. "
                "Hardcode it, and use the base file's knobs for a one-off size"
            )
            continue
        stray = [n for n in names if not n.startswith(prefix)]
        assert not stray, (
            f"{filename} reads {', '.join(stray)} for `{key}`. Names this overlay "
            f"does not own reach it from .env.docker; use {prefix}* so only a "
            "deliberate override of this file changes it"
        )


def _worker_services():
    prod = _render_worker()
    dev = _compose_service(REPO_ROOT / "docker-compose.yml", "worker")
    return prod, dev


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

    for key in ("mem_limit", "cpus"):
        assert key in dev, (
            f"the dev worker no longer sets `{key}`, so it runs on the whole host "
            "and cannot reproduce a production OOM — which is the entire point of "
            "CF-241 (docker-compose.yml)"
        )
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

    assert "envVars" in prod, (
        "render.yaml's clipfarm-worker has no envVars block at all — FFMPEG_THREADS "
        "cannot be pinned there, and CF-224's whole point is that the value must be "
        "chosen rather than inherited from the code default"
    )
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
    dev_env = _env_map(dev)
    assert "FFMPEG_THREADS" in dev_env, (
        "the dev worker no longer pins FFMPEG_THREADS, so it falls back to the code "
        "default (2) while production pins its own — the divergence CF-241 exists "
        "to close (docker-compose.yml)"
    )
    dev_threads = _default(dev_env["FFMPEG_THREADS"])
    # str() on both sides: render.yaml quotes its value today, but YAML would
    # hand back an int the moment someone unquotes it, and `"1" == 1` is False.
    # The test would then fail with a message reading `1 != 1`.
    prod_threads = str(prod_threads)

    assert dev_threads == prod_threads, (
        f"production runs FFMPEG_THREADS={prod_threads} and the dev worker defaults "
        f"to {dev_threads} — parity is the point (CF-241); raise it per-run with the "
        "env var, or use the unconstrained `eval` service"
    )


def test_eval_service_is_unconstrained_and_not_started_by_default():
    """CF-241's known tension, resolved: correctness sweeps get the whole host,
    but only on request, so nobody benchmarks at one core by accident.
    """
    evaluation = _compose_service(REPO_ROOT / "docker-compose.yml", "eval")

    constrained = [k for k in RESOURCE_KEYS if k in evaluation]
    if _has_deploy_resources(evaluation):
        constrained.append("deploy.resources")
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
    reachable = _env_map(evaluation)
    assert "FFMPEG_THREADS" not in reachable, (
        "eval pins FFMPEG_THREADS, which nothing on any eval path reads: "
        "Settings.ffmpeg_threads reaches only recut_clip_task and "
        "process_game_task, whose callers are both in tasks.py, and nothing "
        "under ml/eval imports ml.pipeline.clip. Config that looks load-bearing "
        "and is not is worse than none. If eval grows an encode of its own, give "
        "it its own variable rather than the worker's"
    )
    assert evaluation.get("command"), (
        "eval needs an explicit command: without one it inherits Dockerfile.api's "
        "CMD, so a bare `run eval` starts a second uvicorn instead of failing"
    )

    assert not {"CELERY_BROKER_URL", "REDIS_URL"} & set(reachable), (
        "eval must not carry the broker URLs: with them, `run --rm eval celery ...` "
        "drains the real queue and runs those games unconstrained. Nothing on an "
        "eval path reads them, and without them Settings falls back to localhost "
        "inside a container with no redis — impossible beats discouraged"
    )

    worker = _compose_service(REPO_ROOT / "docker-compose.yml", "worker")
    assert evaluation.get("image") and evaluation["image"] == worker.get("image"), (
        "eval and worker must name the same `image:`. Sharing only `build:` makes "
        "Compose tag them separately and build them independently, from whatever "
        "the tree held at each build — two images that merely look alike in "
        "docker-compose.yml. `eval` is only a fair stand-in for the worker while "
        "it is literally the same image"
    )


def test_the_vps_overlay_states_every_dev_resource_limit():
    """The dev worker's limits must not reach the VPS by accident — the bug this
    branch shipped and had to fix.

    "States", not "clears": today the overlay resets all three, but a VPS that
    wants a cap of its own is a legitimate answer (CF-223 may well want one).
    What is forbidden is silence, which is indistinguishable from having thought
    about it.

    `docker-compose.prod.yml` layers over the dev file, and Compose carries any
    field the overlay does not mention. The VPS is a 2-3 vCPU / 2-4 GB box
    running redis, api and worker together (DEPLOY.md), so inheriting Render's
    2 GB / 1 CPU caps holds the whole backend to one core. Asserted rather than
    left to the overlay's comment, because the failure is silent: it looks like
    a slow box, not a misconfiguration.
    """
    dev = _compose_service(REPO_ROOT / "docker-compose.yml", "worker")
    vps = _compose_service(REPO_ROOT / "docker-compose.prod.yml", "worker")

    assert not _has_deploy_resources(dev) or _has_deploy_resources(vps), (
        "docker-compose.yml caps the dev worker under `deploy.resources` and "
        "docker-compose.prod.yml is silent about it, so the VPS inherits it"
    )

    for key in RESOURCE_KEYS:
        if key not in dev:
            continue
        assert key in vps, (
            f"docker-compose.yml caps the dev worker's `{key}` and "
            "docker-compose.prod.yml is silent about it, so the VPS inherits a "
            "number chosen for Render's box. Say what this box should do: "
            f"`{key}: !reset null` for no limit, or a value of its own — a plain "
            "null is neither, and does not clear a base value"
        )
        assert isinstance(vps.get(key), _Reset) or vps.get(key) is not None, (
            f"docker-compose.prod.yml sets `{key}` to a bare null, which does not "
            "clear the dev value — the VPS still inherits it. Use `!reset null`, "
            "or give the key a real value"
        )


def test_the_vps_overlay_pins_its_own_ffmpeg_threads():
    """The dev default is Render's 1. The VPS is a different box, so the overlay
    has to state a value rather than inherit one.

    Only that it is *stated*, not what it is: CF-223 may well conclude 1 is right
    for a 2-3 vCPU box sharing itself with redis and the api, and a test that
    demanded the two differ would fail the correct config.
    """
    vps = _compose_service(REPO_ROOT / "docker-compose.prod.yml", "worker")
    # Production is not tunable by prefix. The VPS is layered with --env-file
    # too, so any variable this file reads can be set in the box's .env or in a
    # deploying shell - and FFMPEG_THREADS is the one setting CF-224 turned on.
    _assert_no_interpolation('docker-compose.prod.yml', vps)
    env = _env_map(vps)

    assert "FFMPEG_THREADS" in env, (
        "docker-compose.prod.yml must pin FFMPEG_THREADS: the dev file defaults it "
        "to Render's 1 (CF-241), and the VPS would inherit that silently. Pin it "
        "there — including if the right answer turns out to be the same number"
    )


def test_the_repro_overlay_still_reproduces_the_oom():
    """docker-compose.repro.yml exists to fail. Every number in it is
    load-bearing, and getting one wrong makes the run *succeed* — which reads as
    "already fixed" rather than as a broken repro. That is the failure mode this
    whole branch calls worse than none, so it is pinned.
    """
    repro = _compose_service(REPO_ROOT / "docker-compose.repro.yml", "worker")

    _assert_no_interpolation("docker-compose.repro.yml", repro)
    _assert_sets("docker-compose.repro.yml", repro, ("mem_limit", "memswap_limit", "cpus"))

    # The box CF-224 died on is Render's `starter`, so take the numbers from the
    # same table the parity checks use rather than restating them here.
    memory, cpus = RENDER_PLANS["starter"]

    assert _bytes(str(repro["mem_limit"])) == _bytes(memory), (
        f"the repro overlay must cap memory at {memory} — Render's `starter`, the "
        "box CF-224 actually died on"
    )
    assert str(repro["memswap_limit"]) == str(repro["mem_limit"]), (
        "the repro overlay must leave no swap: 512 MB of it is enough to carry the "
        "encode that killed production, and the run comes back green"
    )
    assert float(repro["cpus"]) == float(cpus), (
        f"the repro overlay must run on {cpus} CPU — `starter`'s share, and half "
        "the reason x264 oversubscribed its thread pool in the first place"
    )

    repro_env = _env_map(repro)
    assert "FFMPEG_THREADS" in repro_env, (
        "docker-compose.repro.yml no longer pins FFMPEG_THREADS, so the repro "
        "inherits the base file's 1 — at which CF-224's encode peaks at 407 MB, "
        "fits inside 512 MB, and the repro passes"
    )
    threads = int(repro_env["FFMPEG_THREADS"])
    assert threads >= 4, (
        f"the repro overlay runs FFMPEG_THREADS={threads}. CF-224 measured the "
        "ceiling: 1 thread peaks at 407 MB and survives, 2 at 468 MB, and only "
        "4-or-unbounded exceeds 512 MB. Below 4 this overlay reproduces nothing "
        "and reports success"
    )


def test_the_fast_overlay_is_faster_than_the_default_and_still_swapless():
    """The fast path has to actually be one — an overlay that quietly matches the
    defaults is a file people invoke for nothing.
    """
    dev = _compose_service(REPO_ROOT / "docker-compose.yml", "worker")
    fast = _compose_service(REPO_ROOT / "docker-compose.fast.yml", "worker")

    _assert_no_interpolation("docker-compose.fast.yml", fast, prefix="FAST_")
    _assert_sets("docker-compose.fast.yml", fast, ("mem_limit", "memswap_limit", "cpus"))

    assert float(_default(str(fast["cpus"]))) > float(_default(str(dev["cpus"]))), (
        "docker-compose.fast.yml gives the worker no more CPU than the default "
        "does, so it is a file that buys nothing"
    )
    assert _bytes(_default(str(fast["mem_limit"]))) >= _bytes(
        _default(str(dev["mem_limit"]))
    ), (
        "docker-compose.fast.yml gives the worker LESS memory than the default — "
        "it would be slower and tighter, which is neither of the two things this "
        "repo has a file for"
    )
    assert str(fast["memswap_limit"]) == str(fast["mem_limit"]), (
        "even the fast path stays swapless: swap makes `did it work` unreliable "
        "in a different way than it makes `did it fit` unreliable"
    )
