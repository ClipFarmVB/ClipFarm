"""Per-caller rate limiting for the anonymous read surface (CF-186, #189).

Seven endpoints answer without a credential. #189 splits them into two
exposures that want different answers, and this module implements both:

**A. Enumerable, handle-keyed** — ``GET /users/{handle}``, ``GET /posts?username=``.
Handles are backfilled from email local parts (migration 010), so for every
pre-CF-107 account the handle is guessable rather than random and a wordlist
walk returns a roster of real accounts. Throttled.

**B. UUID-keyed content** — ``GET /games/{id}``, ``GET /games/{id}/clips``,
``GET /clips/{id}/share``, ``GET /posts/{id}``. These take a UUID so they cannot
be walked; the exposure is load and abuse of a leaked link. Throttled, loosely,
because traffic on a deliberately public clip is the success case.

``GET /clips/{id}/download`` was the fifth route in exposure B and is **not
here**: it requires authentication instead — see ``routers/clips.py``. Neither
that nor this module bounds how often a clip is fetched: ``/share`` and the post
reads presign the same object anonymously, and a presigned URL is fetched from
R2 without touching the API. What these limits bound is calls to the API.

Not applied to writes. Those already require a credential and are bounded by
the upload quota (``quota_max_games_per_window``).

**Exposure B keys on the viewer when there is one; exposure A always keys on
the address.** B prefers the viewer for two reasons. The game detail page polls
``GET /games/{id}`` every five seconds while a game is processing, from the
authenticated owner — four tabs, or a household behind one NAT during a
tournament, would otherwise eat a budget that exists to stop strangers. And an
authenticated enumerator gets their own bucket and is bannable, which an address
is not.

A gives both of those up, because signup here is self-serve: a per-account
bucket is one an attacker can mint, which turns 30/min into 30N/min and the
walk-time figure into a division. That is what ``Policy.by_address`` sets, and
the two exposures differing is the whole point of splitting the card — see
``Policy`` for the trade it makes. The address side is not the raw address
either: it is the v4 host or the v6 /64, see ``_bucket``.

**Fixed window, not sliding.** ``INCR`` plus ``EXPIRE ... NX`` is one round
trip with no Lua and no sorted sets. The cost is that a caller can spend two
windows' worth across a window boundary. At these magnitudes that is not the
difference between safe and unsafe, and the alternative buys precision nobody
is asking for.

**One round trip on the happy path, not zero.** #189 asks that the limiter not
add a per-request round trip. With a counter shared across replicas, zero is
not achievable — the honest answer is one pipelined round trip against a pooled
connection. A per-process in-memory counter would be zero and would also be
wrong the moment there is more than one instance.

**Fail open.** If the backend raises — including on either of the two bounds
above ``_enforce``, which are what make a *hanging* backend raise at all — the
request is allowed. CLAUDE.md's code
posture — "reporting must never break processing" — applies with force here:
what this protects is a cost and enumeration-rate concern, not an authorization
boundary. ``services/access.py`` still decides who may read what and never
consults Redis, so failing closed would convert a cache blip into a total
outage of the public read surface while buying no confidentiality at all. The
limiter has never been what keeps a private clip private.

**Keys are prefixed ``rl:``** because ``redis_url`` and ``celery_broker_url``
point at the same database (db 0), so the limiter shares a keyspace with the
broker.

That prefix prevents a name collision and not a memory one, which is worth
stating because Render's instance is ``noeviction``: filling it makes broker
writes fail, so an attack on the read surface could stop job processing.
Two things bound it. Every key carries a one-window TTL, so steady-state
cardinality is distinct callers per minute per policy rather than anything
cumulative; and ``REDIS_URL`` is a separate setting from ``CELERY_BROKER_URL``,
so an operator who wants the guarantee rather than the bound points them at
different instances. A shared *database number* would not help — memory is per
instance.

**``EXPIRE ... NX`` needs Redis 7.0 or newer.** Against 6.x the queued command
is rejected, ``execute()`` raises, and the limiter then fails open permanently
with one log line a minute — quiet, and indistinguishable from Redis being
fine. Render's keyvalue is 7.x; DEPLOY.md says so for the VPS path.
"""
# DELIBERATELY NO `from __future__ import annotations`.
#
# RateLimiter below is a *callable instance* used as a FastAPI dependency.
# FastAPI resolves a dependency's string annotations through its `__globals__`,
# which an instance does not have — so with postponed evaluation `request:
# Request` stays the string "Request", FastAPI cannot tell it is the request
# object, and treats it as a required query parameter. Every throttled route
# then answers 422 to every caller. `test_a_limiter_actually_refuses_over_the_wire`
# is what catches it.
import asyncio
import ipaddress
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Callable, NamedTuple, Protocol

from fastapi import Depends, HTTPException, Request, status

from app.auth import get_optional_user_id
from app.config import settings

logger = logging.getLogger(__name__)


class Hit(NamedTuple):
    """One counted request: how many are in this window, and when it resets.

    The field is `hits` rather than `count` because a NamedTuple field named
    `count` shadows `tuple.count`, which mypy rejects outright.
    """

    hits: int
    reset_in: int


@dataclass(frozen=True)
class Policy:
    """One route group's budget.

    ``setting`` names the ``Settings`` field holding the limit rather than
    carrying the number, so an incident can be handled from the environment
    instead of a deploy. ``name`` is what goes in the key: two policies must
    never share a counter, or ``/share`` and ``/users/{handle}`` would spend
    each other's budget.

    ``by_address`` forces the key onto the client address even for a signed-in
    caller. **Exposure A sets it and exposure B does not**, and the difference
    is the whole point of splitting the card:

    * B bounds *load from a client*, so a signed-in caller should have their own
      budget — the game page polls from an authenticated owner, and a household
      behind one NAT must not throttle itself.
    * A bounds *enumeration from a source*. Signup here is self-serve, so a
      per-account bucket is a bucket an attacker can mint: "create account, spend
      30, create another" turns 30/min into 30N/min and the walk-time figure into
      a division. Keying on the address is what makes the budget mean something.

    The cost of A's choice is a shared office sharing one profile-read budget.
    That is the right side to err on: nothing in the app polls those two routes,
    so 30/min is far above real browsing, and being wrong the other way is
    silently no limit at all.
    """

    name: str
    setting: str
    window_seconds: int = 60
    by_address: bool = False

    @property
    def limit(self) -> int:
        return int(getattr(settings, self.setting))


# The whole table in one place, so a reviewer reads all six numbers together.
# Justifications are on each route's docstring, where someone changing that
# route will see them.
POLICIES: dict[str, Policy] = {
    # Exposure A — enumeration. Deliberately the same number for both: a
    # different one would only tell a walker which of the two doors is cheaper.
    # by_address because signup is self-serve; see Policy.
    "profile": Policy("profile", "rate_limit_profile_per_minute", by_address=True),
    "user_posts": Policy(
        "user_posts", "rate_limit_user_posts_per_minute", by_address=True
    ),
    # Exposure B — content. The game pair is sized by the detail page's own
    # polling; the share pair is a load bound, not an anti-enumeration one.
    "game": Policy("game", "rate_limit_game_per_minute"),
    "games_clips": Policy("games_clips", "rate_limit_games_clips_per_minute"),
    "share": Policy("share", "rate_limit_share_per_minute"),
    "post": Policy("post", "rate_limit_post_per_minute"),
}


# Two bounds on how long a limiter check may take. Neither is a tuning knob:
# without them a *hanging* backend defeats the fail-open posture entirely.
# `_enforce` catches a raise, and redis-py's asyncio client defaults its socket
# timeouts to None (verified against the installed 5.2.1:
# `AbstractConnection.__init__`), so a blackholed connection never raises and
# never returns. The request then blocks on the kernel's TCP retry bound rather
# than on anything this module decided, and the module docstring's "if the
# backend raises, the request is allowed" quietly does not cover the case that
# matters most. `_check_redis` in main.py has always bounded the same call
# against the same service with its own `wait_for`.
#
# **They bound different things, which is why both are here.** The socket
# timeout is per OPERATION — connect, handshake, each read — so a server that
# answers every operation just inside the bound still stretches one `hit()` well
# past it; a round measured 6.31s cold against a 0.9s-per-reply server. The
# request budget bounds the whole call, which is the number a caller waiting on
# a public read actually experiences.
#
# *An earlier version of this comment argued the request budget was unsafe —
# that cancelling mid-command would return a connection to the pool with an
# unread reply on it. That was wrong, and a review round measured it wrong:
# `Connection.read_response` ends in `except BaseException: await
# self.disconnect(nowait=True); raise` (`redis/asyncio/connection.py`, comment
# citing redis-py #1128), and `CancelledError` is a `BaseException`. The
# cancelled connection is closed, not reused. 42 mid-flight cancellations
# produced 43 fresh connections and no stale reply. The claim was made from
# reading `execute_command` and not `read_response`.*
#
# Both timeouts raise something that is an `Exception` — `redis.TimeoutError`
# and, on 3.11, `asyncio.TimeoutError is builtins.TimeoutError` — so both land
# in the fail-open branch that was always there.
#
# One second per operation, not the two `_check_redis` uses: this is on every
# throttled read and a healthy round trip is sub-millisecond in-region. Two for
# the whole call, so a cold connect plus handshake plus command has room before
# the budget fires. Either firing means the request is allowed and logged, never
# refused.
REDIS_SOCKET_TIMEOUT_SECONDS = 1.0
REQUEST_BUDGET_SECONDS = 2.0

# How often MemoryBackend drops expired windows. One window, so a sweep is
# amortised over at least a window's worth of requests and the resident set is
# bounded by two windows of distinct callers rather than by uptime.
_SWEEP_EVERY_SECONDS = 60.0


class Backend(Protocol):
    async def hit(self, key: str, window_seconds: int) -> Hit: ...


class MemoryBackend:
    """In-process counters.

    Both the test seam and the fallback when no Redis client has been
    installed — a local ``uvicorn app.main:app`` with no lifespan, or any
    single-process run. It is correct for one process and wrong across
    replicas, which is why ``main.py`` installs the Redis backend in
    production.

    ``clock`` is injected for the same reason ``GameProgress`` takes one: the
    window arithmetic is the part worth testing, and it should not need a
    ``sleep``.

    **Expired windows are swept**, because this is a shipped fallback and not
    only a test seam. Redis gets the same bound from the one-window TTL on every
    key, and the module docstring leans on that to say steady-state cardinality
    is distinct callers per minute rather than anything cumulative. A plain dict
    has no TTL, so without a sweep the same sentence is false for the in-process
    path: every distinct bucket ever seen stays resident, and what drives that
    cardinality is exactly the attacker-controlled address space ``_bucket``
    exists to bound. Growth is the reason to sweep; nothing here is a
    correctness fix, since an expired entry is already ignored on read.

    The sweep runs at most once per ``_SWEEP_EVERY_SECONDS`` and costs one pass
    over the dict, so the resident set is bounded by the distinct callers of one
    window plus one sweep interval — not by the process's whole history. The
    window length is stored per entry rather than assumed, because a policy is
    free to carry its own and a sweep that used the caller's would drop live
    counters belonging to a longer one.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._windows: dict[str, tuple[int, float, int]] = {}
        self._swept_at: float | None = None

    async def hit(self, key: str, window_seconds: int) -> Hit:
        now = self._clock()
        self._sweep(now)
        count, started, _window = self._windows.get(key, (0, now, window_seconds))
        if now - started >= window_seconds:
            count, started = 0, now
        count += 1
        self._windows[key] = (count, started, window_seconds)
        remaining = window_seconds - (now - started)
        return Hit(count, max(1, int(remaining) + 1))

    def _sweep(self, now: float) -> None:
        # `None` rather than 0.0 for "never swept": time.monotonic()'s origin is
        # arbitrary, so a 0.0 sentinel means the first call either sweeps or
        # does not depending on how long the machine has been up.
        if self._swept_at is not None and now - self._swept_at < _SWEEP_EVERY_SECONDS:
            return
        self._swept_at = now
        self._windows = {
            key: entry
            for key, entry in self._windows.items()
            if now - entry[1] < entry[2]
        }


class RedisBackend:
    """Shared counters, one pipelined round trip.

    ``EXPIRE ... nx=True`` rather than a bare ``EXPIRE``: without ``NX`` every
    request inside a window pushes the expiry out, so a caller hitting the
    route steadily would never see the window roll over and the first refusal
    would last forever.
    """

    def __init__(self, client) -> None:
        self._client = client

    async def hit(self, key: str, window_seconds: int) -> Hit:
        pipe = self._client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        pipe.ttl(key)
        count, _set, ttl = await pipe.execute()
        # -1 (no expiry) and -2 (no key) are both "cannot say"; the window is
        # the honest upper bound and the caller only uses this for Retry-After.
        # No floor needed here, unlike MemoryBackend: TTL answers in whole
        # seconds, and anything not strictly positive has already fallen through
        # to the window. A `max(1, ...)` would be unreachable, and unreachable
        # code that looks like a safety check is worse than none — it invites
        # the reader to assume the case is handled somewhere.
        reset_in = ttl if ttl and ttl > 0 else window_seconds
        return Hit(int(count), int(reset_in))


_backend: Backend = MemoryBackend()


def get_backend() -> Backend:
    return _backend


def set_backend(backend: Backend) -> None:
    """Install the process backend. Called from main.py's lifespan."""
    global _backend
    _backend = backend


# A backend outage must not produce a log line per request — the same lesson
# GameProgress records about a failing progress writer. One line a minute is
# enough to see it in the dashboard; 10k is what buries the cause.
_WARN_EVERY_SECONDS = 60.0
# `None` rather than 0.0, for the reason MemoryBackend's `_swept_at` gives:
# time.monotonic()'s origin is arbitrary, so against a 0.0 sentinel the first
# outage either logs or does not depending on how long the machine has been up.
# Same file, same trap, and this copy predates the other.
_last_warned_at: float | None = None


def _warn_backend_down(exc: BaseException) -> None:
    global _last_warned_at
    now = time.monotonic()
    if _last_warned_at is not None and now - _last_warned_at < _WARN_EVERY_SECONDS:
        return
    _last_warned_at = now
    logger.warning("rate limit backend unavailable, allowing requests: %r", exc)


def client_ip(request: Request) -> str:
    """The caller's address, honouring exactly as many proxy hops as we trust.

    ``rate_limit_trusted_proxy_hops`` is the number of reverse proxies in front
    of this process. Each one appends the address it received the connection
    *from*, and the last proxy's own address never appears in the header — it is
    the peer. So with one trusted hop the client is the last entry.

    Getting this wrong is silent in both directions, which is why it is a
    setting rather than a guess: too low and every caller behind the proxy
    shares one bucket, too high and a caller picks their own bucket by sending
    the header themselves.
    """
    hops = settings.rate_limit_trusted_proxy_hops
    peer = request.client.host if request.client else "unknown"
    if hops <= 0:
        return peer
    # `getlist`, not `get`. A proxy may APPEND A SECOND HEADER LINE rather than
    # concatenate into the existing one, and nothing merges them for us —
    # `.get` would return the first line only. A caller who sends their own
    # X-Forwarded-For would then win against a proxy that appends a line,
    # because the counting-from-the-right rule below would be counting inside
    # the attacker's line. Joining every line first restores the one thing the
    # rule depends on: that the last entries are the ones proxies wrote.
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    if len(parts) < hops:
        # A chain shorter than we expect means the request did not come through
        # the proxies we configured. Trusting a short chain would let anyone
        # bypass their bucket by sending a one-entry header.
        return peer
    return parts[-hops]


def _bucket(addr: str) -> str:
    """The address, narrowed to what one party actually controls.

    An IPv6 caller is routinely handed a /64 and often a /56 or /48, so keying
    on the full address gives a single residential customer on the order of
    10^19 fresh budgets — the per-address limit stops meaning anything, and the
    walk-time figure the enumeration policies rest on holds only against IPv4.
    Truncating to /64 is the narrowest prefix providers are expected to hand out
    whole. That is an allocation convention rather than a guarantee, and the one
    case where it is plainly false is handled below rather than asserted away.

    **An IPv4-mapped address is keyed as the IPv4 host it names.**
    ``::ffff:a.b.c.d`` is version 6 and its /64 is all zeroes, so narrowing it
    would file every mapped caller — and ``::1`` — under one key, and three
    strangers would spend each other's budget. That is worse than not narrowing
    at all, which is the direction this function exists to avoid. Mapping it
    back to the v4 host also puts the two spellings of one caller in one bucket,
    which is the right answer however the address reached us. Both uvicorn
    invocations in this repo bind ``0.0.0.0``, so the peer is never the mapped
    form; ``client_ip`` returns whatever an upstream proxy wrote into
    ``X-Forwarded-For``, and a dual-stack front end may well write it.

    ``::`` and the deprecated IPv4-compatible form ``::a.b.c.d`` still share the
    zero /64. Neither is something a proxy writes, and unlike the mapped form
    they carry no host to recover.

    A v4 address is one host, so it is returned unchanged. Anything unparseable
    — `unknown` from a missing peer, or a malformed header entry that survived
    the split — is returned as-is: it is already a single bucket, and inventing
    a parse for it would be inventing a key.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return addr
    # isinstance rather than `.version`, because `ipv4_mapped` exists only on
    # the v6 class and mypy narrows on the type, not on the number.
    if not isinstance(ip, ipaddress.IPv6Address):
        return addr
    mapped = ip.ipv4_mapped
    if mapped is not None:
        return str(mapped)
    return str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address) + "/64"


def identity(request: Request, viewer_id: uuid.UUID | None, policy: Policy) -> str:
    """Who is spending this budget.

    Signed-in callers get their own, except on an ``by_address`` policy, where
    an account is something the attacker can mint — see ``Policy``.
    """
    if viewer_id is not None and not policy.by_address:
        return f"user:{viewer_id}"
    return f"ip:{_bucket(client_ip(request))}"


class RateLimiter:
    """A route dependency enforcing one policy.

    A callable object rather than a closure so the policy stays *readable from
    the route*: these are attached through the decorator's
    ``dependencies=[...]``, which makes them invisible to this suite's habit of
    calling router coroutines directly, and ``test_ratelimit_wiring.py`` needs
    to be able to say which policy a route carries rather than only that it
    carries something. FastAPI resolves a callable instance by inspecting
    ``__call__``, so the dependency signature works exactly as a function's
    would.

    ``dependencies=[...]`` rather than a parameter, in turn, because a new
    parameter would change all six throttled coroutine signatures and churn
    every direct call site in the suite. (Six, not the seven anonymous reads
    this card started from: ``download_clip`` is the seventh and its signature
    *did* change here, since auth rather than a limiter is what it got.)
    """

    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    async def _enforce(self, request: Request, viewer_id: uuid.UUID | None) -> None:
        policy = self.policy
        if not settings.rate_limit_enabled:
            return
        key = f"rl:{policy.name}:{identity(request, viewer_id, policy)}"
        try:
            hit = await asyncio.wait_for(
                get_backend().hit(key, policy.window_seconds),
                timeout=REQUEST_BUDGET_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 — fail open, see module docstring
            _warn_backend_down(exc)
            return
        if hit.hits > policy.limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Try again shortly.",
                headers={"Retry-After": str(hit.reset_in)},
            )

    async def __call__(self, request: Request) -> None:
        # No auth dependency in this signature, deliberately. `identity` ignores
        # `viewer_id` on a `by_address` policy, so resolving it would be work
        # done to be discarded — and not free work:
        #
        #   * `get_optional_user_id` re-raises a JWKS failure, so a route that
        #     depends on it answers 503 when auth is unwell. These are public
        #     reads; a signed-out visitor was fine, but the web client attaches a
        #     bearer whenever a session exists, so any signed-in visitor to a
        #     profile page took that path.
        #
        #     That is narrower than "the profile page is immune now". The page
        #     also fetches `GET /posts?username=`, whose handler declares
        #     `viewer_id` itself to filter visibility — so auth is in that
        #     route's tree on its own account, as it is on `main`, and the grid
        #     still answers 503 with a bearer when auth is unwell. What this
        #     removed is the limiter putting auth on a route that had no other
        #     use for it, which is all of `GET /users/{handle}` and none of
        #     `GET /posts`.
        #   * FastAPI resolves dependencies BEFORE the handler, so the JWT
        #     verification and `_ensure_user_exists`'s INSERT-and-COMMIT ran
        #     ahead of the counter. A refused request still cost a database
        #     write, on the two routes whose whole purpose is bounding an
        #     attacker's rate.
        #
        # `ViewerRateLimiter` below declares it, because a viewer-keyed policy
        # cannot build its key without it.
        await self._enforce(request, None)


class ViewerRateLimiter(RateLimiter):
    """A limiter for a policy keyed on the signed-in caller.

    Separate class rather than a branch inside one, because FastAPI reads the
    dependency graph off the signature: a single `__call__` cannot declare the
    auth dependency for some policies and not others. `rate_limit` picks.
    """

    async def __call__(
        self,
        request: Request,
        viewer_id: uuid.UUID | None = Depends(get_optional_user_id),
    ) -> None:
        await self._enforce(request, viewer_id)


def rate_limit(policy: Policy) -> RateLimiter:
    """The dependency for a policy — address-keyed or viewer-keyed.

    The choice is the policy's, not the route's, so a policy that keys on the
    address can never accidentally acquire an auth dependency it does not use.
    """
    return RateLimiter(policy) if policy.by_address else ViewerRateLimiter(policy)
