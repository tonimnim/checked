from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import List, Optional

from app.database import get_db
from app.models.player import Player
from app.models.tournament import Tournament, TournamentPlayer, TournamentStatus
from app.models.pairing import Pairing, GameResult
from app.schemas.player import PlayerResponse, PlayerUpdate
from app.services.auth import get_current_player, get_current_admin
from app.services.chess_com import chess_com_service

router = APIRouter(prefix="/api/players", tags=["Players"])


@router.get("/leaderboard/global")
async def get_global_leaderboard(
    sort_by: str = "wins",  # wins, win_rate, tournaments, podiums
    county: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
    db: AsyncSession = Depends(get_db)
):
    """
    Get global leaderboard - all-time player rankings.

    Sort options:
    - wins: Total wins
    - win_rate: Win percentage
    - tournaments: Tournaments played
    - podiums: Podium finishes (1st, 2nd, 3rd)
    - score: Total score accumulated

    Optional county filter for regional leaderboards.
    """
    # Get all players with their aggregated stats
    result = await db.execute(
        select(Player).where(Player.is_active == True)
    )
    players = result.scalars().all()

    # Filter by county if specified
    if county:
        players = [p for p in players if p.county and p.county.lower() == county.lower()]

    leaderboard = []

    for player in players:
        # Get player's tournament stats
        result = await db.execute(
            select(TournamentPlayer)
            .options(selectinload(TournamentPlayer.tournament))
            .where(
                TournamentPlayer.player_id == player.id,
                TournamentPlayer.is_withdrawn == False
            )
        )
        participations = result.scalars().all()

        if not participations:
            continue  # Skip players with no tournament history

        total_tournaments = len(participations)
        completed = [p for p in participations if p.tournament.status == TournamentStatus.COMPLETED]

        total_wins = sum(p.wins for p in participations)
        total_draws = sum(p.draws for p in participations)
        total_losses = sum(p.losses for p in participations)
        total_games = total_wins + total_draws + total_losses
        total_score = sum(p.score for p in participations)

        win_rate = round((total_wins / total_games * 100), 1) if total_games > 0 else 0

        first_places = sum(1 for p in completed if p.final_rank == 1)
        second_places = sum(1 for p in completed if p.final_rank == 2)
        third_places = sum(1 for p in completed if p.final_rank == 3)
        podiums = first_places + second_places + third_places

        leaderboard.append({
            "player_id": player.id,
            "chess_com_username": player.chess_com_username,
            "chess_com_avatar": player.chess_com_avatar,
            "county": player.county,
            "club": player.club,
            "chess_com_status": player.chess_com_status,
            "stats": {
                "tournaments_played": total_tournaments,
                "total_games": total_games,
                "wins": total_wins,
                "draws": total_draws,
                "losses": total_losses,
                "win_rate": win_rate,
                "total_score": total_score,
                "first_places": first_places,
                "second_places": second_places,
                "third_places": third_places,
                "podium_finishes": podiums
            }
        })

    # Sort based on criteria
    sort_keys = {
        "wins": lambda x: (-x["stats"]["wins"], -x["stats"]["win_rate"]),
        "win_rate": lambda x: (-x["stats"]["win_rate"], -x["stats"]["total_games"]),
        "tournaments": lambda x: (-x["stats"]["tournaments_played"], -x["stats"]["wins"]),
        "podiums": lambda x: (-x["stats"]["podium_finishes"], -x["stats"]["first_places"]),
        "score": lambda x: (-x["stats"]["total_score"], -x["stats"]["wins"])
    }

    sort_fn = sort_keys.get(sort_by, sort_keys["wins"])
    leaderboard.sort(key=sort_fn)

    # Add ranks
    for i, entry in enumerate(leaderboard):
        entry["rank"] = i + 1

    # Paginate
    total = len(leaderboard)
    leaderboard = leaderboard[skip:skip + limit]

    return {
        "sort_by": sort_by,
        "county_filter": county,
        "total": total,
        "showing": len(leaderboard),
        "leaderboard": leaderboard
    }


@router.get("/", response_model=List[PlayerResponse])
async def list_players(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_player)  # Require auth
):
    """List all registered players (paginated)"""
    result = await db.execute(
        select(Player)
        .where(Player.is_active == True)
        .offset(skip)
        .limit(limit)
        .order_by(Player.created_at.desc())
    )
    players = result.scalars().all()
    return [PlayerResponse.model_validate(p) for p in players]


@router.get("/{player_id}", response_model=PlayerResponse)
async def get_player(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_player)
):
    """Get a player by ID"""
    result = await db.execute(
        select(Player).where(Player.id == player_id)
    )
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Player not found"
        )

    return PlayerResponse.model_validate(player)


@router.patch("/me", response_model=PlayerResponse)
async def update_profile(
    update_data: PlayerUpdate,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Update current player's profile"""
    update_dict = update_data.model_dump(exclude_unset=True)

    for field, value in update_dict.items():
        setattr(current_player, field, value)

    await db.commit()
    await db.refresh(current_player)

    return PlayerResponse.model_validate(current_player)


@router.post("/me/refresh-avatar", response_model=PlayerResponse)
async def refresh_avatar(
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Refresh avatar from Chess.com (in case user changed it)"""
    profile = await chess_com_service.get_player_profile(current_player.chess_com_username)

    if profile:
        current_player.chess_com_avatar = profile.avatar
        current_player.chess_com_status = profile.status
        await db.commit()
        await db.refresh(current_player)

    return PlayerResponse.model_validate(current_player)


@router.post("/me/refresh-ratings", response_model=PlayerResponse)
async def refresh_my_ratings(
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Refresh current player's ratings from Chess.com"""
    from datetime import datetime

    stats = await chess_com_service.get_player_stats(current_player.chess_com_username)

    if stats:
        current_player.rating_rapid = stats.chess_rapid
        current_player.rating_blitz = stats.chess_blitz
        current_player.rating_bullet = stats.chess_bullet
        current_player.ratings_updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(current_player)

    return PlayerResponse.model_validate(current_player)


@router.post("/{player_id}/refresh-ratings", response_model=PlayerResponse)
async def refresh_player_ratings(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_admin)
):
    """Refresh a player's ratings from Chess.com (admin only)"""
    from datetime import datetime

    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    stats = await chess_com_service.get_player_stats(player.chess_com_username)

    if stats:
        player.rating_rapid = stats.chess_rapid
        player.rating_blitz = stats.chess_blitz
        player.rating_bullet = stats.chess_bullet
        player.ratings_updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(player)

    return PlayerResponse.model_validate(player)


@router.post("/refresh-all-ratings")
async def refresh_all_ratings(
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_admin)
):
    """Refresh all players' ratings from Chess.com (admin only). Returns count of updated players."""
    from datetime import datetime
    import asyncio

    result = await db.execute(select(Player).where(Player.is_active == True))
    players = result.scalars().all()

    updated = 0
    failed = 0

    for player in players:
        try:
            stats = await chess_com_service.get_player_stats(player.chess_com_username)
            if stats:
                player.rating_rapid = stats.chess_rapid
                player.rating_blitz = stats.chess_blitz
                player.rating_bullet = stats.chess_bullet
                player.ratings_updated_at = datetime.utcnow()
                updated += 1
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.5)
        except Exception:
            failed += 1

    await db.commit()

    return {
        "updated": updated,
        "failed": failed,
        "total": len(players)
    }


@router.get("/username/{username}", response_model=PlayerResponse)
async def get_player_by_username(
    username: str,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_player)
):
    """Get a player by Chess.com username"""
    result = await db.execute(
        select(Player).where(Player.chess_com_username == username.lower())
    )
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Player not found"
        )

    return PlayerResponse.model_validate(player)


# Admin endpoints

@router.patch("/{player_id}/admin", response_model=PlayerResponse)
async def toggle_admin(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_admin)
):
    """Toggle admin status for a player (admin only)"""
    result = await db.execute(
        select(Player).where(Player.id == player_id)
    )
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Player not found"
        )

    player.is_admin = not player.is_admin
    await db.commit()
    await db.refresh(player)

    return PlayerResponse.model_validate(player)


@router.patch("/{player_id}/deactivate", response_model=PlayerResponse)
async def deactivate_player(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_admin)
):
    """Deactivate a player account (admin only)"""
    result = await db.execute(
        select(Player).where(Player.id == player_id)
    )
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Player not found"
        )

    player.is_active = False
    await db.commit()
    await db.refresh(player)

    return PlayerResponse.model_validate(player)


# Tournament History & Stats

@router.get("/me/tournaments")
async def get_my_tournament_history(
    status_filter: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Get current player's tournament history.

    Optional filter by status: registration, active, completed
    """
    query = (
        select(TournamentPlayer)
        .options(selectinload(TournamentPlayer.tournament))
        .where(TournamentPlayer.player_id == current_player.id)
    )

    if status_filter:
        try:
            status_enum = TournamentStatus(status_filter)
            query = query.join(Tournament).where(Tournament.status == status_enum)
        except ValueError:
            pass

    query = query.order_by(TournamentPlayer.joined_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    tournament_players = result.scalars().all()

    history = []
    for tp in tournament_players:
        t = tp.tournament
        history.append({
            "tournament_id": t.id,
            "name": t.name,
            "format": t.format.value,
            "status": t.status.value,
            "start_date": t.start_date,
            "end_date": t.end_date,
            "total_rounds": t.total_rounds,
            "current_round": t.current_round,
            "player_stats": {
                "rank": tp.final_rank,
                "score": tp.score,
                "wins": tp.wins,
                "draws": tp.draws,
                "losses": tp.losses,
                "is_withdrawn": tp.is_withdrawn
            },
            "joined_at": tp.joined_at
        })

    return {
        "count": len(history),
        "tournaments": history
    }


@router.get("/me/stats")
async def get_my_stats(
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Get current player's overall statistics across all tournaments.
    """
    # Get all tournament participations
    result = await db.execute(
        select(TournamentPlayer)
        .options(selectinload(TournamentPlayer.tournament))
        .where(
            TournamentPlayer.player_id == current_player.id,
            TournamentPlayer.is_withdrawn == False
        )
    )
    participations = result.scalars().all()

    # Aggregate stats
    total_tournaments = len(participations)
    completed_tournaments = sum(1 for p in participations if p.tournament.status == TournamentStatus.COMPLETED)

    total_score = sum(p.score for p in participations)
    total_wins = sum(p.wins for p in participations)
    total_draws = sum(p.draws for p in participations)
    total_losses = sum(p.losses for p in participations)
    total_games = total_wins + total_draws + total_losses

    # Calculate win rate
    win_rate = round((total_wins / total_games * 100), 1) if total_games > 0 else 0

    # Count podium finishes (1st, 2nd, 3rd)
    first_places = sum(1 for p in participations if p.final_rank == 1)
    second_places = sum(1 for p in participations if p.final_rank == 2)
    third_places = sum(1 for p in participations if p.final_rank == 3)

    # Best and worst ranks
    ranks = [p.final_rank for p in participations if p.final_rank and p.tournament.status == TournamentStatus.COMPLETED]
    best_rank = min(ranks) if ranks else None

    return {
        "player_id": current_player.id,
        "chess_com_username": current_player.chess_com_username,
        "tournaments": {
            "total": total_tournaments,
            "completed": completed_tournaments,
            "active": total_tournaments - completed_tournaments
        },
        "games": {
            "total": total_games,
            "wins": total_wins,
            "draws": total_draws,
            "losses": total_losses,
            "win_rate": win_rate
        },
        "achievements": {
            "first_places": first_places,
            "second_places": second_places,
            "third_places": third_places,
            "podium_finishes": first_places + second_places + third_places,
            "best_rank": best_rank
        },
        "total_score": total_score
    }


@router.get("/{player_id}/tournaments")
async def get_player_tournament_history(
    player_id: str,
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_player)
):
    """Get another player's tournament history (public tournaments only)"""
    # Verify player exists
    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    query = (
        select(TournamentPlayer)
        .options(selectinload(TournamentPlayer.tournament))
        .where(TournamentPlayer.player_id == player_id)
        .order_by(TournamentPlayer.joined_at.desc())
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(query)
    tournament_players = result.scalars().all()

    history = []
    for tp in tournament_players:
        t = tp.tournament
        history.append({
            "tournament_id": t.id,
            "name": t.name,
            "format": t.format.value,
            "status": t.status.value,
            "start_date": t.start_date,
            "player_stats": {
                "rank": tp.final_rank,
                "score": tp.score,
                "wins": tp.wins,
                "draws": tp.draws,
                "losses": tp.losses
            }
        })

    return {
        "player_id": player_id,
        "chess_com_username": player.chess_com_username,
        "count": len(history),
        "tournaments": history
    }


@router.get("/{player_id}/stats")
async def get_player_stats(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_player)
):
    """Get another player's statistics"""
    # Verify player exists
    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # Get all tournament participations
    result = await db.execute(
        select(TournamentPlayer)
        .options(selectinload(TournamentPlayer.tournament))
        .where(
            TournamentPlayer.player_id == player_id,
            TournamentPlayer.is_withdrawn == False
        )
    )
    participations = result.scalars().all()

    total_tournaments = len(participations)
    completed_tournaments = sum(1 for p in participations if p.tournament.status == TournamentStatus.COMPLETED)

    total_wins = sum(p.wins for p in participations)
    total_draws = sum(p.draws for p in participations)
    total_losses = sum(p.losses for p in participations)
    total_games = total_wins + total_draws + total_losses

    win_rate = round((total_wins / total_games * 100), 1) if total_games > 0 else 0

    first_places = sum(1 for p in participations if p.final_rank == 1)
    second_places = sum(1 for p in participations if p.final_rank == 2)
    third_places = sum(1 for p in participations if p.final_rank == 3)

    return {
        "player_id": player_id,
        "chess_com_username": player.chess_com_username,
        "tournaments_played": total_tournaments,
        "games": {
            "total": total_games,
            "wins": total_wins,
            "draws": total_draws,
            "losses": total_losses,
            "win_rate": win_rate
        },
        "achievements": {
            "first_places": first_places,
            "second_places": second_places,
            "third_places": third_places,
            "podium_finishes": first_places + second_places + third_places
        }
    }
