"""CF-65c: the deploy config must stay compatible with multiple worker replicas.

These assert deployment *shape*, not behaviour — the kind of thing that silently
regresses because re-adding a disk or a container_name looks harmless in review
and only fails when someone tries to scale months later.
"""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    path = _ROOT / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _render_worker():
    for service in _load("render.yaml").get("services", []):
        if service.get("name") == "clipfarm-worker":
            return service
    pytest.fail("clipfarm-worker service not found in render.yaml")


def test_worker_declares_no_disk():
    """A Render disk pins a service to ONE instance, so a disk here would make
    numInstances > 1 impossible. The model cache is deliberately container-local
    (CF-65c) — see the comment in render.yaml."""
    assert "disk" not in _render_worker()


def test_worker_model_cache_paths_are_still_set():
    """Dropping the disk must not drop CF-40's cache wiring: the paths still
    point at /models, which is now the container's own filesystem."""
    env = {e.get("key"): e.get("value") for e in _render_worker().get("envVars", []) if "key" in e}
    for var in ("MODEL_CACHE_DIR", "INFERENCE_HOME", "MODELS_DIR", "YOLO_CONFIG_DIR"):
        assert env.get(var, "").startswith("/models"), f"{var} should live under /models"


def test_compose_worker_has_nothing_that_blocks_scaling():
    """`--scale worker=N` fails outright if the service fixes a container name,
    and port-binds collide on the host."""
    worker = _load("docker-compose.yml")["services"]["worker"]
    assert "container_name" not in worker
    assert "ports" not in worker


def test_modal_gpu_fanout_is_capped():
    """M replicas x N children invoke Modal independently; without a ceiling a
    queue spike maps 1:1 onto simultaneously-billed T4s."""
    source = (_ROOT / "ml" / "modal_app.py").read_text(encoding="utf-8")
    assert "max_containers=MAX_GPU_CONTAINERS" in source
    # Both entry points must be capped — the worker prefers the streaming one.
    assert source.count("max_containers=MAX_GPU_CONTAINERS") == 2
