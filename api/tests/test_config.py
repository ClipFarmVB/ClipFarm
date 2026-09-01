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

from pydantic import ValidationError  # noqa: E402
from pydantic_core import InitErrorDetails, PydanticCustomError  # noqa: E402

from app.config import (  # noqa: E402
    LOCAL_CORS_ORIGINS,
    LOCAL_DATABASE_URL,
    REQUIRED_IN_PRODUCTION,
    REQUIRED_IN_PRODUCTION_WORKER,
    Settings,
    _boot_error,
    _origin_problem,
    _origin_problems,
    _settings_or_boot_error,
    cors_origins_error,
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


# ── CF-235: CORS_ORIGINS set, but wrong ────────────────────────────
#
# CF-172 above catches the variable being unset. Everything below is a value
# someone did paste, which the guard used to accept.


def _production_with_cors(value):
    return _settings(
        **{**{k.lower(): v for k, v in PRODUCTION_ENV.items()}, "cors_origins": value}
    )


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "https://clipfarm.ca,*",
        "*,https://clipfarm.ca",
    ],
)
def test_production_rejects_a_wildcard_origin(clean_env, value):
    """The headline case. main.py sets allow_credentials=True, and Starlette
    echoes the requesting origin with credentials allowed rather than sending a
    literal `*` — so the browser rule that normally defuses this never applies.
    Rejected wherever it appears in the list, not just alone.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    assert "CORS_ORIGINS" in str(exc.value)
    assert "allow_credentials" in str(exc.value)


def test_production_rejects_a_value_that_parses_to_no_origins(clean_env):
    """`,` is not empty, is not the localhost default, and yields []. CF-172's
    guard sees a non-blank string and passes it; the app then boots allowing no
    origin at all, health check green and every browser request blocked.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(",")

    assert "contains no origins" in str(exc.value)


@pytest.mark.parametrize(
    "value",
    [
        "https://clipfarm.ca/",          # trailing slash
        "https://clipfarm.ca/app",       # path
        "https://clipfarm.ca?x=1",       # query
        "https://clipfarm.ca#frag",      # fragment
    ],
)
def test_production_rejects_an_origin_that_is_not_bare(clean_env, value):
    """None of these is a browser Origin, and Starlette compares strings, so
    each silently matches nothing while looking right in the dashboard."""
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    assert "trailing" in str(exc.value) or "scheme://host" in str(exc.value)


@pytest.mark.parametrize("value", ["clipfarm.ca", "https://", "ftp://clipfarm.ca"])
def test_production_rejects_an_entry_that_is_not_scheme_and_host(clean_env, value):
    """Both halves have to be checked. `clipfarm.ca` parses to an empty scheme
    with the whole string in .path, and a bare `https://` parses to an empty
    netloc — a check that only looked at path/query/fragment would misreport the
    first and accept the second.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    assert "scheme://host" in str(exc.value)


@pytest.mark.parametrize(
    "value",
    [
        "https://user:pass@clipfarm.ca",   # userinfo
        "https://user@clipfarm.ca",        # username only
        "https://@clipfarm.ca",            # empty userinfo, still an `@`
    ],
)
def test_production_rejects_an_origin_carrying_userinfo(clean_env, value):
    """RFC 6454 reduces a URL to scheme://host[:port] before sending it, so a
    browser never puts credentials in an Origin header and an entry containing
    them matches nothing — the same silent failure as a trailing slash.

    Rejecting is not by itself what keeps the boot log safe — an earlier version
    of this docstring said it was. `cors_origins_error` echoes every bad entry
    back whatever the reason for rejecting it, so what protects the log is the
    redaction in `_redacted_origin`, tested below.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    assert "userinfo" in str(exc.value)


@pytest.mark.parametrize(
    "value",
    [
        "https://clipfarm.ca:99999",   # out of range
        "https://clipfarm.ca:abc",     # not a number
        "https://clipfarm.ca:0",       # in urlsplit's range, not a real port
    ],
)
def test_production_rejects_a_malformed_port(clean_env, value):
    """`urlsplit` does not validate on parse — it raises only when `.port` is
    read, so a check that never reads it accepts all three. `:0` is the one
    that needs saying out loud: it is inside the range `.port` accepts, so it
    raises nothing and has to be rejected explicitly.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    assert "port" in str(exc.value)


def test_production_rejects_a_host_with_a_trailing_colon(clean_env):
    """`https://clipfarm.ca:` — `.port` returns None rather than raising, so
    this is not caught by reading the port, and the netloc is not empty, so it
    is not caught by the scheme/host check either. It falls between both.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors("https://clipfarm.ca:")

    assert "trailing `:`" in str(exc.value)


# `https://clip farm.ca` used to live here, on the reading that a space made the
# host unusable. It is a whitespace problem, and it is now caught with the tab,
# CR and LF cases above — before urlsplit rather than after it, since those
# three never reach the host check at all. Same rejection, honester branch.
@pytest.mark.parametrize(
    "value",
    [
        "https://:8443",          # port but no host
    ],
)
def test_production_rejects_an_entry_with_no_usable_host(clean_env, value):
    """What is left once userinfo and port are peeled off has to be a host.

    `https://:8443` is the one that needs saying: its netloc is non-empty, it
    carries no userinfo, and its port is valid, so it clears every other check
    in the function and arrives here with the host simply missing.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    assert "host" in str(exc.value)


def test_production_rejects_a_mixed_case_origin(clean_env):
    """Starlette matches origins as exact strings, so this is as broken as a
    trailing slash — and looks even more correct in a dashboard."""
    with pytest.raises(ValidationError) as exc:
        _production_with_cors("HTTPS://ClipFarm.ca")

    assert "lower-case" in str(exc.value)


@pytest.mark.parametrize(
    "value",
    [
        "https://*.clipfarm.ca",       # the one an operator actually reaches for
        "https://sub.*.clipfarm.ca",
        "https://*",
    ],
)
def test_production_rejects_a_wildcard_host(clean_env, value):
    """Only the BARE `*` was rejected before; these returned None and passed.

    Starlette compares allow_origins as exact strings — allow_origin_regex is
    the only other route and main.py passes none — so a wildcard host allows
    nobody. That is the same silent outage a trailing slash causes, and it is
    the likelier mistake of the two: a trailing slash is a slip, while "allow
    all my subdomains" is an intention this config language cannot express, so
    someone reaches for it on purpose.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    assert "wildcard" in str(exc.value)
    # Not the bare-`*` branch, which would pass this test for the wrong reason.
    assert "allow_credentials" not in str(exc.value)


@pytest.mark.parametrize("ws", ["\t", "\n", "\r", " "])
def test_production_rejects_whitespace_urlsplit_would_have_swallowed(clean_env, ws):
    """The check has to run on the raw entry, and it used to run on the parsed
    host. urlsplit strips tab, CR and LF *before* parsing, so `.hostname` came
    back clean and the entry was accepted — while `cors_origins_list` splits the
    raw env string, handing Starlette the whitespace-bearing value, which
    matches no Origin header. Only a plain space was ever caught.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(f"https://clip{ws}farm.ca")

    assert "whitespace" in str(exc.value)


def test_an_unparseable_host_is_reported_rather_than_escaping(clean_env):
    """`urlsplit` raises on a malformed bracketed netloc, and it sat above every
    check with no guard.

    Asserting "something raised" would pass against the unguarded version too,
    since pydantic wraps a stray ValueError from a validator into a
    ValidationError either way. What distinguishes them is the message: guarded,
    the operator is told which variable and where to read about it, AND the
    other bad entry in the same list is still reported. Unguarded, the
    comprehension that builds `problems` dies on the first bad entry, so the
    second one is never mentioned and neither is CORS_ORIGINS.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors("http://[bad],https://clipfarm.ca/")

    message = str(exc.value)
    assert "CORS_ORIGINS" in message
    assert "could not be parsed" in message
    # The entry AFTER the unparseable one still gets reported.
    assert "nothing after it" in message


@pytest.mark.parametrize(
    "value",
    [
        "https://admin:hunter2@clipfarm.ca",    # fails on userinfo
        "https://admin:hunter2@clipfarm.ca/",   # fails on the PATH check first
        "https://Admin:Hunter2@clipfarm.ca",    # fails on the CASE check first
        "https://admin:hunter2@[bad]",          # fails to parse at all
        "https://admin:pa@ss@clipfarm.ca",      # `@` inside the password
        # A stray delimiter between the credential and the `@`. These are the
        # ones an authority-only search misses: the `/`, `?` or `#` truncates
        # the authority before the `@`, so there is no userinfo left to find
        # and the entry was echoed whole. urlsplit reports username and
        # password as None for all three — it reads `admin:hunter2` as host
        # `admin`, port `hunter2` — so nothing but a textual rule catches them.
        "https://admin:hunter2/x@clipfarm.ca",
        "https://admin:hunter2?a@clipfarm.ca",
        "https://admin:hunter2#a@clipfarm.ca",
        "admin:hunter2/x@clipfarm.ca",          # and in the no-scheme branch
        # An empty authority: the delimiter is in LEADING position, so the
        # authority truncates to "" and an "is this a bare host?" test says yes.
        "//admin:hunter2@clipfarm.ca",
        "https:///admin:hunter2@clipfarm.ca",
        # An all-digit password. This is the shape that retired the textual
        # rule: `admin:12345` cannot be told from `clipfarm.ca:8443`.
        "https://admin:12345/x@clipfarm.ca",
        # A second `@` after the authority, which anchored the old slice to the
        # wrong one and printed `https://***@handle` — host gone.
        "https://admin:hunter2@clipfarm.ca/x@handle",
    ],
)
def test_a_rejected_origin_does_not_echo_its_password(clean_env, value):
    """A failed production boot prints this message into the service log, whose
    audience is everyone with dashboard access — wider than everyone who can
    read the env var. Before the redaction the entry went in verbatim.

    The three shapes after the first are why the redaction is applied at the
    interpolation site rather than inside the userinfo branch, which is where it
    looks like it belongs. A credential-bearing entry usually fails some *other*
    check first, and a branch-local redaction would echo it in full.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    message = str(exc.value)
    assert "hunter2" not in message.lower()
    assert "12345" not in message
    assert "***" in message
    # Nothing of the entry survives but the scheme — the operator finds it by
    # the entry number instead, which the test below pins.
    assert "admin" not in message


@pytest.mark.parametrize("value", ["https://clipfarm.ca?", "https://clipfarm.ca#"])
def test_production_rejects_an_empty_but_present_delimiter(clean_env, value):
    """`urlsplit` reports query and fragment as "" whether the delimiter was
    absent or present-and-empty, so a truthiness test cannot tell
    `https://clipfarm.ca` from `https://clipfarm.ca?`.

    The existing cases all use non-empty values (`?x=1`, `#frag`), which is why
    the gap was invisible to them. Starlette compares exact strings, so the bare
    delimiter matches nothing — the same silent outage as the trailing slash a
    few lines above.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    assert "nothing after it" in str(exc.value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://clipfarm.ca:080", "default port"),      # padded AND default
        ("https://clipfarm.ca:0443", "default port"),
        ("https://clipfarm.ca:08443", "zero-padded"),    # padded only
    ],
)
def test_a_padded_default_port_costs_one_refused_boot_not_two(clean_env, value, expected):
    """`:080` on http is both padded and the default. Reporting the padding
    first sends the operator to `:80`, which the next boot refuses as the
    default port — two refused production boots for one mistake.

    "Drop it" is the right instruction whenever the effective port is the
    default, so that check goes first; the padded message is left for ports
    worth keeping.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    assert expected in str(exc.value)


@pytest.mark.parametrize(
    "value",
    [
        "https://clipfarm.ca/@handle",       # `@` in the path, no credential
        "https://clipfarm.ca:8443/x@y",      # ...with a port on the authority
        "clipfarm.ca/@handle",               # ...and with no scheme at all
    ],
)
def test_an_at_sign_is_redacted_even_where_it_is_probably_harmless(clean_env, value):
    """These carry no credential, and they are redacted anyway.

    Two earlier versions of this file asserted the opposite — that an entry
    whose `@` sits in the path is echoed intact — and a review round showed why
    that cannot be pinned: `admin:12345` is textually indistinguishable from
    `host:8443`, so no rule separates userinfo from a host and port. A test
    asserting "this one is safe to print" was enforcing the opposite of the
    stated tie-break.

    The cost is that the entry's text is gone from the message, which is what
    the numbering below exists to pay for.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    message = str(exc.value)
    assert "***" in message
    assert value not in message


def test_each_problem_is_numbered_so_a_redacted_entry_is_identifiable(clean_env):
    """Redacted entries print as `scheme://***`, so two of them are otherwise
    indistinguishable and the operator cannot tell which one to go and fix.

    The position in the pasted list is what replaces the text. It has to survive
    entries that are *fine* — the numbering counts every origin, not only the
    bad ones — or it points at the wrong entry.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(
            "https://clipfarm.ca,https://a:b@x.ca,https://ok.ca,https://c:d@y.ca"
        )

    message = str(exc.value)
    assert "entry 2 'https://***'" in message
    assert "entry 4 'https://***'" in message
    # The good entries are counted but not reported.
    assert "entry 1" not in message and "entry 3" not in message
    # One problem per line. This assertion used to live in its own test, which
    # a rewrite of the surrounding block deleted as collateral — leaving the
    # `"; "` join, itself a finding from an earlier round, with nothing holding
    # it. Two of the messages carry their own semicolon, so a joined form gives
    # no way to see where one problem ends.
    lines = [line for line in message.splitlines() if line.startswith("  - ")]
    assert len(lines) == 2


def test_the_entry_number_matches_what_was_pasted(clean_env):
    """`cors_origins_list` drops blank entries, so numbering over it counts the
    survivors rather than the positions. With a doubled comma the operator's
    third entry was reported as "entry 1", pointing at a comma.

    That number is the only handle they have on an entry whose text has been
    redacted, so a wrong one is worse than none.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(",,https://admin:hunter2@clipfarm.ca")

    assert "entry 3 'https://***'" in str(exc.value)


@pytest.mark.parametrize(
    "value",
    [
        # A missing comma, which is what actually produces this: one "entry"
        # holding two URLs. `partition("://")` split at the FIRST `://`, so the
        # credential landed in `scheme` and was echoed verbatim.
        "admin:hunter2@clipfarm.ca https://clipfarm.ca",
        "admin:hunter2@clipfarm.ca ftp://elsewhere.ca",
        "postgres:hunter2@db https://clipfarm.ca",
    ],
)
def test_a_credential_before_a_second_scheme_is_not_echoed(clean_env, value):
    """The scheme is echoed only when it is one this guard would accept.

    Testing the scheme's *shape* instead would repeat the mistake this function
    was rewritten to escape — no textual rule separates a credential from a
    legitimate value, and a shape test is exactly that rule. A closed set of two
    schemes needs no rule at all.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    message = str(exc.value)
    assert "hunter2" not in message.lower()
    assert "'***'" in message


def test_only_a_scheme_this_guard_accepts_is_echoed(clean_env):
    """What separates the closed set from a shape test, which nothing else here
    does — a mutation swapping `scheme.lower() in _ALLOWED_ORIGIN_SCHEMES` for
    `scheme.isalnum()` passed the whole suite without this.

    `hunter2://admin@clipfarm.ca` is a secret pasted where a scheme belongs. It
    is alphanumeric, so a shape test echoes it; it is not http or https, so the
    closed set does not. That is the complement a shape rule always leaves open,
    and the reason this function stopped using one.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors("hunter2://admin@clipfarm.ca")

    message = str(exc.value)
    assert "hunter2" not in message.lower()
    assert "'***'" in message


def test_an_alphabetic_scheme_is_not_echoed_either(clean_env):
    """The companion to the test above, and it exists because that one pinned
    the closed set only against `isalnum()`.

    `s3cret` contains a digit, so `isalpha()` rejects it and the leak would not
    show; `password` does not. Both are secrets pasted where a scheme belongs,
    and neither is http or https.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors("password://admin@clipfarm.ca")

    message = str(exc.value)
    assert "password://" not in message
    assert "'***'" in message


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Credential + trailing slash: independent, so both must be reported.
        ("https://admin:hunter2@clipfarm.ca/", {"user:password", "nothing after it"}),
        # Credential + upper-case scheme.
        ("HTTPS://admin:hunter2@clipfarm.ca", {"user:password", "lower-case"}),
        # Credential + unlatinised host.
        ("https://admin:hunter2@\u043a\u043b\u0438\u043f\u0444\u0430\u0440\u043c.\u0440\u0444",
         {"user:password", "punycode"}),
    ],
)
def test_every_problem_with_an_entry_is_reported_at_once(clean_env, value, expected):
    """An entry can be wrong in more than one independent way, and reporting one
    costs a second refused production boot to discover the other.

    Three rounds tried to fix this by ORDERING the checks. That cannot work:
    with two independent defects, whichever is reported first, fixing it leaves
    the other.

    Two pairs here are not independent and are still reported once: a
    zero-padded default port, handled by ordering the two port checks, and an
    upper-case unicode host, handled by suppressing the case message. An earlier
    version of this docstring said both were handled by ordering.

    A backslash was briefly counted as a third such pair and is not one — see
    `test_a_backslash_does_not_hide_a_real_port_problem`. The number here has
    been wrong in three places at once (this file, config.py, and the PR body),
    which is why the config.py docstring now states the retraction rather than
    just the corrected count.
    """
    problems = _origin_problems(value)

    assert len(problems) == len(expected)
    for fragment in expected:
        assert any(fragment in problem for problem in problems), fragment


def test_a_disallowed_scheme_does_not_withhold_the_rest(clean_env):
    """`ftp://admin:hunter2@clipfarm.ca` parses cleanly — netloc
    `admin:hunter2@clipfarm.ca`, username `admin` — so the credential is
    readable, and withholding it costs a second refused boot.

    The early return exists for entries that cannot be parsed at all
    (`clipfarm.ca`, a bare `https://`), and a disallowed scheme was swept in
    with them.

    Also pins that the scheme itself is not echoed: a secret pasted where a
    scheme belongs is exactly what `_redacted_origin` is for, and a message
    quoting the value would undo it. The first version of this message did
    interpolate it.
    """
    problems = _origin_problems("ftp://admin:hunter2@clipfarm.ca")

    assert len(problems) == 2
    assert any("not http or https" in problem for problem in problems)
    assert any("user:password" in problem for problem in problems)
    assert not any("ftp" in problem for problem in problems)


@pytest.mark.parametrize(
    ("value", "expected_count"),
    [
        ("//clipfarm.ca:8O", 2),      # scheme + invalid port, NOT a case problem
        ("//A@b.ca", 2),              # scheme + userinfo; the capital is in userinfo
        ("//clipfarm.ca/PATH", 2),    # scheme + path; the capitals are in the path
        ("//clipfarm.ca", 1),         # scheme alone
        ("//X.ca", 2),                # scheme + a genuine host capital
    ],
)
def test_a_protocol_relative_entry_is_not_over_reported(clean_env, value, expected_count):
    """These reach the case check only because a disallowed scheme now
    accumulates instead of returning early.

    The check used to slice the raw entry by hand, and `origin.partition("://")`
    returns the WHOLE string when there is no `://` — so the entire entry landed
    in `scheme_text` and every capital anywhere in it became a case problem,
    re-inventing the two defects the check had just been rewritten to avoid.
    Reading `parts.netloc` instead is what fixes it: urlsplit has already
    stripped path, query and fragment, and preserves case.

    `//X.ca` is in the list to prove the fix did not simply switch the check
    off — a genuine host capital is still reported.
    """
    assert len(_origin_problems(value)) == expected_count


def test_idna_does_not_lower_case_an_ascii_label(clean_env):
    """The implied-pair suppression has to be narrower than "the host is
    non-ASCII".

    `"WWW.\u043a\u043b\u0438\u043f\u0444\u0430\u0440\u043c.\u0440\u0444".encode("idna")`
    gives `WWW.xn--80apfehqi4a.xn--p1ai` — IDNA leaves an already-ASCII label
    alone. So suppressing the case message for any non-ASCII host sent the
    operator to a value that is refused again on the next boot, which is the
    two-boot failure this branch exists to end.

    An ASCII capital is the part punycode usually will not fix. Not always: a
    capital INSIDE a non-ASCII label is folded away with the rest of it, since
    the whole label is re-encoded — `"WWWклипфарм.рф".encode("idna")` gives
    `xn--www-8cd3blhkzl6b.xn--p1ai`. That over-reports by one line and costs no
    extra boot.

    An earlier version of this sentence stated the rule as absolute. It was
    retracted in `config.py` and left standing here — the sixth time on this
    branch a correction has reached one of two copies.
    """
    mixed = "https://WWW.\u043a\u043b\u0438\u043f\u0444\u0430\u0440\u043c.\u0440\u0444"
    pure = "https://\u041a\u041b\u0418\u041f\u0424\u0410\u0420\u041c.\u0420\u0424"

    assert len(_origin_problems(mixed)) == 2
    assert len(_origin_problems(pure)) == 1


def test_whitespace_does_not_suppress_the_other_problems(clean_env):
    """`https://admin:hunter2@clip farm.ca/` parses perfectly well — netloc
    `admin:hunter2@clip farm.ca`, username `admin`, path `/` — so whitespace is
    not the "nothing further can be said" case the early returns are for.

    Reporting it alone cost exactly the second refused boot this function's own
    docstring argues against, in the one early return that was never revisited
    when the rest of the function moved to accumulating.
    """
    problems = _origin_problems("https://admin:hunter2@clip farm.ca/")

    assert len(problems) == 3
    assert "whitespace" in problems[0]
    assert any("user:password" in problem for problem in problems)
    assert any("nothing after it" in problem for problem in problems)


@pytest.mark.parametrize(
    "value",
    ["http://[bad ]", "clip farm.ca"],
)
def test_whitespace_is_still_reported_alongside_a_parse_failure(clean_env, value):
    """The genuine early returns carry it too — an entry that cannot be parsed
    still had whitespace, and that is the more actionable of the two.
    """
    problems = _origin_problems(value)

    assert len(problems) == 2
    assert "whitespace" in problems[0]


def test_a_boot_error_names_the_env_var_not_the_field(clean_env, monkeypatch):
    """`loc` carries the FIELD name and the operator sets an environment
    variable. `Settings.env_name_for()` exists for exactly this, the module
    comment at the top of config.py mandates it, and `production_config_error`
    uses it for CF-172's message — this function printed the raw field name
    instead. (`production_config_error` has never called it; `git log -L` on
    that function says so. An earlier version of this docstring credited it, and
    the correction reached config.py and not here.)

    A message whose stated justification is telling the operator which setting
    is wrong should name it the way they set it.
    """
    for name, value in PRODUCTION_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CONDENSE_MODE", "banana")

    with pytest.raises(RuntimeError) as exc:
        _settings_or_boot_error(_env_file=None)

    message = str(exc.value)
    assert "CONDENSE_MODE:" in message
    assert "condense_mode:" not in message


BACKSLASH_MESSAGE = (
    "contains a backslash — browsers read it as `/`, so this is sent as the "
    "part before it and matches no Origin header"
)


@pytest.mark.parametrize(
    ("value", "extra"),
    [
        # A good port with a backslash after it: the ONLY case where deleting
        # the backslash also fixes the port, so nothing else is reported.
        ("https://clipfarm.ca:8443\\", None),
        # A default port, backslash in the netloc: deleting the backslash leaves
        # `:80`, which is still wrong. Reported.
        ("http://clipfarm.ca:80\\", "default port"),
        # A default port, backslash in the PATH: the netloc is clean and the
        # port problem has nothing to do with the backslash at all.
        ("http://clipfarm.ca:80/x\\y", "default port"),
        # A genuinely unparseable port.
        ("https://clipfarm.ca:abc\\", "port must be a number"),
    ],
)
def test_a_backslash_does_not_hide_a_real_port_problem(clean_env, value, extra):
    """urlsplit leaves the backslash in the netloc, so `.port` raised and a
    spurious "not a number" printed beside the backslash message.

    Skipping the port checks whenever a backslash appeared fixed that and lost
    three real problems. It is not a third implied pair: the implication holds
    only where the backslash is in the netloc AND the port is otherwise valid,
    one case of the four below. The port is read from the entry with the
    backslash removed instead, which needs no carve-out.

    The first version of this test did NOT pin that loss, though an earlier
    version of this docstring said it did. It asserted a single entry —
    `https://clipfarm.ca:8443` plus a backslash — which is the one case of four
    where nothing is lost, so it passed against the buggy code and the fixed
    code alike. That is the whole reason the parametrize list below has four
    rows: one example
    cannot distinguish a rule from a coincidence, which is also how the rule it
    was written for came to be wrong.
    """
    problems = _origin_problems(value)

    assert BACKSLASH_MESSAGE in problems
    others = [problem for problem in problems if problem != BACKSLASH_MESSAGE]
    if extra is None:
        assert others == []
    else:
        assert any(extra in problem for problem in others), others


@pytest.mark.parametrize(
    ("with_backslash", "without"),
    [
        ("https://clipfarm.ca:\\", "https://clipfarm.ca:"),
        ("https://\\:8443", "https://:8443"),
        ("https://admin:hunter2@[::1]\\", "https://admin:hunter2@[::1]"),
        ("http://clipfarm.ca:80\\", "http://clipfarm.ca:80"),
        ("https://clipfarm.ca:abc\\", "https://clipfarm.ca:abc"),
        # Early-return paths, where cleaning empties the netloc. These are where
        # the property failed: the backslash message was folded into the tail
        # check, which the early return never reaches, so the entry was refused
        # with nothing saying what to delete.
        ("https://\\", "https://"),
        ("https://\\\\", "https://"),
        ("ftp://\\", "ftp://"),
    ],
)
def test_a_backslash_hides_nothing(clean_env, with_backslash, without):
    """A backslash must be orthogonal to every other check: the entry reports
    whatever it would report without one, plus the backslash itself.

    An earlier fix cleaned the value for the PORT checks only, through a second
    `urlsplit`, and left every other check reading the raw parse. Three then
    cost a second refused boot apiece — the trailing colon, the missing host,
    and an unparseable bracketed netloc that hid a credential. The entry is
    parsed once, cleaned, instead.

    Asserting the relationship rather than a fixed list is what makes this hold
    for checks nobody has written yet.
    """
    expected = set(_origin_problems(without))
    got = set(_origin_problems(with_backslash))

    assert expected <= got, f"lost: {expected - got}"
    assert got - expected == {BACKSLASH_MESSAGE}


def test_production_rejects_a_backslash(clean_env):
    """Browsers treat `\\` as `/` (WHATWG URL), so `https://x.ca\\evil.com` is
    sent as Origin `https://x.ca` and the entry matches nothing — the same
    silent outage as the tab this guard already catches.

    urlsplit does NOT agree with browsers here: it leaves the backslash in the
    netloc, which is why the entry was accepted. No legitimate origin contains
    one.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors("https://clipfarm.ca\\evil.com")

    assert "contains a backslash" in str(exc.value)


@pytest.mark.parametrize(
    ("char", "name"),
    [("\x01", "SOH"), ("\x00", "NUL"), ("\x7f", "DEL")],
)
def test_production_rejects_a_control_character(clean_env, char, name):
    """`str.isspace()` is False for every C0 control and for DEL, and
    `cors_origins_list` strips only whitespace — so a control-bearing entry was
    ACCEPTED and handed to Starlette, matching no Origin header.

    The silent outage this whole guard exists to prevent, admitted by the check
    written to catch it.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(f"https://clip{char}farm.ca")

    assert "invisible or control character" in str(exc.value)


@pytest.mark.parametrize(
    ("char", "name"),
    [("\u200b", "ZWSP"), ("\ufeff", "BOM"), ("\u2060", "WORD JOINER")],
)
def test_a_zero_width_character_is_not_called_a_punycode_problem(
    clean_env, char, name
):
    """These are not `isspace()` either, and being non-ASCII they fell through
    to the punycode branch — so a host that displays as plain ASCII was told to
    "use the xn-- spelling".

    A BOM is a routine paste artefact. That advice is unactionable, and the
    entry needs retyping rather than inspecting, which is why the message is
    separate from the whitespace one.
    """
    problems = _origin_problems(f"https://clip{char}farm.ca")

    assert problems == [
        "contains an invisible or control character — no browser can send this "
        "as an Origin; retype the value rather than editing it"
    ]


@pytest.mark.parametrize(
    "value",
    [
        "https://\u043a\u043b\u0438\u043f.\u0440\u0444/ a",
        "https://\u043a\u043b\u0438\u043f.\u0440\u0444?x= y",
        "https://u er@\u043a\u043b\u0438\u043f.\u0440\u0444",
    ],
)
def test_whitespace_outside_the_host_does_not_hide_a_punycode_problem(
    clean_env, value
):
    """The punycode suppression is gated on the HOST, not the entry.

    Its justification is host-scoped — an invisible character in the host makes
    the host non-ASCII, so the punycode advice would be wrong — but the test was
    entry-scoped, so whitespace anywhere in the entry suppressed a genuine
    punycode problem and cost a second refused boot. The comment said "host"
    and the code said "entry".
    """
    problems = _origin_problems(value)

    assert any("punycode" in problem for problem in problems)
    assert any("whitespace" in problem for problem in problems)


@pytest.mark.parametrize(
    "value",
    [
        "https://cl ip\u200bfarm.ca",          # space and a zero-width space
        "\ufeffhttps://clipfarm.ca two.ca",    # BOM and a space
        "https://clip\x01farm.ca two.ca",      # control and a space
    ],
)
def test_both_invisible_problems_are_reported(clean_env, value):
    """An entry can carry a space AND an invisible character. An `if`/`elif`
    reported only the first, which cost the second refused boot this function
    exists to avoid — and gave the WORSE of the two pieces of advice: "look for
    the space" for a value whose real problem cannot be seen.

    Two independent checks now, which is what the rest of the function does.
    """
    problems = _origin_problems(value)

    assert any("whitespace" in problem for problem in problems)
    assert any("invisible or control" in problem for problem in problems)


def test_a_plain_space_is_not_also_called_invisible(clean_env):
    """The other half: a space is `isprintable()`, so without excluding
    whitespace from the second check it would report both messages for one
    defect — the over-reporting failure, arriving from the fix for
    under-reporting.
    """
    assert _origin_problems("https://clip farm.ca") == [
        "contains whitespace — no browser can send this as an Origin"
    ]


def test_whitespace_does_not_draw_punycode_advice(clean_env):
    """A non-breaking space makes the host non-ASCII, so the punycode check
    fired alongside the whitespace one — and "use the xn-- spelling" is advice
    the operator cannot act on. The fix is to delete the space, after which the
    host may be plain ASCII.

    One refused boot either way, so this is about the instruction being right
    rather than about a wasted deploy.
    """
    assert _origin_problems("https://\xa0clipfarm.ca") == [
        "contains whitespace — no browser can send this as an Origin"
    ]
    # A genuinely unlatinised host, with no whitespace, still gets the advice.
    assert any(
        "punycode" in problem
        for problem in _origin_problems("https://\u043a\u043b\u0438\u043f.\u0440\u0444")
    )


def test_production_rejects_a_percent_escape_in_the_host(clean_env):
    """`clipfarm%2eca` reads as `clipfarm.ca` to whoever pasted it and matches
    nothing, because a browser sends the decoded host in an Origin header. Same
    silent outage as the wildcard.

    It was accepted, while a comment a few lines above the wildcard check
    claimed such entries were "refused either way". They were not — `%2A` was
    refused only by accident, for its capital, and `%2e` sailed through.

    The bracketed form is excluded on purpose: an IPv6 zone id is written
    `%25eth0`, and rejecting it would be a guess about a shape this guard has no
    evidence about.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors("https://clipfarm%2eca")

    assert "percent-escape" in str(exc.value)
    assert _origin_problems("http://[fe80::1%25eth0]") == []
    # With userinfo in front, the bracket is no longer at the start of netloc
    # and `parts.hostname` has had the brackets stripped — so neither obvious
    # source sees an IPv6 literal here. Reading netloc-after-userinfo does.
    # Without this case the fix is unpinned: a mutation reverting it to plain
    # `parts.netloc.startswith("[")` passed the whole suite.
    assert _origin_problems("https://user@[::1%25eth0]") == [
        "carries user:password — RFC 6454 discards userinfo, so this entry "
        "can never match a browser Origin header"
    ]


@pytest.mark.parametrize("value", ["//x.ca/A://b", "//clipfarm.ca?Next=https://x"])
def test_a_later_scheme_separator_is_not_read_as_the_scheme(clean_env, value):
    """`origin.index("://")` finds the FIRST `://` anywhere in the entry, so a
    second one inside a path or query put everything before it into the scheme
    and invented "must be lower-case".

    The scheme now comes from `parts.scheme` — what urlsplit actually parsed —
    sliced back out of the raw entry to recover its case.
    """
    problems = _origin_problems(value)

    assert not any("lower-case" in problem for problem in problems)
    # And the case check still works where there IS a scheme.
    assert _origin_problems("HTTPS://clipfarm.ca") == [
        "must be lower-case — origins are compared as exact strings"
    ]


def test_an_invalid_port_does_not_invent_a_case_problem(clean_env):
    """A capital can only reach the port when the port is invalid, and the
    invalid port is reported on its own.

    Spanning the port in the case check invented "must be lower-case" against
    `clipfarm.ca`, which is already lower-case — telling the operator to fix
    something that is not wrong, in the message they read when production will
    not boot.
    """
    assert _origin_problems("https://clipfarm.ca:80A") == [
        "port must be a number in 1-65535"
    ]


def test_capitals_inside_a_credential_are_not_a_case_problem(clean_env):
    """`https://admin:Hunter2@clipfarm.ca` has capitals only in userinfo, which
    has to go anyway. Reporting "must be lower-case" as well would be advice the
    operator cannot act on separately — so the case check runs on scheme plus
    host[:port] with userinfo removed, not on the whole entry.
    """
    assert _origin_problems("https://admin:Hunter2@clipfarm.ca") == [
        "carries user:password — RFC 6454 discards userinfo, so this entry "
        "can never match a browser Origin header"
    ]


@pytest.mark.parametrize(
    "value",
    [
        "https://clipfarm.ca/",
        "https://*.clipfarm.ca",
        "https://:8443",
        "https://clipfarm.ca:",
        "https://clipfarm.ca:abc",
        "http://clipfarm.ca:80",
    ],
)
def test_a_single_defect_still_reports_exactly_one_problem(clean_env, value):
    """The other half of accumulating: it must not turn one mistake into a list.

    `https://:8443` is the one worth pinning — it has no host AND could pick up
    the trailing-colon message, and `http://x:80` is both a default port and a
    candidate for the padded-port message.
    """
    assert len(_origin_problems(value)) == 1


def test_an_uppercase_credential_costs_one_refused_boot(clean_env):
    """`https://Admin:S3cret@clipfarm.ca` is both upper-case and credential-
    bearing. Reporting the case first sends the operator to
    `https://admin:s3cret@clipfarm.ca`, which the next boot refuses for
    userinfo — two refused production boots, and the first diagnosis is the
    less important of the two problems.

    Third application of the same ordering rule, and the only one that was not
    pinned when it was written: reverting the order passed the whole suite.

    Asserted against `_origin_problem` directly rather than through the
    ValidationError. The first version of this test went through `Settings` and
    failed for a reason worth recording: pydantic middle-truncates the rendered
    message, and `user:password` landed inside the elided part. That is the same
    arithmetic that hid the credential in `input_value=` — here it silently
    defeats an assertion instead.
    """
    problem = _origin_problem("https://Admin:S3cret@clipfarm.ca")

    assert problem is not None
    assert "user:password" in problem
    assert "lower-case" not in problem


def test_an_uppercase_unicode_host_reports_only_the_punycode_problem(clean_env):
    """Writing the host in punycode lower-cases it as a side effect, so the two
    are an implied pair and only one message is worth printing.

    The previous version of this test asserted `"punycode" in str(exc.value)`
    and was named for an ordering the code does not have. It passed while a
    redundant "must be lower-case" line was printed alongside, because a
    substring check cannot see an extra line. Asserting the exact list is what
    makes the implied-pair claim in `_origin_problems` true rather than
    intended.

    A scheme in capitals is NOT implied by the punycode fix and is still
    reported.
    """
    upper = "https://\u041a\u041b\u0418\u041f\u0424\u0410\u0420\u041c.\u0420\u0424"

    assert _origin_problems(upper) == [
        "a browser sends the punycode form of an internationalised host, "
        "so this matches nothing — use the xn-- spelling"
    ]
    assert len(_origin_problems(upper.replace("https", "HTTPS"))) == 2
    # The punycode spelling must still pass, or the guard breaks the deploy it
    # exists to protect.
    assert _production_with_cors("https://xn--80ak6aa92e.com").cors_origins_list


def test_production_rejects_a_raw_unicode_host(clean_env):
    """Browsers send the punycode form in an Origin header, so a raw-unicode
    host allows nobody while looking correct in a dashboard — the same silent
    outage as the wildcard.

    The punycode spelling of the same host must still pass, or the guard breaks
    the deploy it exists to protect.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors("https://\u043a\u043b\u0438\u043f\u0444\u0430\u0440\u043c.\u0440\u0444")

    assert "punycode" in str(exc.value)
    assert _production_with_cors("https://xn--80ak6aa92e.com").cors_origins_list


@pytest.mark.parametrize(
    "value",
    [
        "https://admin:hunter2%40clipfarm.ca",
        "https://admin:hunter2%40clipfarm.ca/x",
    ],
)
def test_a_percent_encoded_at_sign_is_still_userinfo(clean_env, value):
    """A pasted credential that went through a URL-encoder still carries the
    secret, and `%40` is the likelier of the two forms when the value came out
    of a connection string.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    message = str(exc.value)
    assert "hunter2" not in message.lower()
    assert "***" in message


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://clipfarm.ca:443", "default port"),
        ("http://clipfarm.ca:80", "default port"),
        ("https://clipfarm.ca:080", "zero-padded"),
    ],
)
def test_production_rejects_a_port_no_browser_sends(clean_env, value, expected):
    """Same silent class as the trailing slash: looks deliberate in a dashboard,
    matches no Origin header. A browser omits the default port and never pads.

    The padded case has to be read off the raw netloc — `parts.port` has already
    normalised `:080` to 80, so a check written against it cannot see the
    padding at all.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors(value)

    assert expected in str(exc.value)


def test_each_problem_reads_as_a_sentence(clean_env):
    """The messages are standalone clauses, so the line that joins them must not
    supply a verb. `f"{value} is {why}"` produced "… is must be lower-case" and
    "… is an Origin is scheme://host[:port]" for most of them. (A count used to
    stand here and was stale within two commits, so this one does not give one.)

    This is the text an operator reads at the moment production will not boot,
    and this PR goes to the trouble of naming both deploy paths in it.
    """
    with pytest.raises(ValidationError) as exc:
        _production_with_cors("HTTPS://ClipFarm.ca,https://clipfarm.ca/")

    message = str(exc.value)
    assert " is must be" not in message
    assert " is an Origin is " not in message


@pytest.mark.parametrize(
    "value",
    [
        "https://clipfarm.ca",
        "https://clipfarm.ca,https://www.clipfarm.ca",
        "https://clipfarm.ca,http://localhost:3000",   # port must stay valid
        "http://127.0.0.1:3000",
        "http://[::1]:3000",           # IPv6 — the host checks must not break it
        # Bare IPv6, unported. This one pins the `port is not None` guard on the
        # port block: without it, `'[::1]'.rpartition(':')` yields "port text"
        # `1]` and the entry is rejected as zero-padded. Only the *ported* form
        # was listed here, so mutating that guard to `if True:` passed the whole
        # suite while refusing a production boot for a legitimate origin.
        "http://[::1]",
        "https://clipfarm.ca:1",       # boundary — the port checks must not
        "https://clipfarm.ca:65535",   # narrow the range they validate

        "https://clipfarm.ca ,  https://www.clipfarm.ca",  # entries are stripped
    ],
)
def test_production_accepts_ordinary_origin_lists(clean_env, value):
    """The guard must not be the thing that breaks a correct deploy."""
    settings = _production_with_cors(value)
    assert settings.cors_origins_list


def test_the_shape_guard_is_production_only(clean_env):
    """A wildcard outside production is someone's local experiment. The guard
    exists for the value pasted into a dashboard, not to police a laptop."""
    assert _settings(cors_origins="*").cors_origins_list == ["*"]
    assert _settings(environment="development", cors_origins="*").cors_origins_list == ["*"]


def test_the_shape_guard_follows_the_same_environment_spelling_as_cf_172(clean_env):
    """`missing_in_production` normalizes with .strip().lower(); a guard that
    compared the raw string would silently not fire for these.
    """
    for spelling in ("Production", " production", "PRODUCTION"):
        with pytest.raises(ValidationError):
            _settings(
                **{
                    **{k.lower(): v for k, v in PRODUCTION_ENV.items()},
                    "environment": spelling,
                    "cors_origins": "*",
                }
            )


def test_the_unset_guard_reports_before_the_shape_guard(clean_env):
    """Pydantic runs `mode="after"` validators in definition order and the first
    raise short-circuits the rest, so on a box that is both unconfigured and
    misconfigured only one message is ever seen. "You have not finished setting
    this up" is the more useful one, so CF-172's validator is defined first —
    pinned here because source order is doing real work.
    """
    with pytest.raises(ValidationError) as exc:
        _settings(
            **{
                **{k.lower(): v for k, v in PRODUCTION_ENV.items()},
                "supabase_url": "",
                "cors_origins": "*",
            }
        )

    assert "SUPABASE_URL" in str(exc.value)
    assert "allow_credentials" not in str(exc.value)


def test_the_boot_error_carries_no_input_values(clean_env, monkeypatch):
    """The redaction this module does was true of the message it composes and
    not of what an operator actually reads.

    pydantic attaches the whole input mapping to a ValidationError and `str()`
    renders it as `input_value={...}` — every secret the model takes, verbatim,
    middle-truncated. Whether a given secret falls inside the elided middle
    depends on how many fields precede it and how long they are: it is
    arithmetic, not a guarantee. Moving one field to the end of the class puts a
    pasted password in the boot log with **every other test in this file still
    passing**, because they construct Settings directly and pin a key ordering
    unrelated to production's.

    No field positions are quoted here on purpose. Two earlier versions did, a
    review measured a third set, and the numbers move every time anyone adds a
    setting — a load-bearing fact that goes stale on an unrelated commit is
    worse than no fact.

    So this asserts the ordering-independent property — no `input_value=` at
    all — rather than "the secret happens not to appear". The distinction is the
    whole finding.
    """
    for name, value in PRODUCTION_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CORS_ORIGINS", "https://admin:hunter2@clipfarm.ca")

    with pytest.raises(RuntimeError) as exc:
        _settings_or_boot_error(_env_file=None)

    message = str(exc.value)
    assert "input_value" not in message
    assert "hunter2" not in message.lower()
    # The chained original carries the same mapping, so a traceback must not
    # print it either. `raise ... from None` is what sets this.
    assert exc.value.__suppress_context__
    assert exc.value.__cause__ is None
    # Still actionable: the operator learns which variable and why.
    assert "CORS_ORIGINS" in message
    assert "user:password" in message


def test_a_boot_error_still_names_every_problem(clean_env, monkeypatch):
    """Only the messages survive the re-raise, so all of them have to.

    Two CORS problems, which arrive as one pydantic error — the companion test
    below covers several *pydantic* errors, which is the case the `"; ".join`
    exists for and which this one does not reach.
    """
    for name, value in PRODUCTION_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CORS_ORIGINS", "https://clipfarm.ca/,https://*.clipfarm.ca")

    with pytest.raises(RuntimeError) as exc:
        _settings_or_boot_error(_env_file=None)

    message = str(exc.value)
    assert "nothing after it" in message
    assert "wildcard" in message


def test_pydantics_own_prefix_does_not_reach_the_operator(clean_env, monkeypatch):
    """`Value error, ` is pydantic's, and it lands mid-sentence in ours.

    The composed line is the one thing whoever is staring at a failed deploy
    reads, and it opened `Configuration is not usable: Value error, ENVIRONMENT=
    production but ...`. Nothing asserted the leading text before this, which is
    why the wart survived CF-235.

    Both halves are asserted on purpose. A test that only checks the prefix is
    gone passes just as well if the strip ate the message with it, and this
    repository has recent form for assertions that hold for the wrong reason.
    """
    for name, value in PRODUCTION_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CORS_ORIGINS", "https://clipfarm.ca/")

    with pytest.raises(RuntimeError) as exc:
        _settings_or_boot_error(_env_file=None)

    message = str(exc.value)
    assert "Value error," not in message
    assert "ENVIRONMENT=production but CORS_ORIGINS" in message


def test_the_prefix_strip_is_keyed_on_the_type_and_keeps_a_bare_message():
    """The two branches of the strip, driven directly because nothing else can.

    Both of these were written first as end-to-end tests through `Settings`,
    and **both could not fail**: measured, deleting the `type` guard and
    deleting the empty-remainder guard each left the whole suite green. No env
    var this codebase accepts produces either input — `config.py`'s two model
    validators raise from three sites and every one passes non-empty text, so
    a real error is either a `value_error` with text or a pydantic-generated
    message that has no such prefix to lose.

    That is the failure class CF-308 is about, so the errors are constructed
    instead. `_boot_error` takes a `ValidationError` and nothing else, which is
    what makes driving it directly honest rather than a workaround.

    - A non-`value_error` whose message merely *starts* with pydantic's prefix
      must keep it: the strip is a fact about where the text came from, not a
      pattern match on the text.
    - A `value_error` carrying nothing but the prefix must keep it too, or the
      composed line becomes `Configuration is not usable: ` and names no
      problem at all.
    - A `value_error` whose text opens with characters drawn from the prefix
      must lose the prefix and nothing else. `rev, all of it` is every one of
      its leading characters, so `lstrip` in place of `removeprefix` eats them
      and leaves `all of it`. Without this case that substitution survives the
      whole suite, because every message a real setting produces starts with
      `E` — which is not in the set, so the two spellings agree by luck rather
      than by design.
    """
    errors = [
        InitErrorDetails(
            type=PydanticCustomError("not_a_value_error", "Value error, mine"),
            loc=(),
        ),
        InitErrorDetails(type="value_error", loc=(), ctx={"error": ValueError("")}),
        InitErrorDetails(
            type="value_error", loc=(), ctx={"error": ValueError("rev, all of it")}
        ),
    ]
    message = _boot_error(ValidationError.from_exception_data("Settings", errors))

    assert "Value error, mine" in message
    assert "Value error, ; " in message
    assert message.endswith("rev, all of it")


def test_a_boot_error_names_every_pydantic_error_not_just_the_first(
    clean_env, monkeypatch
):
    """`"; ".join(... for error in exc.errors())` was pinned by nothing —
    replacing it with `errors()[0]` passed the whole suite, because every other
    test produces exactly one pydantic error.

    Two FIELD errors are needed. A field failure short-circuits the model
    validator, so pairing a bad CORS value with a bad field yields one error,
    not two — which is why the first attempt at this test proved nothing.

    It also pins that each problem names its field. Taking only `msg` rendered
    `CONDENSE_MODE=banana` as a bare "Input should be 'rules' or 'guarded'":
    true, and useless, since the operator is not told which setting it is about,
    out of dozens. pydantic puts the field on its own line and the first version of
    the wrapper dropped it.
    """
    for name, value in PRODUCTION_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CONDENSE_MODE", "banana")
    monkeypatch.setenv("DEBUG", "notabool")

    with pytest.raises(RuntimeError) as exc:
        _settings_or_boot_error(_env_file=None)

    message = str(exc.value).lower()
    assert "condense_mode" in message
    assert "debug" in message


def test_the_cors_message_points_at_both_deploy_paths():
    """Same requirement as CF-172's message, for the same reason: this one is
    read on Render and on the VPS."""
    message = cors_origins_error(["'*' is bad"])

    assert "DEPLOY_RENDER.md" in message
    assert "DEPLOY.md" in message
    assert ".env.docker" in message

# ── CF-187 — condense_mode is a closed set, checked at load ────────────────
#
# The failure this guards is the quiet kind. `condense_mode` selects the
# keep-window builder, and the worker's fallback treats an unknown name the same
# way it treats a builder that raised: warn once, use "rules". That is right for
# a genuine builder failure and wrong for a typo — `CONDENSE_MODE=Guarded` boots
# clean and then runs the path the operator was trying to leave, one warning per
# game, for as long as nobody reads the logs. A Literal moves that to load.


def test_a_misspelled_condense_mode_fails_at_load(clean_env):
    """Capitalisation is the realistic typo, and it is the one that used to
    survive: pydantic accepted any str, so "Guarded" reached the worker and fell
    through to "rules" because the equality test is case-sensitive.
    """
    clean_env.setenv("CONDENSE_MODE", "Guarded")

    with pytest.raises(Exception) as excinfo:
        Settings(_env_file=None)

    assert "condense_mode" in str(excinfo.value).lower()


def test_an_unknown_condense_mode_fails_at_load(clean_env):
    with pytest.raises(Exception) as excinfo:
        _settings(condense_mode="banana")

    assert "condense_mode" in str(excinfo.value).lower()


def test_both_shipping_modes_still_load(clean_env):
    """The companion to the two above: closing the set must not close it around
    the wrong values. Both names here are dispatched on by
    `tasks._build_condense_windows` and `ml/eval/harness.py`, so a Literal that
    admitted only the default would fail the run it was meant to protect.
    """
    for mode in ("rules", "guarded"):
        assert _settings(condense_mode=mode).condense_mode == mode

    assert _settings().condense_mode == "guarded", "the default must stay guarded"


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


# pip options that take their value as the NEXT argument. Without this list the
# value is read as a package: `--index-url https://x/simple` reported the URL as
# an unpinned install. `--opt=value` needs no entry — it is a single token.
_PIP_OPTS_TAKING_A_VALUE = frozenset(
    {
        "-r", "--requirement",
        "-c", "--constraint",
        "-e", "--editable",
        "-i", "--index-url",
        "--extra-index-url",
        "-f", "--find-links",
        "-t", "--target",
        "-d", "--dest",
        "--prefix", "--root", "--src",
        "--upgrade-strategy",
        "--no-binary", "--only-binary",
        "--config-settings", "--global-option", "--install-option",
        "--proxy", "--trusted-host",
        "--cache-dir", "--log",
        "--timeout", "--retries",
        "--exists-action",
        "--python-version", "--platform", "--abi", "--implementation",
        "--report", "--progress-bar",
    }
)

# Tokens `shlex(punctuation_chars=True)` emits for shell plumbing. The first
# group ends one command and begins another; the second redirects, and swallows
# the token after it.
_SHELL_SEPARATORS = frozenset({"|", "||", "&&", ";", "&", "(", ")"})
_SHELL_REDIRECTS = frozenset({">", ">>", "<", "<<", ">&", "<&", ">|"})

# Install targets that name something exact without carrying an `==`. Flagging
# these would be unsatisfiable advice: there is no version specifier to add to a
# local path, a built artifact, or a VCS URL already fixed at a revision.
_VCS_SCHEMES = ("git+", "hg+", "svn+", "bzr+")
_ARTIFACT_SUFFIXES = (".whl", ".tar.gz", ".tar.bz2", ".zip")


def _shell_commands(script):
    """A shell script as a list of commands, each a list of argument tokens.

    Redirections and their targets are dropped, and pipelines / `&&` / `;` split
    into separate commands, so nothing downstream can mistake shell plumbing for
    packages someone asked pip to install. Splitting on whitespace did exactly
    that: `> /dev/null` yielded `['>', '/dev/null']` and `2>&1 | tee pip.log`
    yielded `['2>&1', '|', 'tee', 'pip.log']`, all reported as unpinned.
    """
    import shlex

    # Join backslash-continued lines before tokenising: a `run: |` block wraps
    # long commands that way, and neither fragment is a command on its own.
    script = script.replace("\\\n", " ")

    commands = []
    for line in script.splitlines():
        if not line.strip():
            continue
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:
            # Unbalanced quotes: not something this guard can read, and not its
            # job to fail the build over — a malformed workflow fails in CI at
            # the step itself, with a better message than this test could give.
            continue

        current = []
        skip_next = False
        for token in tokens:
            if skip_next:
                skip_next = False
                continue
            if token in _SHELL_SEPARATORS:
                commands.append(current)
                current = []
                continue
            if token in _SHELL_REDIRECTS:
                # `2>&1` lexes as `2`, `>&`, `1`: the file descriptor has
                # already been collected, so drop it, then skip the target.
                if current and current[-1].isdigit():
                    current.pop()
                skip_next = True
                continue
            current.append(token)
        commands.append(current)

    return [c for c in commands if c]


def _is_pinned(target):
    """Whether an install target names an exact thing.

    `==` is the usual way. A local path, a built artifact and a VCS URL fixed at
    a revision are equally exact and have no version specifier to add, so
    demanding one would be advice nobody could take.
    """
    if "==" in target:
        return True
    if target in (".", "..") or target.startswith(("./", "../", "/")):
        return True
    if target.startswith(_VCS_SCHEMES):
        # `git+https://host/repo.git@v1.2.3` — pinned iff a revision follows the
        # repo, i.e. there is an `@` past the scheme's own `://`.
        return "@" in target.split("://", 1)[-1]
    return target.endswith(_ARTIFACT_SUFFIXES)


def _pip_install_targets(script):
    """Package arguments of every `pip install` in a shell script.

    Options and their values, `-r`/`-c` file pairs, and the pip/python words
    themselves are dropped; what is left is what pip would resolve to a version.
    Shell tokenising (see `_shell_commands`) is what keeps redirections, pipes
    and option values out of that list.
    """
    import re

    # `pip`, `pip3`, `pip3.11` — all of which install into the same environment
    # and all of which are things people write in a workflow. Not `pipenv`.
    is_pip = re.compile(r"pip3?(\.\d+)?$").fullmatch

    for words in _shell_commands(script):
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
            if word in _PIP_OPTS_TAKING_A_VALUE:
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
    unpinned = sorted({t for _, t in installs if not _is_pinned(t)})

    assert not unpinned, (
        f"ci.yml installs {unpinned} inline without pinning it. Add it to "
        "requirements-tooling.txt with a `==` instead — an unpinned name "
        "resolves to whatever is current on the day the job runs, which is "
        "what CF-92 exists to stop. (A local path, a built artifact and a VCS "
        "URL fixed at a revision already name one exact thing and are allowed "
        "as they are.)"
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # Shell plumbing is not a package list. Each of these reported the
        # bracketed tokens as unpinned installs when this split on whitespace.
        ("pip install -r requirements-tooling.txt > /dev/null", []),        # ['>', '/dev/null']
        ("pip install -r requirements-tooling.txt 2>&1 | tee pip.log", []), # ['2>&1', '|', 'tee', 'pip.log']
        ("pip install -r a.txt >> pip.log 2>&1", []),
        # An option's value is not a package either.
        ("pip install --index-url https://x/simple numpy==1.0", ["numpy==1.0"]),
        ("pip install -i https://x/simple numpy==1.0", ["numpy==1.0"]),
        ("pip install --index-url=https://x/simple numpy==1.0", ["numpy==1.0"]),
        # ...and the real thing still comes through, including after plumbing.
        ("pip install -r a.txt numpy", ["numpy"]),
        ("pip install -r a.txt > /dev/null && pip install numpy", ["numpy"]),
        ("pip install -r a.txt | grep x; pip install numpy", ["numpy"]),
    ],
)
def test_shell_plumbing_is_not_read_as_packages(command, expected):
    """The parser's job is to say what pip would resolve, and only that.

    A false positive here is worse than it sounds: it fails a green build with a
    message telling someone to pin `/dev/null`, which is advice they cannot take
    and will eventually route around by deleting the guard.
    """
    assert list(_pip_install_targets(command)) == expected


@pytest.mark.parametrize(
    ("target", "pinned"),
    [
        ("numpy==1.26.4", True),
        ("numpy", False),
        (".", True),                                   # the repo being built
        ("./api", True),
        ("git+https://h/r.git@v1.2.3", True),          # fixed at a revision
        ("git+https://h/r.git", False),                # tracks a branch head
        ("dist/pkg-1.0-py3-none-any.whl", True),       # one exact file
        ("pkg-1.0.tar.gz", True),
        ("pip", False),                                # `--upgrade pip` is real
    ],
)
def test_what_counts_as_pinned(target, pinned):
    """`==` is the usual spelling, not the only one.

    A local path, a built artifact and a VCS URL at a revision each name one
    exact thing already; demanding a `==` on them would be unsatisfiable. A VCS
    URL with no revision is the opposite case — it follows a branch, so it can
    change CI's answer on an untouched branch exactly as a bare name can.
    """
    assert _is_pinned(target) is pinned


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
