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
here**: it mints an attachment URL for the whole clip, and a distributed pull of
a leaked link is an unbounded egress bill that a per-IP limit does nothing
about. It requires authentication instead — see ``routers/clips.py``. That is
the one route where this module's fail-open posture would be least comfortable,
and it is deliberately not relying on it.

Not applied to writes. Those already require a credential and are bounded by
the upload quota (``quota_max_games_per_window``).

**Keyed on the viewer when there is one, on the IP only when there is not.**
Two reasons. The game detail page polls ``GET /games/{id}`` every five seconds
while a game is processing, from the authenticated owner — four tabs, or a
household behind one NAT during a tournament, would otherwise eat a budget that
exists to stop strangers. And an authenticated enumerator gets their own bucket
and is bannable, which an IP is not.

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

**Fail open.** If the backend raises, the request is allowed. CLAUDE.md's code
posture — "reporting must never break processing" — applies with force here:
what this protects is a cost and enumeration-rate concern, not an authorization
boundary. ``services/access.py`` still decides who may read what and never
consults Redis, so failing closed would convert a cache blip into a total
outage of the public read surface while buying no confidentiality at all. The
limiter has never been what keeps a private clip private.

**Keys are prefixed ``rl:``** because ``redis_url`` and ``celery_broker_url``
point at the same database (db 0), so the limiter shares a keyspace with the
broker.
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
    """

    name: str
    setting: str
    window_seconds: int = 60

    @property
    def limit(self) -> int:
        return int(getattr(settings, self.setting))


# The whole table in one place, so a reviewer reads all six numbers together.
# Justifications are on each route's docstring, where someone changing that
# route will see them.
POLICIES: dict[str, Policy] = {
    # Exposure A — enumeration. Deliberately the same number for both: a
    # different one would only tell a walker which of the two doors is cheaper.
    "profile": Policy("profile", "rate_limit_profile_per_minute"),
    "user_posts": Policy("user_posts", "rate_limit_user_posts_per_minute"),
    # Exposure B — content. The game pair is sized by the detail page's own
    # polling; the share pair is a load bound, not an anti-enumeration one.
    "game": Policy("game", "rate_limit_game_per_minute"),
    "games_clips": Policy("games_clips", "rate_limit_games_clips_per_minute"),
    "share": Policy("share", "rate_limit_share_per_minute"),
    "post": Policy("post", "rate_limit_post_per_minute"),
}


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
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._windows: dict[str, tuple[int, float]] = {}

    async def hit(self, key: str, window_seconds: int) -> Hit:
        now = self._clock()
        count, started = self._windows.get(key, (0, now))
        if now - started >= window_seconds:
            count, started = 0, now
        count += 1
        self._windows[key] = (count, started)
        remaining = window_seconds - (now - started)
        return Hit(count, max(1, int(remaining) + 1))


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
_last_warned_at = 0.0


def _warn_backend_down(exc: BaseException) -> None:
    global _last_warned_at
    now = time.monotonic()
    if now - _last_warned_at < _WARN_EVERY_SECONDS:
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
    forwarded = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    if len(parts) < hops:
        # A chain shorter than we expect means the request did not come through
        # the proxies we configured. Trusting a short chain would let anyone
        # bypass their bucket by sending a one-entry header.
        return peer
    return parts[-hops]


def identity(request: Request, viewer_id: uuid.UUID | None) -> str:
    """Who is spending this budget. Signed-in callers get their own."""
    if viewer_id is not None:
        return f"user:{viewer_id}"
    return f"ip:{client_ip(request)}"


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
    parameter would change seven coroutine signatures and churn every direct
    call site in the suite.
    """

    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    async def __call__(
        self,
        request: Request,
        viewer_id: uuid.UUID | None = Depends(get_optional_user_id),
    ) -> None:
        policy = self.policy
        if not settings.rate_limit_enabled:
            return
        key = f"rl:{policy.name}:{identity(request, viewer_id)}"
        try:
            hit = await get_backend().hit(key, policy.window_seconds)
        except Exception as exc:  # noqa: BLE001 — fail open, see module docstring
            _warn_backend_down(exc)
            return
        if hit.hits > policy.limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Try again shortly.",
                headers={"Retry-After": str(hit.reset_in)},
            )


def rate_limit(policy: Policy) -> RateLimiter:
    return RateLimiter(policy)
