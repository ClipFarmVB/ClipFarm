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

from app.config import settings

logger = logging.getLogger(__name__)

_REDACTED = "[redacted]"

# Header names whose values must never leave the process.
_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "apikey"}


def _secret_values() -> list[str]:
    """Concrete secret strings to strip from any outgoing event text. The
    minimum length guard avoids redacting short/empty config values."""
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
        settings.database_url,
        settings.redis_url,
        settings.celery_broker_url,
        settings.celery_result_backend,
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
