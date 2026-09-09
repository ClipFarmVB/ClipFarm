"""The anonymous-read limiter (CF-186, #189).

#189's acceptance line is "if B is throttled, a test demonstrates the control
engaging". That is `test_the_call_that_exceeds_the_limit_is_refused` and, over
the wire, `test_ratelimit_wiring.py`.

Driven through `MemoryBackend` with an injected clock, the way
`ml/pipeline` tests drive `GameProgress`: the window arithmetic is the part
worth pinning and it should not need a `sleep`. The Redis backend is covered
separately against a stub client — those two tests pin *which commands it
issues*, not that Redis honours them, and say so.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

from app.config import settings
from app.services import ratelimit
from app.services.ratelimit import (
    MemoryBackend,
    Policy,
    RedisBackend,
    client_ip,
    rate_limit,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRequest:
    """Only what client_ip touches: headers and a peer.

    A real `Headers`, not a dict, because the interesting case is a REPEATED
    header. HTTP allows `X-Forwarded-For` to appear more than once and nothing
    merges the lines, so a dict-shaped fake cannot express the case where a
    caller sends their own line and a proxy appends a second one — which is
    exactly the bypass `client_ip` has to survive.

    `forwarded` takes a string for the ordinary single-line case, or a list for
    one entry per header line.
    """

    def __init__(
        self,
        peer: str = "198.51.100.9",
        forwarded: str | list[str] | None = None,
    ) -> None:
        lines = [forwarded] if isinstance(forwarded, str) else (forwarded or [])
        self.headers = Headers(
            raw=[(b"x-forwarded-for", line.encode()) for line in lines]
        )
        self.client = type("C", (), {"host": peer})()


TEST_POLICY = Policy("test", "rate_limit_profile_per_minute", window_seconds=60)


@pytest.fixture
def limiter(monkeypatch):
    """A limiter over a fresh MemoryBackend with a controllable clock."""
    clock = FakeClock()
    backend = MemoryBackend(clock=clock)
    monkeypatch.setattr(ratelimit, "_backend", backend)
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 0)
    monkeypatch.setattr(settings, "rate_limit_profile_per_minute", 3)
    return clock, backend, rate_limit(TEST_POLICY)


def call(dep, request, viewer_id=None):
    """Drive the dependency to completion.

    `asyncio.run` rather than an async test function: this suite installs no
    pytest-asyncio, so an async test is collected, skipped with a warning, and
    then REPORTED AS A PASS — the silent-skip failure this repo guards against
    elsewhere. test_clip_download.py drives its route the same way.
    """
    return asyncio.run(dep(request, viewer_id))


# ── the budget ───────────────────────────────────────────────────────────────

def test_a_caller_under_the_limit_is_allowed(limiter):
    _clock, _backend, dep = limiter
    request = FakeRequest()
    for _ in range(3):
        assert call(dep, request) is None


def test_the_call_that_exceeds_the_limit_is_refused(limiter):
    _clock, _backend, dep = limiter
    request = FakeRequest()
    for _ in range(3):
        call(dep, request)
    with pytest.raises(HTTPException) as exc:
        call(dep, request)
    assert exc.value.status_code == 429


def test_a_refusal_is_429_with_a_positive_retry_after(limiter):
    # Decision recorded on get_profile: 429 with Retry-After, not the 404 that
    # leaks less. Against a per-caller counter the two say the same thing about
    # whether a handle exists, so honesty is free.
    _clock, _backend, dep = limiter
    request = FakeRequest()
    for _ in range(4):
        try:
            call(dep, request)
        except HTTPException as e:
            assert e.headers is not None
            assert int(e.headers["Retry-After"]) > 0
            break
    else:
        pytest.fail("never refused")


def test_the_window_rolls_over(limiter):
    clock, _backend, dep = limiter
    request = FakeRequest()
    for _ in range(3):
        call(dep, request)
    with pytest.raises(HTTPException):
        call(dep, request)

    clock.advance(60)
    # A fresh window, not a permanently spent one. Without EXPIRE ... NX on the
    # Redis side this is exactly what breaks.
    assert call(dep, request) is None


def test_two_addresses_do_not_share_a_budget(limiter):
    _clock, _backend, dep = limiter
    for _ in range(3):
        call(dep, FakeRequest(peer="203.0.113.1"))
    assert call(dep, FakeRequest(peer="203.0.113.2")) is None


def test_a_signed_in_caller_spends_their_own_budget_not_the_addresss(limiter):
    # The office/NAT case, and the reason the key is viewer-or-IP: two people
    # behind one address must not throttle each other.
    _clock, _backend, dep = limiter
    request = FakeRequest(peer="203.0.113.7")
    for _ in range(3):
        call(dep, request, uuid.uuid4())
    assert call(dep, request, uuid.uuid4()) is None
    # ...and the address itself is still untouched by either of them.
    assert call(dep, request) is None


def test_two_policies_do_not_share_a_budget(monkeypatch):
    # Without the policy name in the key, /share and /users/{handle} would spend
    # each other's budget — and the tighter of the two would win.
    monkeypatch.setattr(ratelimit, "_backend", MemoryBackend(clock=FakeClock()))
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 0)
    monkeypatch.setattr(settings, "rate_limit_profile_per_minute", 2)
    monkeypatch.setattr(settings, "rate_limit_share_per_minute", 2)
    first = rate_limit(Policy("profile", "rate_limit_profile_per_minute"))
    second = rate_limit(Policy("share", "rate_limit_share_per_minute"))
    request = FakeRequest()

    for _ in range(2):
        call(first, request)
    with pytest.raises(HTTPException):
        call(first, request)
    assert call(second, request) is None


def test_the_limit_is_read_from_settings_not_baked_in(limiter):
    # So an incident is handled from the environment rather than a deploy.
    _clock, _backend, dep = limiter
    request = FakeRequest()
    for _ in range(3):
        call(dep, request)
    with pytest.raises(HTTPException):
        call(dep, request)

    settings.rate_limit_profile_per_minute = 99
    assert call(dep, request) is None


# ── fail open ────────────────────────────────────────────────────────────────

class BoomBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def hit(self, key: str, window_seconds: int):
        self.calls += 1
        raise RuntimeError("redis is down")


def test_a_backend_that_raises_allows_the_request(monkeypatch):
    """CLAUDE.md's code posture, on the route that serves footage.

    What this protects is a cost and enumeration-rate concern, not an
    authorization boundary — access.py decides who may read what and never
    consults Redis. Failing closed would turn a cache blip into a total outage
    of the public read surface and buy no confidentiality at all.
    """
    monkeypatch.setattr(ratelimit, "_backend", BoomBackend())
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    dep = rate_limit(TEST_POLICY)
    assert call(dep, FakeRequest()) is None


def test_a_backend_outage_does_not_log_once_per_request(monkeypatch, caplog):
    # A failing dependency that re-logs on every call buries the cause it is
    # reporting. One line a minute is enough to see it.
    monkeypatch.setattr(ratelimit, "_backend", BoomBackend())
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(ratelimit, "_last_warned_at", 0.0)
    dep = rate_limit(TEST_POLICY)
    with caplog.at_level("WARNING", logger="app.services.ratelimit"):
        for _ in range(50):
            call(dep, FakeRequest())
    warnings = [r for r in caplog.records if "rate limit backend" in r.message]
    assert len(warnings) == 1


def test_the_kill_switch_does_not_even_reach_the_backend(monkeypatch):
    backend = BoomBackend()
    monkeypatch.setattr(ratelimit, "_backend", backend)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    dep = rate_limit(TEST_POLICY)
    assert call(dep, FakeRequest()) is None
    # Not just "the request passed" — a disabled limiter must cost nothing.
    assert backend.calls == 0


# ── which address is the caller's ────────────────────────────────────────────

def test_the_peer_is_the_client_when_no_proxy_is_trusted(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 0)
    assert client_ip(FakeRequest(peer="192.0.2.5")) == "192.0.2.5"


def test_a_spoofed_forwarded_header_is_ignored_when_no_hop_is_trusted(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 0)
    request = FakeRequest(peer="192.0.2.5", forwarded="1.1.1.1, 2.2.2.2")
    assert client_ip(request) == "192.0.2.5"


def test_one_trusted_hop_reads_the_forwarded_client(monkeypatch):
    # Render. Each proxy appends the address it received the connection from,
    # and the last proxy's own address is the peer rather than a header entry —
    # so with exactly one trusted hop the client is the last entry.
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 1)
    request = FakeRequest(peer="10.0.0.1", forwarded="203.0.113.7")
    assert client_ip(request) == "203.0.113.7"


def test_a_caller_cannot_pick_their_bucket_by_prepending_an_address(monkeypatch):
    """The spoofing case, and the reason the scan counts from the RIGHT.

    A caller can put anything in X-Forwarded-For; the trusted proxy then
    *appends* the address it actually saw. So with one trusted hop the real
    client is the LAST entry, and the entries before it are attacker-controlled
    text. Reading from the left — `parts[0]` — hands every caller a bucket of
    their own choosing, which is the same as no limiter, and a single-entry
    header cannot tell the two implementations apart.
    """
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 1)
    spoofed = FakeRequest(peer="10.0.0.1", forwarded="198.51.100.4, 203.0.113.7")
    assert client_ip(spoofed) == "203.0.113.7"

    # ...and rotating the prefix must not buy a fresh budget.
    other = FakeRequest(peer="10.0.0.1", forwarded="192.0.2.99, 203.0.113.7")
    assert client_ip(spoofed) == client_ip(other)


def test_a_second_forwarded_header_line_cannot_outrank_the_proxys(monkeypatch):
    """The bypass that defeats the whole design if `.get` is used.

    `Headers.get` returns the FIRST matching line and silently drops the rest.
    A proxy is free to append a second `X-Forwarded-For` line rather than
    concatenate into the caller's, and nothing merges them — so with `.get`,
    counting from the right would be counting inside the ATTACKER's line, and
    every caller could hand themselves a private bucket by sending one header.

    Joining every line first is what restores the invariant the rule rests on:
    that the rightmost entries are the ones proxies wrote.
    """
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 1)
    request = FakeRequest(peer="10.0.0.1", forwarded=["6.6.6.6", "203.0.113.7"])
    assert client_ip(request) == "203.0.113.7"

    # ...and rotating the attacker's line must not buy a fresh budget.
    other = FakeRequest(peer="10.0.0.1", forwarded=["7.7.7.7", "203.0.113.7"])
    assert client_ip(request) == client_ip(other)


def test_two_trusted_hops_read_past_the_inner_proxy(monkeypatch):
    # Each proxy appends what it saw, so with two the client is second from the
    # right and the last entry is the outer proxy's view of the inner one.
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 2)
    request = FakeRequest(peer="10.0.0.1", forwarded="203.0.113.7, 10.0.0.9")
    assert client_ip(request) == "203.0.113.7"


def test_a_forwarded_chain_shorter_than_expected_falls_back_to_the_peer(monkeypatch):
    # Trusting a short chain would let anyone pick their own bucket by sending
    # a one-entry header. And it must not IndexError.
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 2)
    request = FakeRequest(peer="10.0.0.1", forwarded="203.0.113.7")
    assert client_ip(request) == "10.0.0.1"


def test_behind_a_proxy_an_unconfigured_hop_count_puts_everyone_in_one_bucket(monkeypatch):
    """The regression `rate_limit_trusted_proxy_hops` exists to prevent.

    uvicorn only rewrites `request.client` from X-Forwarded-For when the peer
    is in --forwarded-allow-ips (default 127.0.0.1), and
    scripts/render-start-api.sh passes no override — so in production the peer
    is Render's router for every request. Left at 0, two different clients hash
    to the same bucket, which is strictly worse than no limiter and shows no
    local symptom. render.yaml sets it to 1; test_config.py holds it there.
    """
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 0)
    alice = FakeRequest(peer="10.0.0.1", forwarded="203.0.113.7")
    bob = FakeRequest(peer="10.0.0.1", forwarded="198.51.100.4")
    assert client_ip(alice) == client_ip(bob)

    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 1)
    assert client_ip(alice) != client_ip(bob)


# ── the Redis backend's commands ─────────────────────────────────────────────

class StubPipeline:
    def __init__(self, recorder: list, ttl: int) -> None:
        self._recorder = recorder
        self._ttl = ttl

    def incr(self, key):
        self._recorder.append(("incr", key))

    def expire(self, key, seconds, nx=False):
        self._recorder.append(("expire", key, seconds, nx))

    def ttl(self, key):
        self._recorder.append(("ttl", key))

    async def execute(self):
        return [1, True, self._ttl]


class StubRedis:
    def __init__(self, ttl: int = 42) -> None:
        self.calls: list = []
        self._ttl = ttl

    def pipeline(self):
        return StubPipeline(self.calls, self._ttl)


def test_the_redis_backend_sets_the_ttl_only_when_the_key_is_new():
    """Pins the commands issued, not that Redis honours them.

    A real-Redis test would be the stronger claim and would skip forever:
    .github/workflows/ci.yml provisions Postgres and no Redis, so it would be
    green without ever running — the silent-skip failure this suite already
    guards against elsewhere. This is what can be checked honestly here.

    `nx=True` is the load-bearing flag. Without it every request inside a
    window pushes the expiry out, so a caller hitting the route steadily never
    sees the window roll over and the first refusal lasts forever.
    """
    redis = StubRedis(ttl=42)
    hit = asyncio.run(RedisBackend(redis).hit("rl:profile:ip:203.0.113.7", 60))
    assert ("expire", "rl:profile:ip:203.0.113.7", 60, True) in redis.calls
    assert hit.hits == 1
    assert hit.reset_in == 42


def test_a_missing_ttl_falls_back_to_the_window():
    # Redis answers -1 (no expiry) and -2 (no key); neither is a Retry-After.
    for ttl in (-1, -2, 0):
        hit = asyncio.run(RedisBackend(StubRedis(ttl=ttl)).hit("k", 60))
        assert hit.reset_in == 60


def test_keys_are_namespaced_away_from_the_celery_broker(monkeypatch):
    # redis_url and celery_broker_url are the same database (db 0), so the
    # limiter shares a keyspace with the broker.
    redis = StubRedis()
    monkeypatch.setattr(ratelimit, "_backend", RedisBackend(redis))
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 0)
    monkeypatch.setattr(settings, "rate_limit_profile_per_minute", 10)
    call(rate_limit(TEST_POLICY), FakeRequest(peer="203.0.113.7"))
    assert redis.calls[0] == ("incr", "rl:test:ip:203.0.113.7")


# ── exposure A keys on the address, not the account ──────────────────────────

def test_an_enumeration_policy_ignores_the_account(monkeypatch):
    """Signup is self-serve, so a per-account budget is one an attacker mints.

    Without this, "create an account, spend 30, create another" turns the
    profile budget into 30 per account per minute and the walk-time figure in
    get_profile's docstring into a division.
    """
    monkeypatch.setattr(ratelimit, "_backend", MemoryBackend(clock=FakeClock()))
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 0)
    monkeypatch.setattr(settings, "rate_limit_profile_per_minute", 2)
    dep = rate_limit(ratelimit.POLICIES["profile"])
    request = FakeRequest(peer="203.0.113.7")

    call(dep, request, uuid.uuid4())
    call(dep, request, uuid.uuid4())
    # A third account, same address, no budget left.
    with pytest.raises(HTTPException) as exc:
        call(dep, request, uuid.uuid4())
    assert exc.value.status_code == 429


def test_a_content_policy_still_gives_a_signed_in_caller_their_own(monkeypatch):
    # The other half of the split: exposure B bounds load from a client, and a
    # household behind one NAT must not throttle itself.
    monkeypatch.setattr(ratelimit, "_backend", MemoryBackend(clock=FakeClock()))
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 0)
    monkeypatch.setattr(settings, "rate_limit_game_per_minute", 2)
    dep = rate_limit(ratelimit.POLICIES["game"])
    request = FakeRequest(peer="203.0.113.7")

    call(dep, request, uuid.uuid4())
    call(dep, request, uuid.uuid4())
    assert call(dep, request, uuid.uuid4()) is None


def test_exactly_the_enumeration_policies_key_by_address():
    # Stated as a set so adding a policy forces a decision about which it is,
    # rather than defaulting to the account key and being wrong quietly.
    by_address = {n for n, p in ratelimit.POLICIES.items() if p.by_address}
    assert by_address == {"profile", "user_posts"}


def test_retry_after_is_never_zero_at_the_end_of_a_window():
    """A Retry-After of 0 tells the client to retry immediately.

    That is the one thing a 429 exists to prevent, and it is reachable only at
    the very end of a window — which is why the clock has to be driven almost
    all the way there. A test taken at the start of the window has 60 seconds
    remaining and passes with or without the floor.
    """
    clock = FakeClock()
    memory = MemoryBackend(clock=clock)
    asyncio.run(memory.hit("k", 60))
    clock.advance(59.9)
    assert asyncio.run(memory.hit("k", 60)).reset_in >= 1

    # The Redis backend needs no floor: TTL answers in whole seconds and
    # anything not strictly positive falls through to the window.
    assert asyncio.run(RedisBackend(StubRedis(ttl=0)).hit("k", 60)).reset_in == 60
    assert asyncio.run(RedisBackend(StubRedis(ttl=1)).hit("k", 60)).reset_in == 1
