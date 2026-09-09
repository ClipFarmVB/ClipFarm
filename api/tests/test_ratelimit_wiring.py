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

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.routers import clips, games, posts, profiles
from app.services import ratelimit
from app.services.ratelimit import MemoryBackend, RateLimiter

# path -> the policy that path must carry. Both halves matter: a route with the
# wrong policy is throttled at somebody else's number.
THROTTLED = {
    ("/users/{handle}", "GET"): "profile",
    ("/posts", "GET"): "user_posts",
    ("/games/{game_id}", "GET"): "game",
    ("/games/{game_id}/clips", "GET"): "games_clips",
    ("/clips/{clip_id}/share", "GET"): "share",
    ("/posts/{post_id}", "GET"): "post",
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


def _policies_on(route) -> list[str]:
    return [
        d.dependency.policy.name
        for d in (route.dependencies or [])
        if isinstance(getattr(d, "dependency", None), RateLimiter)
    ]


@pytest.mark.parametrize("path,method,expected", [(p, m, n) for (p, m), n in THROTTLED.items()])
def test_every_anonymous_read_carries_its_limiter(path, method, expected):
    found = [
        _policies_on(route)
        for route_path, route_method, route in _all_routes()
        if (route_path, route_method) == (path, method)
    ]
    assert found, f"{method} {path} is not registered on any router"
    assert found[0] == [expected], f"{method} {path} carries {found[0]}, expected [{expected}]"


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
