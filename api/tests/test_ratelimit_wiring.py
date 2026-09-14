"""Every anonymous read is actually covered (CF-186, #189).

The limiters hang off each route decorator's ``dependencies=[...]``, which
keeps all seven coroutine signatures byte-identical and leaves every direct
call in this suite working — and makes the limiter completely invisible to
those calls. So this file is not optional: without it, deleting a
``dependencies=[...]`` line would break nothing that runs.

The table below is the guard. A new anonymous read added later without a
limiter fails here, the same shape as test_no_visibility_write_path.py's AST
walk: the point is that a reviewer does not have to notice.
"""
import asyncio

import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.config import Settings, settings
from app.routers import clips, games, posts, profiles
from app.services import ratelimit
from app.services.ratelimit import MemoryBackend, RateLimiter

# path -> (policy name, the settings field it reads, the number that field
# defaults to). All three, because the name alone pins almost nothing: a policy
# can carry the right name and read another route's setting, and every default
# can be raised to 30000, with the name-only check still green. The README,
# config.py and four route docstrings all state these numbers as decisions;
# this table is what makes them true.
THROTTLED = {
    ("/users/{handle}", "GET"): ("profile", "rate_limit_profile_per_minute", 30),
    ("/posts", "GET"): ("user_posts", "rate_limit_user_posts_per_minute", 30),
    ("/games/{game_id}", "GET"): ("game", "rate_limit_game_per_minute", 60),
    ("/games/{game_id}/clips", "GET"): (
        "games_clips",
        "rate_limit_games_clips_per_minute",
        60,
    ),
    ("/clips/{clip_id}/share", "GET"): ("share", "rate_limit_share_per_minute", 120),
    ("/posts/{post_id}", "GET"): ("post", "rate_limit_post_per_minute", 120),
}

ROUTERS = [clips.router, games.router, posts.router, profiles.router]


def _all_routes():
    # APIRouter applies its prefix when the route is added, so route.path is
    # already the full path — concatenating the prefix again yields /posts/posts
    # and matches nothing.
    for router in ROUTERS:
        for route in router.routes:
            for method in getattr(route, "methods", set()):
                yield route.path, method, route


def _limiters_on(route) -> list:
    return [
        d.dependency
        for d in (route.dependencies or [])
        if isinstance(getattr(d, "dependency", None), RateLimiter)
    ]


def _policies_on(route) -> list[str]:
    return [limiter.policy.name for limiter in _limiters_on(route)]


def _dependency_callables(route) -> set:
    """Every callable in the route's resolved dependency tree."""
    seen = set()
    stack = list(getattr(route.dependant, "dependencies", []))
    while stack:
        dep = stack.pop()
        if dep.call is not None:
            seen.add(dep.call)
        stack.extend(dep.dependencies)
    return seen


def _route_for(path, method):
    for route_path, route_method, route in _all_routes():
        if (route_path, route_method) == (path, method):
            return route
    raise AssertionError(f"{method} {path} is not registered on any router")


@pytest.mark.parametrize(
    "path,method,expected", [(p, m, v) for (p, m), v in THROTTLED.items()]
)
def test_every_anonymous_read_carries_its_limiter(path, method, expected):
    name, setting, default = expected
    limiters = _limiters_on(_route_for(path, method))
    assert [limiter.policy.name for limiter in limiters] == [name], (
        f"{method} {path} carries {[x.policy.name for x in limiters]}, expected [{name}]"
    )
    policy = limiters[0].policy
    # The setting, not just the name: a policy reading another route's field is
    # throttled at somebody else's number and the name check cannot see it.
    assert policy.setting == setting
    assert policy.window_seconds == 60, "the numbers above are all per minute"
    # The default off the field itself rather than the live Settings, so a test
    # elsewhere that monkeypatched a limit cannot make this pass or fail.
    assert Settings.model_fields[setting].default == default


@pytest.mark.parametrize(
    "path,method", [(p, m) for (p, m) in THROTTLED], ids=lambda v: str(v)
)
def test_every_throttled_read_stays_anonymous(path, method):
    """The mirror image of the download change, and the likelier regression.

    Someone "making /share consistent with /download" would break every public
    share link. Nothing else in the suite would notice: these routes are
    exercised by calling their coroutines directly, which never resolves an
    auth dependency at all.
    """
    calls = _dependency_callables(_route_for(path, method))
    assert get_current_user_id not in calls, (
        f"{method} {path} now requires auth; it is an anonymous read"
    )


def test_the_download_route_requires_auth_instead_of_a_limiter():
    """The seventh anonymous read, deliberately handled differently.

    It is the only one that hands over the bytes, so a per-caller counter is
    the wrong instrument — a distributed pull of a leaked link never trips one.
    Auth is the control. Pinned here so "make the two share routes consistent"
    cannot quietly undo it.
    """
    import inspect
    import typing

    from app.auth import get_current_user_id, get_optional_user_id

    # The parameter is an Annotated alias (`UserId`), so the Depends lives in
    # the annotation's metadata rather than in the default.
    annotation = inspect.signature(clips.download_clip).parameters["user_id"].annotation
    depends = [
        m for m in typing.get_args(annotation) if getattr(m, "dependency", None) is not None
    ]
    assert depends, f"user_id carries no dependency: {annotation}"
    assert depends[0].dependency is get_current_user_id
    assert depends[0].dependency is not get_optional_user_id

    route = next(
        route
        for path, method, route in _all_routes()
        if (path, method) == ("/clips/{clip_id}/download", "GET")
    )
    assert _policies_on(route) == []


def test_write_routes_are_not_throttled():
    # Writes already require a credential and are bounded by the upload quota.
    # A limiter on them would be a second, weaker control with its own failure
    # mode.
    for path, method, route in _all_routes():
        if method in {"POST", "PATCH", "DELETE", "PUT"}:
            assert _policies_on(route) == [], f"{method} {path} should not be throttled"


def test_the_health_routes_are_not_throttled():
    # /healthz is Render's health check. Throttling it would cycle instances,
    # which is a far worse outage than whatever the limit was protecting.
    from app.main import app

    for route in app.routes:
        if getattr(route, "path", "") in {"/health", "/healthz"}:
            assert _policies_on(route) == []


def test_a_limiter_actually_refuses_over_the_wire(monkeypatch):
    """#189's acceptance line: a test demonstrates the control engaging.

    Over HTTP through a real ASGI stack rather than by calling the dependency,
    so it also covers the wiring, the 429 reaching the client, and the header
    surviving the response.
    """
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 0)
    monkeypatch.setattr(settings, "rate_limit_share_per_minute", 2)
    monkeypatch.setattr(ratelimit, "_backend", MemoryBackend())

    from fastapi import Depends

    from app.auth import get_optional_user_id

    app = FastAPI()

    @app.get(
        "/probe",
        dependencies=[Depends(ratelimit.rate_limit(ratelimit.POLICIES["share"]))],
    )
    async def probe():
        return {"ok": True}

    # The limiter resolves the viewer so a signed-in caller gets their own
    # budget; that dependency opens a database session, which this probe has
    # no use for. Overridden to anonymous so the test is about the limiter.
    app.dependency_overrides[get_optional_user_id] = lambda: None

    with TestClient(app) as client:
        assert client.get("/probe").status_code == 200
        assert client.get("/probe").status_code == 200
        refused = client.get("/probe")
        assert refused.status_code == 429
        assert int(refused.headers["Retry-After"]) > 0


def test_retry_after_is_exposed_to_the_browser():
    # Retry-After is not a CORS-safelisted response header, so without
    # expose_headers the browser receives the 429 and the app cannot read how
    # long to wait — the decision half-delivered.
    from app.main import app

    cors = [m for m in app.user_middleware if "CORS" in m.cls.__name__]
    assert cors, "no CORS middleware"
    assert "Retry-After" in cors[0].kwargs["expose_headers"]


def test_a_malformed_redis_url_does_not_crash_the_boot(monkeypatch):
    """Before this lifespan existed, a bad REDIS_URL booted fine.

    `from_url` validates the scheme eagerly — `_check_redis` binds its client
    outside the try for exactly that reason — so an unguarded lifespan turns a
    typo in one env var into a crash loop. That is a worse outage than an
    unthrottled read surface, and not a trade this card gets to make: the
    limiter falls back to counting in-process and /health goes on saying redis
    is down.
    """
    import app.main as main

    monkeypatch.setattr(settings, "redis_url", "postgres://not-a-redis-url")
    monkeypatch.setattr(ratelimit, "_backend", MemoryBackend())

    async def drive():
        async with main.lifespan(main.app):
            assert isinstance(ratelimit.get_backend(), MemoryBackend)

    asyncio.run(drive())


def test_the_lifespan_installs_and_removes_the_redis_backend(monkeypatch):
    """The limiter counts in-process until something installs a shared backend.

    Without this, a MemoryBackend in production would count per replica and
    silently multiply every limit by the instance count.
    """
    import app.main as main

    closed = []

    class FakeRedis:
        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(main.aioredis, "from_url", lambda url: FakeRedis())
    monkeypatch.setattr(ratelimit, "_backend", MemoryBackend())

    async def drive():
        async with main.lifespan(main.app):
            assert isinstance(ratelimit.get_backend(), ratelimit.RedisBackend)
        # Back to in-process on shutdown, so a late call counts locally rather
        # than reaching a closed connection.
        assert isinstance(ratelimit.get_backend(), MemoryBackend)

    asyncio.run(drive())
    assert closed == [True]


def test_an_address_keyed_route_resolves_no_auth_dependency():
    """The property finding 3 was actually about, pinned at the route.

    An earlier fix removed `viewer_id` from `get_profile`'s own signature, which
    looked like it had removed the dependency -- but `RateLimiter.__call__` still
    declared `Depends(get_optional_user_id)`, so it stayed in the resolved tree
    for every route carrying a limiter. Two consequences, both measured on this
    branch before the split:

      * `get_optional_user_id` re-raises a JWKS failure, so these public reads
        answered 503 when auth was unwell -- and the web client sends a bearer
        whenever a session exists, so a signed-in visitor to a profile page took
        that path.
      * FastAPI resolves dependencies before the handler, so a JWT verification
        and `_ensure_user_exists`'s INSERT-and-COMMIT ran ahead of the counter:
        a refused request still cost a database write, on the two routes whose
        purpose is bounding an attacker's rate.

    Asserted against the resolved `route.dependant` rather than the source, so
    reintroducing the dependency anywhere in the chain fails here.
    """
    from app.auth import get_optional_user_id

    checked = 0
    for (path, method), (name, _setting, _default) in THROTTLED.items():
        route = _route_for(path, method)
        limiters = _limiters_on(route)
        if not limiters or not limiters[0].policy.by_address:
            continue
        # Only where the HANDLER does not want a viewer. `list_user_posts`
        # declares one because it filters visibility by it, so auth is in its
        # tree on its own account and always was; the defect was the limiter
        # putting it there for a route that had no other use for it.
        if "viewer_id" in inspect.signature(route.endpoint).parameters:
            continue
        assert get_optional_user_id not in _dependency_callables(route), (
            f"{method} {path} carries the {name} policy, which keys on the "
            "address and never reads the viewer -- resolving auth here buys a "
            "503 on a public read and a DB commit before the counter"
        )
        checked += 1
    # The clause above is an escape hatch as well as a carve-out: a handler that
    # gains a `viewer_id` takes its route out of this test, and with one
    # address-keyed route left standing that empties the loop entirely. Putting
    # the whole defect back -- `viewer_id` on `get_profile` AND the dependency
    # on `RateLimiter.__call__` -- left this passing with nothing asserted, and
    # only the weaker source-shaped check in test_ratelimit.py went red.
    assert checked, (
        "every address-keyed route was skipped -- this test asserted nothing. "
        "A handler that declares `viewer_id` leaves the route uncheckable here, "
        "so the guarantee needs pinning some other way rather than silently"
    )


def test_a_viewer_keyed_route_does_resolve_auth():
    """The other direction, so the test above cannot be satisfied by deleting
    the dependency everywhere: a content policy keys on the signed-in caller and
    genuinely needs it."""
    from app.auth import get_optional_user_id

    checked = 0
    for (path, method), (_name, _setting, _default) in THROTTLED.items():
        route = _route_for(path, method)
        limiters = _limiters_on(route)
        if not limiters or limiters[0].policy.by_address:
            continue
        assert get_optional_user_id in _dependency_callables(route)
        checked += 1
    assert checked, "no viewer-keyed route in the table -- the pair is vacuous"


def test_the_limiter_is_resolved_before_anything_a_refusal_should_not_pay_for():
    """A 429 must not have cost a JWT verification or a database write first.

    That property is real -- measured at 12 requests against a 2/min budget,
    `GET /posts` performs two commits, one per *allowed* request, and none for
    the ten refusals -- but on `GET /posts` it does not come from the class
    split: that route's handler declares `viewer_id` itself, so auth is in its
    tree regardless. What saves it is ordering. FastAPI puts a decorator's
    ``dependencies=[...]`` ahead of the handler's own parameters and
    ``solve_dependencies`` awaits them in list order (fastapi 0.115.6,
    ``dependencies/utils.py``), so the limiter's ``HTTPException`` propagates
    before ``get_db`` or ``get_optional_user_id`` is ever called.

    Nothing pinned that. Moving a limiter to a handler parameter -- the shape
    the module docstring rejects for an unrelated reason -- would leave the
    whole suite green and restore the full cost on every refused request. So
    assert the position, not just the presence.
    """
    checked = 0
    for (path, method), (name, _setting, _default) in THROTTLED.items():
        route = _route_for(path, method)
        deps = route.dependant.dependencies
        at = [i for i, dep in enumerate(deps) if isinstance(dep.call, RateLimiter)]
        assert at, f"{method} {path} resolves no limiter at all"
        assert at[0] == 0, (
            f"{method} {path} resolves "
            f"{[getattr(d.call, '__name__', d.call) for d in deps[: at[0]]]} "
            f"before its {name} limiter, so a refused request pays for them"
        )
        checked += 1
    assert checked == len(THROTTLED)
