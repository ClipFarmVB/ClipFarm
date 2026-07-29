"""Sentry error monitoring wiring (CF-89).

`init_sentry()` is called once per service process (api, worker). It is a
no-op when ``SENTRY_DSN`` is unset or the ``sentry-sdk`` package isn't
installed, so local and test runs need no Sentry account.

Every event and breadcrumb is scrubbed before it leaves the process: request
auth headers are dropped and any known secret string (Supabase service-role
key, Roboflow key, R2 keys, Modal tokens, JWT secret) is redacted wherever it
appears. ``send_default_pii=False`` and ``max_request_body_size="never"`` keep
user PII and request bodies out of events entirely.
"""
import logging
import os
from typing import Any
from urllib.parse import unquote, urlsplit

from app.config import settings

logger = logging.getLogger(__name__)

_REDACTED = "[redacted]"

# Header names whose values must never leave the process.
_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "apikey"}


_CONNECTION_URL_SETTINGS = (
    "database_url",
    "redis_url",
    "celery_broker_url",
    "celery_result_backend",
)


def _is_shipped_default(setting_name: str, value: str) -> bool:
    """True when a setting still holds the default committed in config.py.

    A value published in the repository is not a secret, and its password
    component is often an ordinary word — the default `database_url` uses
    literally ``password``. Turning that into a scrub pattern would redact the
    word out of every event, mangling exactly the messages this module exists
    to capture (``password authentication failed for user "postgres"``).
    """
    field = type(settings).model_fields.get(setting_name)
    return field is not None and value == field.default


def _url_passwords(named_urls: list[tuple[str, str]]) -> list[str]:
    """Password components extracted from connection URLs.

    The full URLs are already scrubbed, but that only matches a DSN rendered
    *exactly* as configured. The same password re-rendered in another form
    slips through — a DSN rebuilt without the ``+asyncpg`` suffix, a different
    query string, a libpq keyword string, or a driver that logs host and
    password separately. Scrubbing the password component itself covers those.

    Both the raw and percent-decoded forms are returned: a password containing
    reserved characters is encoded inside the URL but usually appears decoded
    when a driver reports it on its own.

    Settings left at their shipped default contribute nothing — see
    ``_is_shipped_default``. The full default URL is still scrubbed, which is
    harmless; it is only the bare password component that is dangerous to
    treat as a secret.
    """
    found: list[str] = []
    for name, url in named_urls:
        if _is_shipped_default(name, url):
            continue
        try:
            password = urlsplit(url).password
        except ValueError:
            # Malformed URL (bad IPv6 literal, stray brackets) — nothing to
            # extract, and this must never raise inside before_send.
            continue
        if not password:
            continue
        found.append(password)
        decoded = unquote(password)
        if decoded != password:
            found.append(decoded)
    return found


def _secret_values() -> list[str]:
    """Concrete secret strings to strip from any outgoing event text. The
    minimum length guard avoids redacting short/empty config values.

    Note the guard applies to bare passwords too. A password shorter than the
    threshold is left unscrubbed rather than risk redacting ordinary words out
    of every error message — short passwords are a credential-hygiene problem,
    not something to fix by making error reports unreadable."""
    named_urls = [(name, getattr(settings, name, "") or "") for name in _CONNECTION_URL_SETTINGS]
    connection_urls = [url for _, url in named_urls]
    candidates = [
        settings.supabase_service_role_key,
        settings.r2_access_key_id,
        settings.r2_secret_access_key,
        settings.modal_token_id,
        settings.modal_token_secret,
        settings.jwt_secret,
        os.environ.get("ROBOFLOW_API_KEY", ""),
        # Connection URLs embed passwords (Supabase DB password, Render Redis
        # password) and routinely appear verbatim in asyncpg/SQLAlchemy/redis
        # connection errors — the most common thing an error tracker sees.
        *connection_urls,
        # ...and the password on its own, for the re-rendered forms above.
        # Settings still at their shipped default are skipped there.
        *_url_passwords(named_urls),
    ]
    return [c for c in candidates if c and len(c) >= 6]


def _scrub(obj: Any, secrets: list[str]) -> Any:
    """Recursively redact sensitive headers and secret substrings."""
    if isinstance(obj, dict):
        result: dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and key.lower() in _SENSITIVE_HEADERS:
                result[key] = _REDACTED
            else:
                result[key] = _scrub(value, secrets)
        return result
    if isinstance(obj, (list, tuple)):
        return [_scrub(item, secrets) for item in obj]
    if isinstance(obj, str):
        scrubbed = obj
        for secret in secrets:
            if secret in scrubbed:
                scrubbed = scrubbed.replace(secret, _REDACTED)
        return scrubbed
    return obj


def _before_send(event: Any, _hint: Any) -> Any:
    return _scrub(event, _secret_values())


def _before_breadcrumb(crumb: Any, _hint: Any) -> Any:
    return _scrub(crumb, _secret_values())


def init_sentry(component: str) -> bool:
    """Initialize Sentry for a service process. ``component`` is "api" or
    "worker" and selects the right integration + tags the events. Returns
    True when Sentry was actually initialized."""
    dsn = settings.sentry_dsn
    if not dsn:
        logger.info("Sentry disabled (no SENTRY_DSN) for %s", component)
        return False

    try:
        import sentry_sdk
    except ImportError:
        logger.warning("sentry-sdk not installed; %s monitoring disabled", component)
        return False

    integrations: list[Any] = []
    try:
        if component == "worker":
            from sentry_sdk.integrations.celery import CeleryIntegration

            integrations.append(CeleryIntegration())
        else:
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            from sentry_sdk.integrations.starlette import StarletteIntegration

            integrations.extend([StarletteIntegration(), FastApiIntegration()])
    except ImportError:
        # Framework extras missing — core exception capture still works.
        logger.warning("Sentry framework integration unavailable for %s", component)

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.sentry_environment,
        release=settings.sentry_release or None,
        integrations=integrations,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        max_request_body_size="never",
        before_send=_before_send,
        before_breadcrumb=_before_breadcrumb,
    )
    sentry_sdk.set_tag("service", component)
    logger.info("Sentry initialized for %s (env=%s)", component, settings.sentry_environment)
    return True
