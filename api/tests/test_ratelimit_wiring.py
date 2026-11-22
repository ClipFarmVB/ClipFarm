"""Every anonymous read is actually covered (CF-186, #189).

The limiters hang off each route decorator's ``dependencies=[...]``, which
keeps the seven throttled coroutine signatures byte-identical and leaves every
direct call in this suite working — and makes the limiter completely invisible to
those calls. So this file is not optional: without it, deleting a
``dependencies=[...]`` line would break nothing that runs.

The table below pins each anonymous read to its policy, and
test_no_anonymous_read_escapes_the_table walks every router module for GET
routes that resolve no credential and requires each one to be in it. So a new
anonymous read added later without a limiter fails here, the same shape as
test_visibility_write_paths_are_declared.py's AST walk: the point is that a
reviewer does not have to notice.

Two objects are examined here, and they answer different questions. The router
table (`ROUTERS`, walked by `_all_routes`) answers "is a limiter attached to
this route at all, and which policy" -- the decorator is where it is declared,
and the policy checks read it there. The served app (`served_app`, built by
`app.main.mount_routers`) answers "where does the limiter sit, and what does a
refusal cost": `include_router(..., dependencies=[...])` lands a dependency on
the served route AHEAD of everything the router declared, invisibly to the
router objects, and a session dependency added inside `RateLimiter.__call__`
moves no position at all. So the guards that assert something is ABSENT or
where it sits read the app's route, and the cost is driven over the wire with a
counting session (CF-408). Both objects are route tables: middleware added to
`main.app` runs before routing and is outside everything here.
"""
import asyncio

import inspect
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.auth import get_current_user_id
from app.config import Settings, settings
from app.routers import clips, games, posts, profiles
from app.services import access, ratelimit
from app.services.ratelimit import MemoryBackend, RateLimiter

# path -> (policy name, the settings field it reads, the number that field
# defaults to). All three, because the name alone pins almost nothing: a policy
# can carry the right name and read another route's setting, and every default
# can be raised to 30000, with the name-only check still green. The README,
# config.py and seven route docstrings all state these numbers as decisions;
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
    # CF-319 deliberately shares /share's policy rather than adding a seventh
    # setting: it is the same action on the same object for the same viewer, so
    # a second number would be one more thing to keep in agreement with the
    # first. That means this row and /share's are expected to be identical —
    # which is also why the setting is asserted below and not only the name.
    ("/clips/{clip_id}/playback", "GET"): ("share", "rate_limit_share_per_minute", 120),
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


def _router_modules():
    """Every `router` under `app.routers`, as (module name, router).

    Discovered rather than listed: the package is walked module by module, so a
    router missing from ROUTERS, or one main.py mounts only behind a flag, is
    still found.
    """
    import importlib
    import pkgutil

    import app.routers as package

    for module_info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"app.routers.{module_info.name}")
        router = getattr(module, "router", None)
        if router is not None:
            yield module_info.name, router


def _anonymous_reads() -> set:
    """Every GET route, on every router module, that resolves no credential."""
    found = set()
    for _name, router in _router_modules():
        for route in router.routes:
            if not hasattr(route, "dependant"):
                continue
            if "GET" not in getattr(route, "methods", set()):
                continue
            if get_current_user_id not in _dependency_callables(route):
                found.add((route.path, "GET"))
    return found


def _route_for(path, method):
    for route_path, route_method, route in _all_routes():
        if (route_path, route_method) == (path, method):
            return route
    raise AssertionError(f"{method} {path} is not registered on any router")


def _served_route(app, path, method):
    """The route as the app serves it -- `include_router` builds a new
    `APIRoute` per route, so this is not the object `_route_for` returns."""
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        if method in getattr(route, "methods", set()):
            return route
    raise AssertionError(
        f"{method} {path} is not served by the app -- if main.py mounts it "
        "outside mount_routers, the probe never sees it"
    )


def _engage(monkeypatch, setting=None, per_minute=2, backend=None):
    """Turn the limiter on, trust no proxy hop, install an in-process backend
    (or the one given), and when a policy's setting is named, give it a budget
    of `per_minute`. The one place to change when the limiter grows a setting."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_trusted_proxy_hops", 0)
    if setting is not None:
        monkeypatch.setattr(settings, setting, per_minute)
    monkeypatch.setattr(ratelimit, "_backend", MemoryBackend() if backend is None else backend)


@pytest.fixture
def served_app(monkeypatch):
    """The route table `app.main` serves, assembled on a fresh app with the
    social surface on.

    `social_enabled` is forced on because the two address-keyed policies
    (`profile`, `user_posts`) live only on the routers main.py mounts behind
    that flag, which defaults off: on the flag-off app the property the guards
    below pin exists on no route at all, and the tests would pass vacuously.
    Forced through the fixture rather than the environment so the module-level
    `app.main.app` -- which other tests import -- is untouched, nothing is
    reloaded, and the flag is back to its default when the test ends.

    A fresh `FastAPI()` rather than `app.main.app` itself, first because of
    that flag: the module-level app was built with it off, so the two
    address-keyed routes are not on it at all, and turning the flag on now
    mounts nothing. Second, for the cost test: entering `with
    TestClient(app.main.app)` runs that app's lifespan, which installs a
    lazily-connecting `RedisBackend` over whatever the test monkeypatched into
    `ratelimit._backend`, the first `hit()` fails and the limiter fails open,
    so no 429 ever arrives. The fresh app has no lifespan and the
    `MemoryBackend` stays installed.
    `test_mount_routers_is_what_the_served_app_is_built_from` is what makes
    this the served table rather than a second, hand-maintained one.
    """
    import app.main as main

    monkeypatch.setattr(settings, "social_enabled", True)
    probe = FastAPI()
    main.mount_routers(probe)
    return probe


def _served_shape(app) -> list:
    """Each served route as (path, methods, resolved top-level dependencies):
    the three things the served-app guards read.

    Routes whose handler is defined in `app.main` itself (/health, /healthz,
    the debug route) are added by the module rather than by `mount_routers`,
    so they are on the served app and never on a probe. Left out by where the
    endpoint lives rather than by a list of paths, so a new route in main.py
    does not fail the shape test with a raw list diff."""
    import app.main as main

    return [
        (
            route.path,
            tuple(sorted(route.methods)),
            [dep.call for dep in route.dependant.dependencies],
        )
        for route in app.routes
        if hasattr(route, "dependant") and route.endpoint.__module__ != main.__name__
    ]


def test_mount_routers_is_what_the_served_app_is_built_from():
    """The fixture above is only evidence about production if `mount_routers`
    is the path `app.main.app` was actually assembled through. Pinned by
    shape: a probe built under the LIVE flag -- no monkeypatch here -- carries
    the same routes, methods and top-level dependencies, in the same order, as
    the module-level app minus the routes main.py adds itself. An unflagged
    `include_router` call that drifts back to module level, or any served
    route that gains a `dependencies=[...]` the helper does not have, shows up
    here. A flagged router that leaves the helper does NOT: this comparison
    runs under the live flag, which is off in the suite, so both sides omit
    `/users/*` and `/posts/*` whatever the helper does with them. That drift
    surfaces in the `served_app` guards instead, each failing as "not served
    by the app" (measured under exactly that move: those guards red, this test
    green)."""
    import app.main as main

    probe = FastAPI()
    main.mount_routers(probe)
    assert _served_shape(probe), "mount_routers mounted nothing"
    assert _served_shape(main.app) == _served_shape(probe)


def test_the_social_routers_are_not_served_with_the_flag_off(monkeypatch):
    """CF-107/CF-109: with `social_enabled` off, /users/* and /posts/* must not
    exist on the served table at all -- the comment above the flag block in
    main.py says /posts/* is the one endpoint that serves other people's
    footage. Nothing else in the suite reads the flag: the `served_app`
    fixture forces it on, and the shape test is blind to the flagged pair.
    Built through `mount_routers` so a `posts.router` that escapes the flag
    block shows up here."""
    import app.main as main

    monkeypatch.setattr(settings, "social_enabled", False)
    probe = FastAPI()
    main.mount_routers(probe)
    served = {r.path for r in probe.routes if hasattr(r, "dependant")}
    social = {p for p in served if p.startswith("/users") or p.startswith("/posts")}
    assert not social, f"social routes served with the flag off: {sorted(social)}"


def test_every_router_module_is_mounted(served_app):
    """A router dropped from `mount_routers` is dropped from both sides of the
    shape comparison, so the shape test cannot see it, and the router's own
    tests mount it on an app of their own. Walk `app.routers` the way
    `_anonymous_reads` does and require each module's router to have a route
    on a probe built with the flag on."""
    served = {r.path for r in served_app.routes if hasattr(r, "dependant")}
    unmounted = []
    for name, router in _router_modules():
        paths = {r.path for r in router.routes if hasattr(r, "dependant")}
        if not paths & served:
            unmounted.append(name)
    assert not unmounted, (
        f"router modules with no route on the served app: {unmounted}"
    )


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


def test_no_anonymous_read_escapes_the_table():
    """What makes the table a guard rather than a list.

    Every other test here walks THROTTLED, so on their own they never examine a
    new anonymous read that nobody added to it: a review round added
    `GET /clips/{clip_id}/probe` with an optional viewer and no limiter, and the
    whole api suite stayed green. This discovers every GET route that resolves no
    credential and requires it to be in the table, where the policy checks apply.
    """
    discovered = _anonymous_reads()
    assert discovered, "the router walk found no anonymous GET route at all"
    missing = discovered - set(THROTTLED)
    assert not missing, (
        f"anonymous reads with no entry in THROTTLED: {sorted(missing)}. Give each "
        "a limiter and a row in the table, or require auth as /download does"
    )
    stale = set(THROTTLED) - discovered
    assert not stale, f"THROTTLED lists routes that are not anonymous reads: {sorted(stale)}"


@pytest.mark.parametrize(
    "path,method", [(p, m) for (p, m) in THROTTLED], ids=lambda v: str(v)
)
def test_every_throttled_read_stays_anonymous(served_app, path, method):
    """The mirror image of the download change, and the likelier regression.

    Someone "making /share consistent with /download" would break every public
    share link. Nothing else in the suite would notice: these routes are
    exercised by calling their coroutines directly, which never resolves an
    auth dependency at all.

    On the served route: `include_router(..., dependencies=[...])` can put auth
    on every public read while the router object shows none (CF-408).
    """
    calls = _dependency_callables(_served_route(served_app, path, method))
    assert get_current_user_id not in calls, (
        f"{method} {path} now requires auth; it is an anonymous read"
    )


def test_the_download_route_requires_auth_instead_of_a_limiter():
    """The one anonymous read handled differently — the seventh of seven when
    CF-186 looked at it, and the eighth now that CF-319 added /playback.

    It requires auth rather than a limiter. It presigns the same object /share
    does, so this pins a decision about who may ask for the attachment URL, not
    a bound on egress. Pinned here so "make the two share routes consistent"
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


def test_write_routes_are_not_throttled(served_app):
    # Writes already require a credential, and uploads are also bounded by the
    # upload quota; the other writes have no limiter here. A limiter on them
    # would be a second, weaker control with its own failure mode. Read off the
    # served app: an `include_router(..., dependencies=[...])` throttles every
    # write on it while the router objects show none (CF-408).
    for route in served_app.routes:
        if not hasattr(route, "dependant"):
            continue
        for method in getattr(route, "methods", set()):
            if method in {"POST", "PATCH", "DELETE", "PUT"}:
                assert _policies_on(route) == [], (
                    f"{method} {route.path} should not be throttled"
                )


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
    _engage(monkeypatch, "rate_limit_share_per_minute", 2)

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
    built_with = {}

    class FakeRedis:
        async def aclose(self):
            closed.append(True)

    def fake_from_url(url, **kwargs):
        built_with.update(kwargs)
        return FakeRedis()

    monkeypatch.setattr(main.aioredis, "from_url", fake_from_url)
    monkeypatch.setattr(ratelimit, "_backend", MemoryBackend())

    async def drive():
        async with main.lifespan(main.app):
            assert isinstance(ratelimit.get_backend(), ratelimit.RedisBackend)
        # Back to in-process on shutdown, so a late call counts locally rather
        # than reaching a closed connection.
        assert isinstance(ratelimit.get_backend(), MemoryBackend)

    asyncio.run(drive())
    assert closed == [True]

    # The socket bounds, asserted here because this is the only place the client
    # is built. redis-py defaults both to None, and an unbounded client is what
    # makes a *hanging* redis defeat the limiter's fail-open branch entirely:
    # that branch catches a raise, and a blackholed connection never raises. The
    # request then blocks on the kernel's TCP retry bound instead. Pinned as
    # "is a bound set at all", not as the number, so tuning it stays free.
    assert built_with.get("socket_timeout"), "no socket_timeout on the client"
    assert built_with.get("socket_connect_timeout"), "no connect timeout"
    assert built_with["socket_timeout"] == ratelimit.REDIS_SOCKET_TIMEOUT_SECONDS
    assert (
        built_with["socket_connect_timeout"] == ratelimit.REDIS_SOCKET_TIMEOUT_SECONDS
    )


def test_a_hanging_backend_is_a_raise_the_limiter_can_fail_open_on(monkeypatch):
    """The half of fail-open that only works if something bounds the wait.

    `_enforce` allows the request when the backend raises. A redis that has gone
    away raises; a redis that accepts the connection and never answers does not,
    and before the socket bounds the request sat there until the kernel gave up.
    This asserts the consequence rather than the configuration: a
    `redis.TimeoutError` -- which is what the socket bound produces, and which is
    an ordinary Exception -- reaches the fail-open branch and the caller is let
    through rather than 500ing.
    """
    import redis.exceptions

    class HungBackend:
        async def hit(self, key, window_seconds):
            raise redis.exceptions.TimeoutError("Timeout reading from socket")

    _engage(monkeypatch, backend=HungBackend())
    limiter = ratelimit.rate_limit(ratelimit.POLICIES["profile"])

    class Req:
        client = type("C", (), {"host": "203.0.113.9"})()
        headers = Headers({})

    assert asyncio.run(limiter(Req())) is None


def test_an_address_keyed_route_resolves_no_auth_dependency(served_app):
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
    reintroducing the dependency anywhere in the chain fails here -- and on the
    SERVED route rather than the router's, so an `include_router(...,
    dependencies=[...])` in main.py, which the router object never sees, fails
    here too (CF-408).
    """
    from app.auth import get_optional_user_id

    checked = 0
    for (path, method), (name, _setting, _default) in THROTTLED.items():
        route = _served_route(served_app, path, method)
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


def test_a_viewer_keyed_limiter_takes_the_viewer_from_auth_not_the_request(monkeypatch):
    """The viewer a content policy keys on must come from the credential.

    `ViewerRateLimiter.__call__` declares `viewer_id` with
    `Depends(get_optional_user_id)`. Drop that default and FastAPI reads
    `viewer_id` from the query string instead, so `?viewer_id=<any uuid>` mints a
    fresh bucket per request and every viewer-keyed route is unthrottled. The
    route-level check above cannot see that: the handlers on those routes declare
    `viewer_id` themselves, so auth stays in their trees either way. So this goes
    over the wire, on a probe whose handler takes no parameters of its own.
    """
    import uuid

    from fastapi import Depends

    from app.auth import get_optional_user_id

    _engage(monkeypatch, "rate_limit_share_per_minute", 2)

    app = FastAPI()

    @app.get(
        "/probe",
        dependencies=[Depends(ratelimit.rate_limit(ratelimit.POLICIES["share"]))],
    )
    async def probe():
        return {"ok": True}

    app.dependency_overrides[get_optional_user_id] = lambda: None

    with TestClient(app) as client:
        codes = [
            client.get(f"/probe?viewer_id={uuid.uuid4()}").status_code
            for _ in range(3)
        ]
    assert codes == [200, 200, 429], (
        f"{codes}: a query-string viewer_id minted its own bucket, so the "
        "viewer-keyed limiter is not taking the viewer from get_optional_user_id"
    )


def test_the_limiter_is_resolved_before_anything_a_refusal_should_not_pay_for(
    served_app,
):
    """On an address-keyed route, a 429 must not have cost a JWT verification or
    a database write first. (A viewer-keyed route resolves the viewer before it
    counts, by design: that is what gives a signed-in caller their own bucket.)

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

    On the served route, not the router's. `include_router` puts a
    `dependencies=[...]` given at the call ahead of the router-declared ones
    (fastapi 0.115.6, ``routing.py`` ``include_router``, then
    ``APIRoute.__init__`` inserting each at index 0), so one added in main.py
    is at position 0 of the served route and the router object is none the
    wiser. A review round measured that shape at 12 auth resolutions and 12
    commits across 12 requests on a 2/min budget, suite green (CF-408).
    """
    checked = 0
    for (path, method), (name, _setting, _default) in THROTTLED.items():
        route = _served_route(served_app, path, method)
        deps = route.dependant.dependencies
        # The route's own limiter by policy name, not any `RateLimiter`: an
        # include-level limiter under another policy would otherwise sit at 0
        # and satisfy the guard with the route's limiter behind it.
        at = [
            i
            for i, dep in enumerate(deps)
            if isinstance(dep.call, RateLimiter) and dep.call.policy.name == name
        ]
        assert at, f"{method} {path} resolves no {name} limiter at all"
        assert at[0] == 0, (
            f"{method} {path} resolves "
            f"{[getattr(d.call, '__name__', d.call) for d in deps[: at[0]]]} "
            f"before its {name} limiter, so a refused request pays for them"
        )
        checked += 1
    assert checked == len(THROTTLED)


def test_a_refused_request_opens_no_session_and_commits_nothing(served_app, monkeypatch):
    """The cost itself, not the shape that implies it.

    The position test above reads `route.dependant.dependencies`, and a
    dependency can sit BELOW the limiter in that list and still run before it:
    `solve_dependencies` resolves each entry's own sub-dependencies first, so a
    `Depends(get_db)` on `RateLimiter.__call__` opens a session ahead of the
    counter while the limiter keeps position 0. Nothing reading the tree can
    tell. So this drives the served app over the wire with the session
    dependency replaced by one that only logs, and asserts that the ten refused
    requests on a 2/min budget add nothing to that log: no session opened, no
    commit, nothing. The two allowed requests do open one each, which is what
    makes the count a measurement rather than a session that never ran.

    Through the real route and the real handler, because the claim is about
    what `GET /users/{handle}` costs as served, not what a probe would cost.
    """
    from app.database import get_db

    _engage(monkeypatch, "rate_limit_profile_per_minute", 2)

    log: list[str] = []

    class NoRow:
        # The one verb `_by_handle` calls on the result; anything else the
        # handler reached would be an AttributeError, a 500 under
        # `raise_server_exceptions=False`, and a failed `[404, 404]` below.
        def scalar_one_or_none(self):
            return None

    class CountingSession:
        """Every verb the handler or an auth dependency could reach, each one
        a log line. `execute` answers "no row" so `get_profile` 404s through
        `_by_handle` rather than reaching anything that wants a real model."""

        async def commit(self):
            log.append("commit")

        async def rollback(self):
            log.append("rollback")

        async def execute(self, *args, **kwargs):
            log.append("execute")
            return NoRow()

        async def scalar(self, *args, **kwargs):
            log.append("scalar")
            return None

        async def get(self, *args, **kwargs):
            log.append("get")
            return None

        async def refresh(self, *args, **kwargs):
            log.append("refresh")

        def add(self, *args, **kwargs):
            log.append("add")

    async def counting_db():
        log.append("open")
        yield CountingSession()

    served_app.dependency_overrides[get_db] = counting_db

    # `raise_server_exceptions=False` so a handler that blows up on the fake
    # session reads as a 500 in `allowed` below, not as a traceback that hides
    # which request it was.
    with TestClient(served_app, raise_server_exceptions=False) as client:
        allowed = [client.get("/users/someone").status_code for _ in range(2)]
        # 404, not 429 and not 500: the request reached the handler, the handler
        # ran its lookup on the counting session, and the session answered
        # "no such handle". That is the cost an ALLOWED request pays.
        assert allowed == [404, 404], allowed
        paid = list(log)
        refused = [client.get("/users/someone").status_code for _ in range(10)]
    assert refused == [429] * 10, refused
    assert log == paid, (
        f"ten refused requests cost {log[len(paid):]}: a 429 must not open a "
        "session, commit, or touch the database at all"
    )
    # Already implied: `allowed == [404, 404]` is the path that never commits
    # and `log == paid` holds the refusals to nothing. Kept as the claim in
    # plain words, not as a check that could stand alone -- do not weaken
    # `log == paid` to a commit-only look at `log[len(paid):]`, which is the
    # shape mutation A (a session `open` and no commit) slips through.
    assert "commit" not in log
    # Not vacuous: the override really was the session the allowed requests
    # used, once each. Zero here would mean the handler never saw it, and the
    # assertion above would be holding nothing.
    assert log.count("open") == 2, log


def test_a_backend_that_never_answers_does_not_hold_the_request(monkeypatch):
    """The socket bounds are per OPERATION; this is the bound per REQUEST.

    A server that answers every operation just inside the socket timeout still
    stretches one `hit()` well past it -- connect, handshake and each read get
    their own second, and a review round measured 6.31s cold against a
    0.9s-per-reply server. What a caller waiting on a public read experiences is
    the whole call, so the whole call is what `REQUEST_BUDGET_SECONDS` bounds.

    Driven with a backend that simply never returns, which no socket timeout can
    reach: the `MemoryBackend` path has no socket at all, and a custom `Backend`
    implementation is free to block. The test's own `wait_for` is the safety
    net -- without a bound in `_enforce` this fails as a timeout rather than
    hanging the suite forever.
    """
    monkeypatch.setattr(ratelimit, "REQUEST_BUDGET_SECONDS", 0.05)

    class NeverAnswers:
        async def hit(self, key, window_seconds):
            await asyncio.Event().wait()

    _engage(monkeypatch, backend=NeverAnswers())
    limiter = ratelimit.rate_limit(ratelimit.POLICIES["profile"])

    class Req:
        client = type("C", (), {"host": "203.0.113.11"})()
        headers = Headers({})

    async def drive():
        # Twenty times the budget: generous enough that a slow machine cannot
        # fail this, tight enough that an unbounded `_enforce` cannot pass it.
        return await asyncio.wait_for(limiter(Req()), timeout=1.0)

    assert asyncio.run(drive()) is None


# ── the counts written into prose ───────────────────────────────────────────
# Three review rounds on CF-319 raised a stale count in this surface's prose,
# each time in a different file, because adding one throttled route makes every
# spelled-out total wrong at once. Two rounds of that were fixed by correcting
# the file the finding pointed at, which is why a third round found four more.
#
# Where the number is incidental to the sentence it has since been removed
# (config.py's settings header, README's flag row, access.py's second mention).
# Where it is the record of what the anonymous surface *is*, it stays spelled
# out — and is asserted here against THROTTLED, so the next route to join
# cannot leave it behind quietly.

_NUMBER_WORDS = {
    "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
}


def _spelled_count(text: str, pattern: str, where: str) -> int:
    found = re.findall(pattern, text, re.IGNORECASE)
    assert found, (
        f"{where}: no sentence matching {pattern!r}. If that prose was "
        "deliberately reworded, update this pattern or drop its row below; "
        "do not delete the assertion to make the suite green."
    )
    assert len(set(found)) == 1, f"{where}: disagreeing counts {sorted(set(found))}"
    word = found[0].lower()
    assert word in _NUMBER_WORDS, f"{where}: unhandled number word {word!r}"
    return _NUMBER_WORDS[word]


def test_the_throttled_count_in_prose_matches_the_table():
    """`len(THROTTLED)` is the truth; these sentences must agree with it."""
    throttled = len(THROTTLED)

    assert _spelled_count(
        access.__doc__ or "",
        r"All (\w+) are throttled per caller",
        "services/access.py module docstring",
    ) == throttled

    assert _spelled_count(
        access.__doc__ or "",
        r"(\w+) endpoints where there were none",
        "services/access.py module docstring",
    ) == throttled

    assert _spelled_count(
        __doc__ or "",
        r"keeps the (\w+) throttled coroutine signatures",
        "this module's docstring",
    ) == throttled


def test_the_uncredentialed_count_in_prose_counts_download_too():
    """ratelimit.py's opening total is the throttled set plus /download.

    /download answers without a credential in the sense that sentence means —
    it is on the anonymous read surface CF-186 assessed — but it was given auth
    rather than a limiter, so it is in the total and not in THROTTLED. Pinning
    the relationship rather than the literal is what keeps the two numbers from
    being corrected independently and drifting apart.
    """
    assert _spelled_count(
        ratelimit.__doc__ or "",
        r"(\w+) endpoints answer without a credential",
        "services/ratelimit.py module docstring",
    ) == len(THROTTLED) + 1
