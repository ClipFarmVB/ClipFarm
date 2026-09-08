"""Scrubbing tests for the Sentry wiring (CF-89 follow-up).

Covers the gap flagged as non-blocking on #107: the full connection URLs were
scrubbed, but the same password re-rendered in another form was not.

Imports only `app.observability`, which pulls in `app.config` — no database,
no network, no sentry-sdk required.
"""

import pytest

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


@pytest.fixture(autouse=True)
def _secret_cache_cleared():
    """Clear the `_secret_values` cache around every test in this file.

    **Autouse deliberately.** The hazard is not this test — it is the next one
    that patches settings and forgets, and `api/tests/` has no `conftest.py` to
    catch that. Autouse is the only version of this fixture that protects a test
    nobody has written yet.

    It costs nothing measurable and breaks nothing: the one test that reads
    `cache_info()` calls `cache_clear()` itself immediately before asserting
    `misses == 1`, and says in its own docstring that this makes it independent
    of whatever ran before. An earlier version of the PR body claimed two tests
    read `cache_info()` and that this blocked autouse. One test reads it, and it
    is immune, so nothing blocked it.

    **The teardown clear is the load-bearing half.** The test below calls
    `_secret_values()` while settings are patched, so the real `lru_cache` ends
    the test holding a fake secret; without this fixture that value is what the
    next caller gets. Today the leak is *masked* — `test_secret_values_cache_can_be_cleared`
    further down the file happens to clear it — so deleting this fixture leaves
    the suite green while reintroducing the bug, which is why it is a fixture
    with a reason rather than two bare calls.

    Parameter order does not matter, and an earlier version of this docstring
    said it did: it claimed listing this before `monkeypatch` mattered because
    "clearing while a patch is live would poison the cache". `cache_clear()`
    only empties — it never recomputes — so that hazard does not exist, and
    swapping the parameters changes nothing. Recorded because the wrong reason
    for a right fixture is what gets the fixture removed.
    """
    observability._secret_values.cache_clear()
    yield
    observability._secret_values.cache_clear()


def test_secret_values_applies_the_minimum_length_guard(monkeypatch):
    """A short secret must not become a scrub pattern — redacting a 3-character
    string would gut every error message that happens to contain it.

    This asserts an exclusion, which needs a short secret to exist. The version
    this replaces asserted `all(len(v) >= 6 ...)` over the *shipped defaults*,
    where every candidate is either empty — dropped by the `if c` half, which
    survives deleting the length guard — or a connection URL far longer than the
    threshold. So it passed with the guard deleted, in CI and on any
    default-config machine (CF-308, #358).

    No length is quoted for those URLs on purpose. An earlier version named one,
    which was wrong for one of the four defaults — and naming the right ones
    here would be the same mistake, since they are facts about shipped config
    that nothing in this file pins. "Longer than the threshold" is all the
    argument needs and all that can be relied on.

    Every value this test asserts on is patched rather than read off the
    ambient config — the connection URLs in the result are still shipped
    defaults, and nothing here asserts about them:
    `Settings` loads `api/.env` if one exists, so an assertion about a value
    this test did not set is an assertion about the developer's machine. The
    comment on `test_postgres_auth_error_survives_scrubbing_under_defaults`
    records that biting once already.
    """
    monkeypatch.setattr(observability.settings, "r2_access_key_id", "abc")
    monkeypatch.setattr(observability.settings, "jwt_secret", "long-enough-secret")
    # The threshold's own two sides. Without these only 3 and 18 characters are
    # pinned, and the guard could then sit anywhere in 4..18 unnoticed —
    # measured, not reasoned: with just those two, >= 4, 5, 6, 7, 8, 12 and 18
    # all pass and only >= 19 fails. An earlier version of this comment said
    # "4..6", understating the gap by twelve — and the >= 7 and >= 8 mutation
    # rows that contradict it live in the PR, not in this file, which is why
    # nothing here caught it.
    monkeypatch.setattr(observability.settings, "modal_token_id", "12345")
    monkeypatch.setattr(observability.settings, "modal_token_secret", "123456")

    values = observability._secret_values()

    # `values` is a tuple, so `in` is exact equality rather than a substring
    # test — which matters here, since "12345" is a prefix of "123456".
    assert "12345" not in values, (
        "a 5-character secret became a scrub pattern; the guard admits one "
        "character below its stated threshold of 6 (CF-308, #358)."
    )
    assert "123456" in values, (
        "a 6-character secret was dropped; the guard excludes the shortest "
        "value it is supposed to admit (CF-308, #358)."
    )
    assert "abc" not in values, (
        "a 3-character secret became a scrub pattern. The `len(c) >= 6` guard in "
        "observability._secret_values is what keeps short config values from "
        "redacting ordinary words out of every error report (CF-308, #358)."
    )
    # The control: without it, a `_secret_values` that returned nothing at all
    # would satisfy the exclusion above and this test would pass while scrubbing
    # had stopped entirely.
    assert "long-enough-secret" in values, (
        "a secret over the length threshold is missing from the scrub patterns, "
        "so nothing is being redacted (CF-308, #358)."
    )


def test_every_secret_source_reaches_the_scrub_patterns(monkeypatch):
    """Each candidate source, pinned individually.

    The exclusions in the test above assert that a short value is *absent*, and
    absence has two causes: the guard rejected it, or the setting is not a
    candidate at all. That made them satisfiable by deleting the very settings
    they name — measured, not reasoned: dropping `r2_access_key_id` and
    `modal_token_id` from the candidate list and weakening the guard to
    `>= 4` left all eighteen tests in this file green. The guard CF-308 exists
    to pin moved by two characters with nothing red.

    So this pins the wiring the other test's exclusions depend on. Every value
    is long enough to clear the threshold, distinct, and patched rather than
    read off the ambient config, so a missing one names the source that dropped
    it rather than reporting a length failure a second time.
    """
    scalars = {
        "supabase_service_role_key": "scrub-supabase-service-role-key",
        "r2_access_key_id": "scrub-r2-access-key-id",
        "r2_secret_access_key": "scrub-r2-secret-access-key",
        "modal_token_id": "scrub-modal-token-id",
        "modal_token_secret": "scrub-modal-token-secret",
        "jwt_secret": "scrub-jwt-secret",
    }
    for name, value in scalars.items():
        monkeypatch.setattr(observability.settings, name, value)
    monkeypatch.setenv("ROBOFLOW_API_KEY", "scrub-roboflow-api-key")

    # The four connection-URL settings, written out rather than read from
    # `_CONNECTION_URL_SETTINGS`. Two earlier versions of this got it wrong in
    # opposite ways. The first patched `database_url` alone and claimed it
    # "covers the last two entries at once" — true of the two *lines* in
    # `candidates`, false of the four *sources* they iterate, so deleting
    # `redis_url`, `celery_broker_url` or `celery_result_backend` left the
    # whole api suite green. The second built this dict *from* the tuple, which
    # is worse: a deleted name is then neither patched nor asserted, so all
    # four deletions passed. A test that enumerates the thing under test cannot
    # see the thing under test shrink.
    url_settings = ("database_url", "redis_url",
                    "celery_broker_url", "celery_result_backend")
    assert set(observability._CONNECTION_URL_SETTINGS) == set(url_settings), (
        "the connection-URL settings changed. Add the new one here with its own "
        "patched value — this list is deliberately not read from the module, so "
        "that a source dropped from it fails rather than disappearing quietly "
        "(CF-308, #358)."
    )
    urls = {name: f"postgresql+asyncpg://postgres.abc:scrub-pw-{name}@db.example.com:5432/x"
            for name in url_settings}
    for name, url in urls.items():
        monkeypatch.setattr(observability.settings, name, url)

    values = observability._secret_values()

    for name, value in scalars.items():
        assert value in values, (
            f"settings.{name} is not a scrub pattern, so its value would appear "
            f"verbatim in an error report (CF-308, #358)."
        )
    assert "scrub-roboflow-api-key" in values, (
        "the ROBOFLOW_API_KEY environment variable is not a scrub pattern "
        "(CF-308, #358)."
    )
    for name, url in urls.items():
        assert url in values, (
            f"settings.{name} is not a scrub pattern. Connection URLs are the "
            f"most common thing an error tracker sees (CF-308, #358)."
        )
        assert f"scrub-pw-{name}" in values, (
            f"settings.{name}'s password is not a scrub pattern on its own, so "
            f"a re-rendered form of that URL would leak it (CF-308, #358)."
        )

    # The drift guard for the scalars, which have no named tuple to compare
    # against the way the URLs do — they are a literal list inside
    # `_secret_values`. Equality rather than containment, so a *new* candidate
    # source shows up here as an unexpected pattern and has to be given its own
    # assertion above, instead of joining the list unpinned. That asymmetry is
    # this file's own recurring failure — one surface covered and the identical
    # one beside it not — so it is closed rather than noted.
    expected = (set(scalars.values()) | {"scrub-roboflow-api-key"}
                | set(urls.values()) | {f"scrub-pw-{name}" for name in urls})
    assert set(values) == expected, (
        "the set of scrub patterns is not the set this test patched. Extra "
        f"{sorted(set(values) - expected)} means a candidate source is not "
        f"pinned here; missing {sorted(expected - set(values))} means one "
        "stopped being scrubbed (CF-308, #358)."
    )


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


def test_published_password_is_exempt_when_percent_encoded():
    """The exemption matches the decoded form too.

    `%70assword` decodes to `password`. Checking only the raw value let the
    decoded form through as a "secret", which then redacted the word out of every
    event — the mangling the exemption exists to prevent, reached by writing the
    same published password a different way.
    """
    encoded_dsn = "postgresql+asyncpg://postgres:%70assword@localhost:5432/clipfarm"
    found = observability._url_passwords([("database_url", encoded_dsn)])
    assert found == [], "an encoding of a published password is still that password"

    message = 'password authentication failed for user "postgres"'
    assert observability._scrub(message, tuple(found)) == message


def test_encoding_a_real_password_still_yields_both_forms():
    # The encoded-form exemption must not swallow genuine secrets.
    encoded_dsn = "postgresql+asyncpg://postgres:s3cr3t%2Dvalue@db.example.com:5432/x"
    found = observability._url_passwords([("database_url", encoded_dsn)])
    assert "s3cr3t%2Dvalue" in found, "raw (still-encoded) form missing"
    assert "s3cr3t-value" in found, "decoded form missing"
