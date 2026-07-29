"""Scrubbing tests for the Sentry wiring (CF-89 follow-up).

Covers the gap flagged as non-blocking on #107: the full connection URLs were
scrubbed, but the same password re-rendered in another form was not.

Imports only `app.observability`, which pulls in `app.config` — no database,
no network, no sentry-sdk required.
"""

from app import observability


def test_url_passwords_extracts_component():
    urls = ["postgresql+asyncpg://postgres.abc:s3cr3t-pw-value@db.example.com:5432/postgres"]
    assert "s3cr3t-pw-value" in observability._url_passwords(urls)


def test_url_passwords_returns_both_encoded_and_decoded_forms():
    # A password with reserved characters is percent-encoded inside the URL but
    # typically appears decoded when a driver reports it on its own.
    urls = ["redis://:p%40ss%2Fword%21@redis.example.com:6379/0"]
    found = observability._url_passwords(urls)
    assert "p%40ss%2Fword%21" in found, "raw (still-encoded) form missing"
    assert "p@ss/word!" in found, "decoded form missing"


def test_url_passwords_ignores_urls_without_one():
    urls = ["redis://redis:6379/0", "postgresql://user@host/db", ""]
    assert observability._url_passwords(urls) == []


def test_url_passwords_survives_a_malformed_url():
    # Must never raise: this runs inside before_send, and an exception there
    # would break error reporting itself.
    assert observability._url_passwords(["postgresql://u:p@[not-an-ipv6/db"]) == []


def test_scrub_redacts_a_password_rendered_outside_its_original_url():
    """The regression this change exists for.

    The configured DSN carries `+asyncpg`; the leaked string does not. Matching
    whole URLs misses it, so the password has to be a secret in its own right.
    """
    configured = "postgresql+asyncpg://postgres.abc:hunter2-hunter2@db.example.com:5432/postgres"
    secrets = [configured, *observability._url_passwords([configured])]

    leaked = "could not connect: postgresql://postgres.abc:hunter2-hunter2@db.example.com:5432/postgres"
    scrubbed = observability._scrub(leaked, secrets)

    assert "hunter2-hunter2" not in scrubbed
    assert observability._REDACTED in scrubbed


def test_scrub_reaches_into_nested_structures():
    configured = "redis://:top-secret-redis-pw@redis.example.com:6379/0"
    secrets = [configured, *observability._url_passwords([configured])]

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
