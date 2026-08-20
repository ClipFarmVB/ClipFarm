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
    assert str(exc.value).count("DATABASE_URL") == 1, (
        "an empty DATABASE_URL must be reported once, not once per detection path"
    )


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
