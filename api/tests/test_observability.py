"""Scrubbing tests for the Sentry wiring (CF-89 follow-up).

Covers the gap flagged as non-blocking on #107: the full connection URLs were
scrubbed, but the same password re-rendered in another form was not.

Imports only `app.observability`, which pulls in `app.config` — no database,
no network, no sentry-sdk required.
"""

from app import observability


def test_url_passwords_extracts_component():
    urls = [("custom_url", "postgresql+asyncpg://postgres.abc:s3cr3t-pw-value@db.example.com:5432/postgres")]
    assert "s3cr3t-pw-value" in observability._url_passwords(urls)


def test_url_passwords_returns_both_encoded_and_decoded_forms():
    # A password with reserved characters is percent-encoded inside the URL but
    # typically appears decoded when a driver reports it on its own.
    urls = [("custom_url", "redis://:p%40ss%2Fword%21@redis.example.com:6379/0")]
    found = observability._url_passwords(urls)
    assert "p%40ss%2Fword%21" in found, "raw (still-encoded) form missing"
    assert "p@ss/word!" in found, "decoded form missing"


def test_url_passwords_ignores_urls_without_one():
    urls = [("a", "redis://redis:6379/0"), ("b", "postgresql://user@host/db"), ("c", "")]
    assert observability._url_passwords(urls) == []


def test_url_passwords_survives_a_malformed_url():
    # Must never raise: this runs inside before_send, and an exception there
    # would break error reporting itself.
    assert observability._url_passwords([("custom_url", "postgresql://u:p@[not-an-ipv6/db")]) == []


def test_scrub_redacts_a_password_rendered_outside_its_original_url():
    """The regression this change exists for.

    The configured DSN carries `+asyncpg`; the leaked string does not. Matching
    whole URLs misses it, so the password has to be a secret in its own right.
    """
    configured = "postgresql+asyncpg://postgres.abc:hunter2-hunter2@db.example.com:5432/postgres"
    secrets = [configured, *observability._url_passwords([("custom_url", configured)])]

    leaked = "could not connect: postgresql://postgres.abc:hunter2-hunter2@db.example.com:5432/postgres"
    scrubbed = observability._scrub(leaked, secrets)

    assert "hunter2-hunter2" not in scrubbed
    assert observability._REDACTED in scrubbed


def test_scrub_reaches_into_nested_structures():
    configured = "redis://:top-secret-redis-pw@redis.example.com:6379/0"
    secrets = [configured, *observability._url_passwords([("custom_url", configured)])]

    event = {
        "message": "boom",
        "extra": {"dsn_parts": ["host=redis.example.com", "password=top-secret-redis-pw"]},
    }
    scrubbed = observability._scrub(event, secrets)

    assert "top-secret-redis-pw" not in repr(scrubbed)


def test_scrub_still_drops_sensitive_headers():
    event = {"request": {"headers": {"Authorization": "Bearer abc", "Accept": "application/json"}}}
    scrubbed = observability._scrub(event, [])

    assert scrubbed["request"]["headers"]["Authorization"] == observability._REDACTED
    assert scrubbed["request"]["headers"]["Accept"] == "application/json"


def test_secret_values_applies_the_minimum_length_guard():
    # Short values must not become scrub patterns — redacting a 3-character
    # string would gut every error message that happens to contain it.
    assert all(len(value) >= 6 for value in observability._secret_values())


# --- caching (raised on #131 review) -----------------------------------------
# before_send AND before_breadcrumb call _secret_values() on every invocation,
# and breadcrumbs are high-frequency — with the framework integrations on,
# roughly every log record, outbound request, and DB query. The work is now four
# URL parses plus percent-decoding, so it must not run per breadcrumb.


def test_secret_values_is_cached_and_returns_a_tuple():
    first = observability._secret_values()

    assert isinstance(first, tuple), "must be immutable — the value is shared across calls"
    assert observability._secret_values() is first, "expected one evaluation, not one per event"


def test_secret_values_cache_can_be_cleared():
    """The escape hatch for a test that needs to change settings mid-run.

    cache_clear() resets the counters, so asserting a single miss straight after
    is independent of whatever ran before this test.
    """
    observability._secret_values()
    observability._secret_values.cache_clear()

    observability._secret_values()
    assert observability._secret_values.cache_info().misses == 1


# --- regression: a shipped default must not become a scrub pattern -----------
# Reported on #131. The default `database_url` in config.py carries the literal
# password `password`, which clears the >= 6 length guard. Treating it as a
# secret turns an ordinary English word into a global redaction pattern and
# mangles the very messages this module exists to capture.


def test_shipped_default_password_is_not_treated_as_a_secret():
    default_dsn = type(observability.settings).model_fields["database_url"].default
    assert "password" in default_dsn, "test assumes the shipped default embeds this password"

    found = observability._url_passwords([("database_url", default_dsn)])
    assert found == [], "a password published in config.py is not a secret"


def test_a_real_password_is_still_extracted_for_the_same_setting():
    # Guard against over-correcting: only the *default* is exempt.
    custom = "postgresql+asyncpg://postgres:aA9-real-db-secret@db.example.com:5432/postgres"
    assert "aA9-real-db-secret" in observability._url_passwords([("database_url", custom)])


# --- _is_shipped_default's own contract --------------------------------------
# Raised on #131: the frozenset subsumes every case this function currently
# catches, so nothing exercised it and deleting it left the suite green. Of the
# four connection settings only `database_url` has a default with a password,
# and that password (`password`) is a frozenset member; the other three exit at
# the `if not password` guard. It's kept as insurance for a future default whose
# password isn't listed, so these pin that path directly.


def test_is_shipped_default_recognises_the_committed_value():
    default_dsn = type(observability.settings).model_fields["database_url"].default
    assert observability._is_shipped_default("database_url", default_dsn) is True
    assert (
        observability._is_shipped_default("database_url", "postgresql://u:pw@host/db")
        is False
    )
    # Unknown setting names must not raise, and must not be treated as defaults.
    assert observability._is_shipped_default("not_a_setting", "anything") is False


def test_shipped_default_is_exempt_even_when_its_password_is_not_published(monkeypatch):
    """The insurance path the frozenset can't cover.

    Simulates a future config.py default whose password is a real-looking string
    rather than one of the published local-dev words. Only `_is_shipped_default`
    can exempt that, so this fails if the function is removed.
    """
    field = type(observability.settings).model_fields["redis_url"]
    future_default = "redis://:n0t-a-published-word@localhost:6379/0"
    monkeypatch.setattr(field, "default", future_default)

    password = "n0t-a-published-word"
    assert password not in observability._PUBLISHED_LOCAL_PASSWORDS
    assert observability._url_passwords([("redis_url", future_default)]) == []

    # Same setting, a value that is NOT the default: still extracted.
    other = "redis://:n0t-a-published-word@prod.example.com:6379/0"
    assert password in observability._url_passwords([("redis_url", other)])


def test_postgres_auth_error_survives_scrubbing_under_defaults():
    """The user-visible symptom of the bug.

    With the default DSN in play, `password authentication failed ...` must come
    through intact rather than as `[redacted] authentication failed ...`.

    Secrets are derived from the committed defaults rather than from
    `_secret_values()`, which reflects whatever the ambient environment produced.
    config.py sets `env_file=".env"` and CLAUDE.md tells developers to run these
    from `api/`, so a developer with an `api/.env` would otherwise hit a spurious
    failure here — this passed in CI only because the runner has no `.env`.
    """
    fields = type(observability.settings).model_fields
    defaults = [
        (name, fields[name].default)
        for name in observability._CONNECTION_URL_SETTINGS
    ]
    secrets = tuple(observability._url_passwords(defaults))
    message = 'password authentication failed for user "postgres"'
    assert observability._scrub(message, secrets) == message


def test_local_compose_password_is_not_treated_as_a_secret():
    """The Option B DSN in .env.docker.example is published, so its password is
    not a secret — and `postgres` is a substring of `postgresql`, so scrubbing it
    would render every mention of the dialect as `[redacted]ql`.

    Distinct from the config.py default (`postgres:password@localhost`), so the
    whole-URL comparison in `_is_shipped_default` does not catch this one.
    """
    compose_dsn = "postgresql+asyncpg://postgres:postgres@db:5432/clipfarm"
    found = observability._url_passwords([("database_url", compose_dsn)])
    assert found == [], "a password published in .env.docker.example is not a secret"

    message = 'relation "games" does not exist (postgresql 16)'
    assert observability._scrub(message, tuple(found)) == message
