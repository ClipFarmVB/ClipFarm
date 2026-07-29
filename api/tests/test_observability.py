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


def test_postgres_auth_error_survives_scrubbing_under_defaults():
    """The user-visible symptom of the bug.

    With the default DSN in play, `password authentication failed ...` must come
    through intact rather than as `[redacted] authentication failed ...`.
    """
    message = 'password authentication failed for user "postgres"'
    assert observability._scrub(message, observability._secret_values()) == message
