from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import games, clips, players, dead_time, collections

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
app.include_router(dead_time.router)
app.include_router(collections.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
