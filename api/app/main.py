from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.routers import games, clips, players, collections

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


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/db")
async def health_db(db: AsyncSession = Depends(get_db)):
    """Touches the database (SELECT 1) so uptime pingers can keep Supabase
    from auto-pausing. Unlike /health, this fails if the DB is unreachable."""
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
