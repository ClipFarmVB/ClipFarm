import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.database import get_db
from app.models.player import Player
from app.models.team import Team
from app.schemas.player import PlayerOut, PlayerCreate

router = APIRouter(prefix="/players", tags=["players"])

DB = Annotated[AsyncSession, Depends(get_db)]
UserId = Annotated[uuid.UUID, Depends(get_current_user_id)]


async def _get_owned_player(player_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> Player:
    player = await db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    if player.team_id is None:
        raise HTTPException(status_code=404, detail="Player not found")
    team = await db.get(Team, player.team_id)
    if not team or team.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


@router.get("", response_model=list[PlayerOut])
async def list_players(
    db: DB,
    user_id: UserId,
    team_id: Annotated[uuid.UUID | None, Query()] = None,
):
    # Only return players belonging to teams the user owns
    owned_team_ids_q = await db.execute(select(Team.id).where(Team.owner_id == user_id))
    owned_team_ids = [r[0] for r in owned_team_ids_q.all()]
    if not owned_team_ids:
        return []

    q = select(Player).where(Player.team_id.in_(owned_team_ids))
    if team_id:
        if team_id not in owned_team_ids:
            return []
        q = q.where(Player.team_id == team_id)
    result = await db.execute(q.order_by(Player.jersey_number.nullslast(), Player.name))
    return result.scalars().all()


@router.post("", response_model=PlayerOut, status_code=status.HTTP_201_CREATED)
async def create_player(body: PlayerCreate, db: DB, user_id: UserId):
    # Verify the team being assigned belongs to the user
    if body.team_id is None:
        raise HTTPException(status_code=400, detail="team_id is required")
    team = await db.get(Team, body.team_id)
    if not team or team.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Team not found")

    player = Player(**body.model_dump())
    db.add(player)
    await db.commit()
    await db.refresh(player)
    return player


@router.patch("/{player_id}", response_model=PlayerOut)
async def update_player(player_id: uuid.UUID, body: PlayerCreate, db: DB, user_id: UserId):
    player = await _get_owned_player(player_id, user_id, db)

    # If reassigning team, verify new team is also owned by user
    updates = body.model_dump(exclude_unset=True)
    if "team_id" in updates and updates["team_id"] is not None:
        new_team = await db.get(Team, updates["team_id"])
        if not new_team or new_team.owner_id != user_id:
            raise HTTPException(status_code=404, detail="Team not found")

    for k, v in updates.items():
        setattr(player, k, v)
    await db.commit()
    await db.refresh(player)
    return player
