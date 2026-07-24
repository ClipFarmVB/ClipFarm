import asyncio
import logging

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, get_db
from app.observability import init_sentry
from app.routers import games, clips, players, collections

logger = logging.getLogger(__name__)

# Wire error monitoring before the app is built so startup errors are captured.
init_sentry("api")

app = FastAPI(title="ClipFarm API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(games.router)
app.include_router(clips.router)
app.include_router(players.router)
app.include_router(collections.router)


async def _check_db() -> bool:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("health: database check failed")
        return False


async def _check_redis() -> bool:
    client = aioredis.from_url(settings.redis_url)
    try:
        await asyncio.wait_for(client.ping(), timeout=2.0)
        return True
    except Exception:
        logger.exception("health: redis check failed")
        return False
    finally:
        await client.aclose()


@app.get("/health")
async def health(response: Response):
    """Liveness + readiness: reports Postgres and Redis reachability. Returns
    503 when a dependency is down so an external uptime monitor can alert."""
    db_ok, redis_ok = await asyncio.gather(_check_db(), _check_redis())
    healthy = db_ok and redis_ok
    if not healthy:
        response.status_code = 503
    return {
        "status": "ok" if healthy else "degraded",
        "checks": {
            "database": "ok" if db_ok else "down",
            "redis": "ok" if redis_ok else "down",
        },
    }


@app.get("/health/db")
async def health_db(db: AsyncSession = Depends(get_db)):
    """Touches the database (SELECT 1) so uptime pingers can keep Supabase from
    auto-pausing. Unlike /health, this checks Postgres only and ignores Redis,
    so a Redis outage won't mask the DB ping or turn it into a 503."""
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


if settings.debug:

    @app.get("/debug/sentry-error")
    async def debug_sentry_error():
        """Deliberately raises so we can confirm errors reach Sentry with a
        stack trace. Registered only when debug is enabled."""
        raise RuntimeError("CF-89 test error from the API (debug only)")
