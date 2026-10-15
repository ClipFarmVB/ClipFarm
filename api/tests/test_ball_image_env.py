"""CF-14: the ball image's environment is a short, reviewed list.

`ml/modal_app.py` sets env vars on the Modal ball image to stop `inference`
importing model families ball tracking never uses, which is what quiets its
cold start. An env var on that image is easy to add and hard to see the effect
of — nothing runs the image in CI — so this pins what may be set there:

- every key is on `ALLOWED`, each traced to the inference==1.3.3 source that
  reads it (the CF-14 PR quotes file:line) or to the pycuda build;
- every value is written as a string, and every inference flag's value is one
  inference's `str2bool` accepts: "true" or "false" in any case. Anything else
  raises InvalidEnvironmentVariableError, a plain Exception, while
  `inference.core.env` is imported, and ml/pipeline/ball.py catches only
  ImportError there, so every ball job would fail at cold start;
- no log level is set high enough to hide warnings the ball path should still
  print (an RF-DETR stack fallback, a failed weight load), nor to a value
  inference's logger cannot parse.

This covers the image layer only: env set at runtime (`os.environ`), on a
function decorator, or through a Modal Secret is not read here.

A static read of the file, stdlib only, so it runs in every api test run
rather than skipping where Modal or celery is absent.

Run from the api/ dir: `cd api && pytest tests/test_ball_image_env.py`.
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BALL_IMAGE = REPO_ROOT / "ml" / "modal_app.py"

# The inference flags CF-14 sets. Each is read in inference/core/env.py as
# `str2bool(os.getenv(...))`; its reason sits beside it in ml/modal_app.py.
# Adding a flag means adding it here and its reason there.
_STR2BOOL_FLAGS = frozenset({
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

# Plus the pycuda build's compilers, which predate CF-14 and are not flags.
ALLOWED = frozenset({"CC", "CXX"}) | _STR2BOOL_FLAGS

# What inference/core/utils/environment.py:str2bool accepts from a string,
# after `.lower()` (lines 45-48). It returns a bool unchanged (39-40), but an
# env var always arrives as a string, so that branch never applies here.
_STR2BOOL_ACCEPTS = frozenset({"true", "false"})

# inference defaults to LOG_LEVEL=WARNING (inference/core/env.py:425), which
# still prints warnings. Only ERROR and above hide them, and ERROR/FATAL also
# make inference filter onnxruntime's UserWarnings (core/logger.py:24-25).
_HIDES_WARNINGS = frozenset({"ERROR", "CRITICAL", "FATAL"})
# Anything else must be a name `logger.setLevel(LOG_LEVEL)` accepts
# (core/logger.py:162, :170): exact-case `logging` level names only, so a
# numeric string such as "20" or a lowercase "info" raises ValueError there.
_SHOWS_WARNINGS = frozenset({"NOTSET", "DEBUG", "INFO", "WARN", "WARNING"})


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
    # contract.
    not_strings = [
        f"line {line}: {key}={value!r}"
        for line, key, value in _image_env(BALL_IMAGE)
        if not isinstance(value, str)
    ]
    assert not not_strings, (
        f"ball image env values must be string literals: {not_strings}"
    )


def test_every_inference_flag_value_parses_with_str2bool():
    # "0", "no" or "off" read as false to a person, but str2bool raises on
    # them at import, and ball.py catches only ImportError around that import.
    unparsable = [
        f"line {line}: {key}={value!r}"
        for line, key, value in _image_env(BALL_IMAGE)
        if key in _STR2BOOL_FLAGS
        and not (isinstance(value, str) and value.lower() in _STR2BOOL_ACCEPTS)
    ]
    assert not unparsable, (
        "inference's str2bool accepts only 'true' or 'false' (any case) and"
        " raises on anything else while inference is imported, which would"
        f" fail every ball job at cold start: {unparsable}"
    )


def _log_levels(path: Path) -> list[tuple[int, object, str]]:
    """(line, key, value) for every key naming a log level."""
    return [
        (line, key, str(value))
        for line, key, value in _image_env(path)
        if "LOG" in str(key).upper() and "LEVEL" in str(key).upper()
    ]


def test_no_ball_image_log_level_hides_warnings():
    hiding = [
        f"line {line}: {key}={value!r}"
        for line, key, value in _log_levels(BALL_IMAGE)
        if value.upper() in _HIDES_WARNINGS
    ]
    assert not hiding, (
        "the ball image must not set a log level of ERROR or above — it would"
        f" hide the warnings the ball path should still print: {hiding}"
    )


def test_every_ball_image_log_level_is_one_inference_can_set():
    unknown = [
        f"line {line}: {key}={value!r}"
        for line, key, value in _log_levels(BALL_IMAGE)
        if value.upper() not in _HIDES_WARNINGS and value not in _SHOWS_WARNINGS
    ]
    assert not unknown, (
        f"a log level here must be one of {sorted(_SHOWS_WARNINGS)}, exactly as"
        " written: inference passes it to logger.setLevel, which raises on any"
        f" other string at import: {unknown}"
    )
