"""CF-172 — a production start must fail on missing config, not fall back to it.

The failure this guards against was silent by construction. Render's env group
only creates keys that carry a literal `value:`, so every `sync: false` key is
absent until a human pastes it in — and the api's `database_url` had a working
localhost default underneath, so an absent `DATABASE_URL` produced
`Connect call failed ('127.0.0.1', 5432)` from the pre-deploy migration rather
than anything naming the variable. The Supabase and R2 credentials are the same
shape and fail even later, at the first auth check or upload.

Two halves are tested here: the validator itself, and the two deployment files
that turn it on. The second half matters as much as the first — a guard the
production config never enables is not a guard.
"""

from pathlib import Path

import pytest

pytest.importorskip("pydantic_settings")

from app.config import (  # noqa: E402
    LOCAL_CORS_ORIGINS,
    LOCAL_DATABASE_URL,
    REQUIRED_IN_PRODUCTION,
    REQUIRED_IN_PRODUCTION_WORKER,
    Settings,
    production_config_error,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Everything the guard requires, at plausible-looking values.
PRODUCTION_ENV = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql+asyncpg://u:p@db.example.supabase.co:5432/postgres",
    "CORS_ORIGINS": "https://clipfarm.ca",
    "SUPABASE_URL": "https://project.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "service-role-key",
    "R2_ACCOUNT_ID": "account",
    "R2_ACCESS_KEY_ID": "access-key",
    "R2_SECRET_ACCESS_KEY": "secret-key",
    "R2_PUBLIC_URL": "https://clips.example.com",
}


@pytest.fixture
def clean_env(monkeypatch):
    """A process with none of these set — a fresh blueprint apply, or a laptop.

    The developer running these tests may well have a real `.env` and real
    exports; both would mask the very thing under test, so clear them and pin
    `_env_file=None` on every construction below.
    """
    for key in PRODUCTION_ENV:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _settings(**overrides):
    return Settings(_env_file=None, **overrides)


def test_zero_config_start_still_works(clean_env):
    """The local defaults are the reason this is gated rather than always on.

    A bare `uvicorn`, `docker compose up` on the dev stack, and CI's
    `pytest api/tests` all run with nothing exported. None of them may start
    demanding secrets — which is also why the gate is not `not debug`: `debug`
    defaults to False, so that spelling would fire on every one of them.
    """
    settings = _settings()

    assert settings.environment == "development"
    assert settings.database_url == LOCAL_DATABASE_URL


def test_production_start_names_every_missing_variable(clean_env):
    """The acceptance case: no config at all, in production mode."""
    with pytest.raises(Exception) as exc:  # pydantic wraps it in ValidationError
        _settings(environment="production")

    message = str(exc.value)
    for key in PRODUCTION_ENV:
        if key == "ENVIRONMENT":
            continue
        assert key in message, f"{key} is required but the error does not name it"


def test_production_rejects_the_localhost_database_default(clean_env):
    """An unset DATABASE_URL is not blank — it is the local default.

    Checking only for emptiness would let exactly the CF-172 deploy through:
    the variable was absent, the field was populated, and the process happily
    dialled 127.0.0.1.
    """
    env = dict(PRODUCTION_ENV)
    del env["DATABASE_URL"]

    with pytest.raises(Exception) as exc:
        _settings(**{k.lower(): v for k, v in env.items()})

    assert "DATABASE_URL" in str(exc.value)
    # Only the one that is actually missing.
    assert "SUPABASE_URL" not in str(exc.value)


def test_fully_configured_production_start_succeeds(clean_env):
    """Including a blank SENTRY_DSN, which is a supported production state
    (monitoring off) and must not be treated as missing config."""
    settings = _settings(**{k.lower(): v for k, v in PRODUCTION_ENV.items()})

    assert settings.environment == "production"
    assert settings.sentry_dsn == ""


def test_blank_credential_counts_as_missing(clean_env):
    """`R2_SECRET_ACCESS_KEY=` in the dashboard is a typo, not a configuration."""
    env = dict(PRODUCTION_ENV, R2_SECRET_ACCESS_KEY="   ")

    with pytest.raises(Exception) as exc:
        _settings(**{k.lower(): v for k, v in env.items()})

    assert "R2_SECRET_ACCESS_KEY" in str(exc.value)


def test_render_blueprint_turns_the_guard_on_without_a_human_step():
    """`ENVIRONMENT` must carry a literal `value:`, never `sync: false`.

    A `sync: false` guard would be absent in precisely the situation it exists
    to catch — the fresh apply where nobody has filled the group in yet.
    """
    yaml = pytest.importorskip("yaml")

    render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    shared = next(g for g in render["envVarGroups"] if g["name"] == "clipfarm-shared")
    entry = next(v for v in shared["envVars"] if v.get("key") == "ENVIRONMENT")

    assert entry.get("value") == "production", (
        "render.yaml must pin ENVIRONMENT=production in clipfarm-shared, or the "
        "required-settings check never runs in production"
    )
    assert "sync" not in entry


def test_vps_compose_turns_the_guard_on_for_both_services():
    """The other production path (CF-41). api and worker share one `.env.docker`
    with the dev stack, where the defaults are supposed to work — so production
    mode is pinned in the override file, not in the env file.
    """
    yaml = pytest.importorskip("yaml")

    text = (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    # `!override` / `!reset` are Compose tags, meaningless to safe_load.
    compose = yaml.safe_load(text.replace("!override", "").replace("!reset", ""))

    for service in ("api", "worker"):
        env = compose["services"][service].get("environment") or {}
        assert str(env.get("ENVIRONMENT", "")).lower() == "production", (
            f"docker-compose.prod.yml must pin ENVIRONMENT for {service} — that "
            "box is production and .env.docker carries dev defaults"
        )


def test_empty_database_url_counts_as_missing(clean_env):
    """The literal Render shape: a key added to the group and left blank.

    Distinct from the localhost-default path above — there the variable is
    absent and pydantic supplies the default; here it is present and empty, so
    the sentinel comparison never fires and only the emptiness check catches it.
    This is the case the deploy docs lean on hardest, so pin it.
    """
    env = dict(PRODUCTION_ENV, DATABASE_URL="")

    with pytest.raises(Exception) as exc:
        _settings(**{k.lower(): v for k, v in env.items()})

    assert "DATABASE_URL" in str(exc.value)

    # Reported once, not once per detection path. Asserted against the list
    # rather than the rendered message, which mentions the variable again in its
    # closing note about localhost defaults.
    blank = _settings(
        **{k.lower(): v for k, v in dict(env, ENVIRONMENT="development").items()}
    ).model_copy(update={"environment": "production"})
    assert blank.missing_in_production(REQUIRED_IN_PRODUCTION) == ["DATABASE_URL"]


def test_required_names_are_real_fields_with_the_expected_env_names(clean_env):
    """The guard stores field names and derives env names from the model.

    Both halves need pinning. A typo'd field name would otherwise surface as an
    AttributeError inside the validator — on a production box, at boot, which is
    exactly the situation this whole change exists to make legible. And the env
    names are what operators paste into the dashboard, so they must match the
    ones documented in render.yaml.
    """
    for field in REQUIRED_IN_PRODUCTION + REQUIRED_IN_PRODUCTION_WORKER:
        assert field in Settings.model_fields, f"{field} is not a Settings field"
        assert Settings.env_name_for(field) == field.upper()

    assert set(PRODUCTION_ENV) - {"ENVIRONMENT"} == {
        Settings.env_name_for(f) for f in REQUIRED_IN_PRODUCTION
    }


def test_modal_tokens_are_required_of_the_worker_only(clean_env):
    """Scoping, deliberately (CF-172 review).

    Modal is a worker dependency and `Settings` is shared config the api
    imports, so requiring the tokens in the validator would take the web service
    down over a credential it never uses. They are checked at worker boot
    instead — but they ARE checked: since CF-164 the image ships no model, so
    blank tokens mean every game completes with trajectory-only labels rather
    than running slowly.
    """
    settings = _settings(**{k.lower(): v for k, v in PRODUCTION_ENV.items()})

    # The api half is satisfied without them...
    assert settings.modal_token_id == ""
    # ...and the worker half is not.
    assert settings.missing_in_production(REQUIRED_IN_PRODUCTION_WORKER) == [
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
        "ROBOFLOW_API_KEY",
    ]
    assert settings.missing_in_production(REQUIRED_IN_PRODUCTION) == []


def test_worker_boot_refuses_a_production_worker_without_modal(clean_env, monkeypatch):
    """The check has to be wired to something. `celeryd_init` fires in the
    worker daemon and not in the api process that imports celery_app to enqueue,
    which is what makes the scoping above hold in practice.
    """
    pytest.importorskip("celery")
    from app.workers import celery_app as worker_module

    production = _settings(**{k.lower(): v for k, v in PRODUCTION_ENV.items()})
    monkeypatch.setattr(worker_module, "settings", production)

    with pytest.raises(SystemExit) as exc:
        worker_module._check_worker_production_config()

    assert "MODAL_TOKEN_ID" in str(exc.value)
    assert "MODAL_TOKEN_SECRET" in str(exc.value)
    assert "ROBOFLOW_API_KEY" in str(exc.value)


def test_worker_boot_is_silent_when_configured_or_local(clean_env, monkeypatch):
    """And a configured production worker — like every dev worker, which is not
    in production mode at all — must boot untouched."""
    pytest.importorskip("celery")
    from app.workers import celery_app as worker_module

    configured = _settings(
        **{k.lower(): v for k, v in PRODUCTION_ENV.items()},
        modal_token_id="token-id",
        modal_token_secret="token-secret",
        roboflow_api_key="roboflow-key",
    )
    monkeypatch.setattr(worker_module, "settings", configured)
    worker_module._check_worker_production_config()

    monkeypatch.setattr(worker_module, "settings", _settings())
    worker_module._check_worker_production_config()


def test_production_rejects_the_localhost_cors_default(clean_env):
    """The api boots clean on this one and passes its health check.

    Nothing server-side ever complains: with the localhost default in place,
    every request from the real web origin is rejected by the CORS middleware
    and the only symptom is in the browser console. Same absence-masked-by-a-
    default shape as DATABASE_URL, so it gets the same sentinel treatment.
    """
    env = dict(PRODUCTION_ENV)
    del env["CORS_ORIGINS"]

    with pytest.raises(Exception) as exc:
        _settings(**{k.lower(): v for k, v in env.items()})

    assert "CORS_ORIGINS" in str(exc.value)
    assert _settings().cors_origins == LOCAL_CORS_ORIGINS


def test_a_production_origin_that_also_allows_localhost_is_accepted(clean_env):
    """The sentinel is exact equality with the default, not a localhost search.

    Keeping a localhost entry alongside the real origin is a legitimate (if
    unusual) production choice; only the untouched default means "never set".
    """
    settings = _settings(
        **{k.lower(): v for k, v in PRODUCTION_ENV.items()},
    )
    assert settings.cors_origins_list == ["https://clipfarm.ca"]

    both = _settings(
        **{
            **{k.lower(): v for k, v in PRODUCTION_ENV.items()},
            "cors_origins": "https://clipfarm.ca,http://localhost:3000",
        }
    )
    assert "http://localhost:3000" in both.cors_origins_list


def test_worker_guard_survives_celerys_exception_swallowing(clean_env, monkeypatch):
    """The guard raises SystemExit, and that is not a stylistic choice.

    `celery.utils.dispatch.Signal.send` wraps every receiver in
    `except Exception` and appends the exception to a response list nobody
    reads — its docstring says "In Celery 'send' and 'send_robust' do the same
    thing". A RuntimeError from this handler is therefore discarded and the
    worker boots anyway, with no trace: celeryd_init fires before Celery
    configures logging, so even `Signal.send`'s own `logger.exception` reaches
    nothing. SystemExit is a BaseException and escapes.

    Sent through a real Signal rather than asserted against the class hierarchy,
    so this keeps holding if Celery's dispatcher changes.
    """
    pytest.importorskip("celery")
    from celery.utils.dispatch import Signal

    from app.workers import celery_app as worker_module

    production = _settings(**{k.lower(): v for k, v in PRODUCTION_ENV.items()})
    monkeypatch.setattr(worker_module, "settings", production)

    signal = Signal(name="test_celeryd_init", providing_args=[])
    signal.connect(worker_module._check_worker_production_config, weak=False)

    with pytest.raises(SystemExit) as exc:
        signal.send(sender=None)

    assert "MODAL_TOKEN_ID" in str(exc.value)


def test_every_required_variable_has_a_slot_in_the_env_template():
    """A named failure only helps if the file you are sent to edit has somewhere
    to put the answer (CF-172 review 3).

    `docker-compose.prod.yml` runs the VPS api and worker in production mode, so
    everything the guard requires is a hard boot requirement on that box — and
    the box is configured entirely from `.env.docker`, copied from this
    template. A required variable the template never mentions turns a clear
    refusal back into a puzzle: the same failure class this change fixes,
    displaced one level. Commented lines count; an operator needs the name and
    the explanation, not an active default.
    """
    template = (REPO_ROOT / ".env.docker.example").read_text(encoding="utf-8")

    for field in REQUIRED_IN_PRODUCTION + REQUIRED_IN_PRODUCTION_WORKER:
        name = Settings.env_name_for(field)
        assert name in template, (
            f"{name} is required in production but .env.docker.example never "
            "mentions it — the VPS operator gets a boot refusal naming a "
            "variable their template has no slot for"
        )


def test_the_error_message_points_at_both_deploy_paths():
    """Render and the VPS supply these differently, and the message is read on
    both. Naming only `sync: false` and DEPLOY_RENDER.md sends a VPS operator to
    a mechanism that does not exist on their box.
    """
    message = production_config_error(["DATABASE_URL"])

    assert "DEPLOY_RENDER.md" in message
    assert "DEPLOY.md" in message
    assert ".env.docker" in message


# ── CF-92: the tools CI runs on are pinned ─────────────────────────────────
#
# Not a config-guard like the ones above, but the same failure shape: a value
# that lives in a file nobody re-reads, and whose drift shows up as an
# unrelated-looking red build.

TOOLING_TXT = REPO_ROOT / "requirements-tooling.txt"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _pins(path):
    """`name -> version` for the `==` requirements in a requirements file."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name, _, version = line.partition("==")
        out[name.strip().lower()] = version.strip()
    return out


def test_every_tool_ci_installs_is_pinned():
    """An unpinned entry here is the whole bug: ruff 0.16.0 widened its default
    rule set and turned #77 red with nine findings in code nobody had touched.
    """
    unpinned = [
        line.strip()
        for line in TOOLING_TXT.read_text(encoding="utf-8").splitlines()
        if (stripped := line.split("#", 1)[0].strip())
        and not stripped.startswith("-")
        and "==" not in stripped
    ]
    assert not unpinned, (
        f"requirements-tooling.txt has unpinned entries: {unpinned}. "
        "A floating version means an upstream release can change CI's answer "
        "on a branch nobody touched, which is what CF-92 exists to stop."
    )
    assert _pins(TOOLING_TXT), "no pins found — did the file format change?"


def _run_steps(workflow):
    """Every `run:` script in a workflow, as strings.

    Parsed rather than grepped. The previous version of this test matched lines
    starting `- run:`, which is only one of the several shapes a step can take:
    a `- name:` step puts `run:` on its own line, and `run: |` puts the command
    on the lines *after* it. Both were invisible to it, and both are ordinary
    GitHub Actions syntax rather than anything exotic.
    """
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                yield step["run"]


def _pip_install_targets(script):
    """Package arguments of every `pip install` in a shell script.

    `-r file` pairs, bare flags, and the pip/python words themselves are
    dropped; what is left is what pip would resolve. Commands are split on
    newlines, `&&`, `;` and `|` so a multi-command `run: |` block is read as
    the several commands it is.
    """
    import re

    # `pip`, `pip3`, `pip3.11` — all of which install into the same environment
    # and all of which are things people write in a workflow. Not `pipenv`.
    is_pip = re.compile(r"pip3?(\.\d+)?$").fullmatch

    for command in re.split(r"[\n;]|&&|\|\|", script):
        words = command.split()
        pip_at = next((i for i, w in enumerate(words) if is_pip(w)), None)
        if pip_at is None or "install" not in words:
            continue
        if words.index("install") < pip_at:
            continue
        rest = words[words.index("install") + 1:]
        skip_next = False
        for word in rest:
            if skip_next:
                skip_next = False
                continue
            if word in ("-r", "--requirement", "-c", "--constraint", "-e"):
                skip_next = True
                continue
            if word.startswith("-"):
                continue
            yield word


def test_ci_installs_the_tooling_file_rather_than_naming_tools_inline():
    """The durable half. Nothing else in the repo parses the workflow, so
    without this a later PR can quietly restore `pip install ruff mypy pytest`
    and the pins stop applying while every check stays green.

    Now checks the *shape* of every install rather than looking for three tool
    names in `- run:` lines. The name list was the weaker half of the two: it
    could only ever catch the tools someone thought to list, so an inline
    `pip install numpy` — equally unpinned, equally able to change CI's answer
    on a branch nobody touched — went through. Anything pip resolves without a
    `==` is the actual problem, whatever it is called.
    """
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))

    installs = [
        (script, target)
        for script in _run_steps(workflow)
        for target in _pip_install_targets(script)
    ]
    unpinned = sorted({target for _, target in installs if "==" not in target})

    assert not unpinned, (
        f"ci.yml installs {unpinned} inline without a `==` pin. Add it to "
        "requirements-tooling.txt with a pin instead — an unpinned name "
        "resolves to whatever is current on the day the job runs, which is "
        "what CF-92 exists to stop."
    )


def test_ci_still_installs_the_tooling_file():
    """Separate from the shape check above, because the two fail for opposite
    reasons: that one fires when something extra is installed, this one when
    the pinned install is gone. Collapsed into one test, a PR that deleted the
    tooling step entirely would leave nothing unpinned and read as a pass.
    """
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))

    targets = [t for s in _run_steps(workflow) for t in _pip_install_targets(s)]
    requirement_files = [
        word
        for script in _run_steps(workflow)
        for word in script.split()
        if word.endswith(".txt")
    ]

    assert "requirements-tooling.txt" in requirement_files, (
        "ci.yml no longer installs requirements-tooling.txt — if the tooling "
        "step moved, point this test at it; if it was inlined, the CF-92 pins "
        f"are no longer in effect. Inline install targets seen: {targets}"
    )


# The four step shapes below are the ones the previous text-matching guard let
# through. Asserted against synthetic workflows rather than by editing ci.yml,
# so the coverage survives any later reshuffle of the real file.

_SYNTHETIC = {
    "-r file plus an inline package": """
jobs:
  api:
    steps:
      - run: pip install -r requirements-tooling.txt numpy
""",
    "a separate inline install step": """
jobs:
  api:
    steps:
      - run: pip install -r requirements-tooling.txt
      - run: pip install numpy
""",
    "the `- name:` / `run:` two-line form": """
jobs:
  api:
    steps:
      - run: pip install -r requirements-tooling.txt
      - name: extra tooling
        run: pip install numpy
""",
    "a `run: |` block scalar": """
jobs:
  api:
    steps:
      - run: pip install -r requirements-tooling.txt
      - run: |
          pip install numpy
""",
    "chained with && inside one run": """
jobs:
  api:
    steps:
      - run: pip install -r requirements-tooling.txt && pip install numpy
""",
}


@pytest.mark.parametrize("shape", sorted(_SYNTHETIC))
def test_an_unpinned_inline_install_is_detected_in_every_step_shape(shape):
    """Each of these passed the old `- run:`-prefix-and-tool-name check.

    The first is the one that matters most: it is what a maintainer would write
    to add a package alongside the pinned file, and the old exclusion of any
    line containing `-r ` swallowed the whole line.
    """
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(_SYNTHETIC[shape])

    targets = [t for s in _run_steps(workflow) for t in _pip_install_targets(s)]

    assert "numpy" in targets, f"{shape}: the inline install was not seen at all"
    assert [t for t in targets if "==" not in t] == ["numpy"]


@pytest.mark.parametrize(
    "command",
    [
        "pip install numpy",
        "pip3 install numpy",
        "pip3.11 install numpy",
        "python -m pip install numpy",
        "uv pip install numpy",
    ],
)
def test_every_spelling_of_pip_is_recognised(command):
    """`pip3` was missed by the first version of this parser.

    It matched the bare word `pip`, so `pip3 install numpy` — the same install
    into the same environment — read as not-a-pip-command and returned nothing.
    A guard that depends on which of two interchangeable names someone typed is
    the same shape of hole as the tool-name list this replaced.
    """
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(f"jobs:\n  api:\n    steps:\n      - run: {command}\n")
    assert [t for s in _run_steps(workflow) for t in _pip_install_targets(s)] == ["numpy"]


def test_pipenv_is_not_read_as_pip():
    """The name check has to be exact at the end — `pipenv install` manages a
    Pipfile and is not what this guard is about."""
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load("jobs:\n  api:\n    steps:\n      - run: pipenv install numpy\n")
    assert not [t for s in _run_steps(workflow) for t in _pip_install_targets(s)]


def test_a_pinned_inline_install_is_allowed():
    """The guard is about drift, not about where a package is declared. Pinning
    inline is worse style than the tooling file, but it cannot change CI's
    answer on a branch nobody touched, so it is not this test's business.
    """
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load("""
jobs:
  api:
    steps:
      - run: pip install numpy==1.26.4
""")
    targets = [t for s in _run_steps(workflow) for t in _pip_install_targets(s)]
    assert targets == ["numpy==1.26.4"]
    assert not [t for t in targets if "==" not in t]


def test_a_non_pip_install_is_not_mistaken_for_one():
    """`npm ci --workspace=web` and `npm install` are in this same workflow."""
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load("""
jobs:
  web:
    steps:
      - run: npm install left-pad
      - run: npm ci --workspace=web
""")
    assert not [t for s in _run_steps(workflow) for t in _pip_install_targets(s)]


def test_requirement_and_constraint_files_are_not_read_as_packages():
    """`-r`/`-c` take the next word. Without consuming it the filename itself
    reads as an unpinned package and every correct workflow fails.
    """
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load("""
jobs:
  api:
    steps:
      - run: pip install -r requirements-tooling.txt -c constraints.txt --upgrade
""")
    assert not [t for s in _run_steps(workflow) for t in _pip_install_targets(s)]


def test_the_two_pytest_pins_agree():
    """`pytest ml/tests` runs on the tooling install; `pytest api/tests` runs
    after requirements-dev is installed. If the two disagree, pip downgrades
    mid-job and the suites run on different versions — which is the state this
    change fixed, so it is worth keeping fixed.
    """
    tooling = _pins(TOOLING_TXT)
    dev = _pins(REPO_ROOT / "api" / "requirements-dev.txt")

    assert "pytest" in tooling and "pytest" in dev, "both files must pin pytest"
    assert tooling["pytest"] == dev["pytest"], (
        f"requirements-tooling.txt pins pytest=={tooling['pytest']} but "
        f"api/requirements-dev.txt pins pytest=={dev['pytest']}. pip will "
        "downgrade at the second install and the two test steps will run on "
        "different versions."
    )
