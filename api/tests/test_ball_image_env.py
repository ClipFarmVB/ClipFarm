"""CF-14: the ball image's environment is a short, reviewed list.

`ml/modal_app.py` sets env vars on the Modal ball image to stop `inference`
importing model families ball tracking never uses, which is what quiets its
cold start. An env var on that image is easy to add and hard to see the effect
of — nothing runs the image in CI — so this pins what may be set there:

- every key is on `ALLOWED`, each traced to the inference==1.3.3 source that
  reads it (the CF-14 PR quotes file:line) or to the pycuda build;
- every value is written as a string, because inference parses its flags with
  a `str2bool` that rejects anything but "true"/"false";
- nothing raises a log level, which would hide the warnings the ball path
  should still print (an RF-DETR stack fallback, a failed weight load).

A static read of the file, stdlib only, so it runs in every api test run
rather than skipping where Modal or celery is absent.

Run from the api/ dir: `cd api && pytest tests/test_ball_image_env.py`.
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BALL_IMAGE = REPO_ROOT / "ml" / "modal_app.py"

# The pycuda build's compilers (they predate CF-14), then the inference flags
# CF-14 sets. Adding a key means adding it here, beside a reason.
ALLOWED = frozenset({
    "CC",
    "CXX",
    "DISABLE_VERSION_CHECK",
    "CORE_MODEL_GAZE_ENABLED",
    "CORE_MODEL_SAM_ENABLED",
    "CORE_MODEL_SAM2_ENABLED",
    "CORE_MODEL_SAM3_ENABLED",
    "CORE_MODEL_CLIP_ENABLED",
    "CORE_MODEL_PE_ENABLED",
    "CORE_MODEL_TROCR_ENABLED",
    "CORE_MODEL_GROUNDINGDINO_ENABLED",
    "CORE_MODEL_YOLO_WORLD_ENABLED",
    "PALIGEMMA_ENABLED",
    "FLORENCE2_ENABLED",
    "QWEN_2_5_ENABLED",
    "QWEN_3_ENABLED",
    "SMOLVLM2_ENABLED",
    "MOONDREAM2_ENABLED",
    "DEPTH_ESTIMATION_ENABLED",
})

# Levels at or below INFO. A key naming a log level may be set only to one of
# these; WARNING and up would silence the warnings this image must still show.
_QUIET_ENOUGH = frozenset({"NOTSET", "DEBUG", "INFO"})


def _image_env_calls(path: Path) -> list[ast.Call]:
    """Every `.env(...)` call inside the single `image = (...)` expression."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    specs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "image" for t in node.targets)
    ]
    assert len(specs) == 1, (
        f"{path.name} assigns `image` {len(specs)} times; this test reads the one"
        " expression that builds the ball image."
    )
    return [
        node
        for node in ast.walk(specs[0].value)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "env"
    ]


def _image_env(path: Path) -> list[tuple[int, object, object]]:
    """(line, key, value) for every pair any `.env` call sets, as written."""
    pairs: list[tuple[int, object, object]] = []
    for call in _image_env_calls(path):
        assert not call.keywords and len(call.args) == 1, (
            f"{path.name}:{call.lineno} .env() takes one dict literal here, so"
            " every key is visible to this test."
        )
        mapping = call.args[0]
        assert isinstance(mapping, ast.Dict), (
            f"{path.name}:{call.lineno} .env() is passed a"
            f" {type(mapping).__name__}; write the dict out literally."
        )
        for key, value in zip(mapping.keys, mapping.values):
            assert key is not None, (
                f"{path.name}:{call.lineno} .env() unpacks `**` into the dict;"
                " write each key out."
            )
            try:
                pairs.append(
                    (key.lineno, ast.literal_eval(key), ast.literal_eval(value))
                )
            except ValueError:
                raise AssertionError(
                    f"{path.name}:{key.lineno} .env() entry is not a literal;"
                    " write the key and value out."
                ) from None
    return pairs


def test_the_ball_image_sets_its_env_in_literals():
    # Guards the guard: if the env moved out of `image = (...)`, every test
    # below would pass on an empty list.
    assert _image_env(BALL_IMAGE), "found no .env() on the ball image"


def test_every_ball_image_env_key_is_allowlisted():
    unlisted = [
        f"line {line}: {key!r}"
        for line, key, _ in _image_env(BALL_IMAGE)
        if key not in ALLOWED
    ]
    assert not unlisted, (
        "ml/modal_app.py sets env vars on the ball image that are not on"
        f" ALLOWED: {unlisted}. Each key there is traced to the source that"
        " reads it; add the new one to ALLOWED with its reason."
    )


def test_every_ball_image_env_value_is_a_string():
    # Modal would stringify a bool, but `True` -> "True" is luck, not a
    # contract; and inference's str2bool raises on anything not "true"/"false".
    not_strings = [
        f"line {line}: {key}={value!r}"
        for line, key, value in _image_env(BALL_IMAGE)
        if not isinstance(value, str)
    ]
    assert not not_strings, (
        f"ball image env values must be string literals: {not_strings}"
    )


def test_no_ball_image_env_key_raises_a_log_level():
    raised = [
        f"line {line}: {key}={value!r}"
        for line, key, value in _image_env(BALL_IMAGE)
        if "LOG" in str(key).upper()
        and "LEVEL" in str(key).upper()
        and str(value).upper() not in _QUIET_ENOUGH
    ]
    assert not raised, (
        "the ball image must not raise a log level above INFO — it would hide"
        f" the warnings the ball path should still print: {raised}"
    )
