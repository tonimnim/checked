from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Dict
import math

from app.database import get_db
from app.models.player import Player
from app.models.tournament import Tournament, TournamentStatus
from app.utils.kenya import KENYA_COUNTIES, KENYA_REGIONS

router = APIRouter(prefix="/api/utils", tags=["Utilities"])


@router.get("/counties", response_model=List[str])
async def get_counties():
    """Get list of all Kenya counties"""
    return KENYA_COUNTIES


@router.get("/regions", response_model=Dict[str, List[str]])
async def get_regions():
    """Get Kenya regions with their counties"""
    return KENYA_REGIONS


@router.get("/time-controls")
async def get_time_controls():
    """Get common time control options"""
    return {
        "bullet": [
            {"value": "1+0", "label": "1 min (Bullet)"},
            {"value": "1+1", "label": "1|1 (Bullet)"},
            {"value": "2+1", "label": "2|1 (Bullet)"},
        ],
        "blitz": [
            {"value": "3+0", "label": "3 min (Blitz)"},
            {"value": "3+2", "label": "3|2 (Blitz)"},
            {"value": "5+0", "label": "5 min (Blitz)"},
            {"value": "5+3", "label": "5|3 (Blitz)"},
        ],
        "rapid": [
            {"value": "10+0", "label": "10 min (Rapid)"},
            {"value": "10+5", "label": "10|5 (Rapid)"},
            {"value": "15+10", "label": "15|10 (Rapid)"},
            {"value": "30+0", "label": "30 min (Rapid)"},
        ],
        "classical": [
            {"value": "60+0", "label": "60 min (Classical)"},
            {"value": "90+30", "label": "90|30 (Classical)"},
        ]
    }


@router.get("/tournament-formats")
async def get_tournament_formats():
    """Get available tournament formats with descriptions"""
    return [
        {
            "value": "swiss",
            "label": "Swiss System",
            "description": "Players with similar scores are paired. Best for large tournaments (10+ players).",
            "recommended_players": "10-500+",
            "rounds_formula": "Recommended: log2(players) + 1, e.g., 5-7 rounds for 32 players"
        },
        {
            "value": "round_robin",
            "label": "Round Robin",
            "description": "Everyone plays everyone exactly once. Best for small tournaments.",
            "recommended_players": "4-12",
            "rounds_formula": "Required: N-1 rounds (N players)"
        }
    ]


@router.get("/calculate-rounds")
async def calculate_rounds(format: str, player_count: int):
    """
    Calculate recommended/required rounds for a tournament.

    - format: 'swiss' or 'round_robin'
    - player_count: expected number of players
    """
    if player_count < 2:
        return {"error": "Need at least 2 players"}

    if format == "round_robin":
        # Round Robin: everyone plays everyone
        rounds_needed = player_count - 1 if player_count % 2 == 0 else player_count
        total_games = player_count * (player_count - 1) // 2

        return {
            "format": "round_robin",
            "player_count": player_count,
            "rounds_required": rounds_needed,
            "total_games": total_games,
            "games_per_round": player_count // 2,
            "warning": "Round Robin not recommended for 12+ players" if player_count > 12 else None
        }

    elif format == "swiss":
        # Swiss: log2(players) rounds ensures unique winner
        # Common practice: add 1-2 extra rounds
        min_rounds = max(3, math.ceil(math.log2(player_count)))
        recommended_rounds = min_rounds + 1
        max_rounds = min_rounds + 3

        return {
            "format": "swiss",
            "player_count": player_count,
            "minimum_rounds": min_rounds,
            "recommended_rounds": recommended_rounds,
            "maximum_rounds": max_rounds,
            "games_per_round": player_count // 2,
            "note": f"With {recommended_rounds} rounds, expect a clear winner"
        }

    else:
        return {"error": f"Unknown format: {format}. Use 'swiss' or 'round_robin'"}


@router.get("/public-stats")
async def get_public_stats(db: AsyncSession = Depends(get_db)):
    """
    Get public statistics for homepage.
    Returns player count, tournament count, county count.
    """
    # Active players count
    result = await db.execute(
        select(func.count(Player.id)).where(Player.is_active == True)
    )
    player_count = result.scalar() or 0

    # Total tournaments
    result = await db.execute(select(func.count(Tournament.id)))
    tournament_count = result.scalar() or 0

    # Completed tournaments
    result = await db.execute(
        select(func.count(Tournament.id)).where(
            Tournament.status == TournamentStatus.COMPLETED
        )
    )
    completed_count = result.scalar() or 0

    # Active tournaments (live)
    result = await db.execute(
        select(func.count(Tournament.id)).where(
            Tournament.status == TournamentStatus.ACTIVE
        )
    )
    active_count = result.scalar() or 0

    # Open for registration
    result = await db.execute(
        select(func.count(Tournament.id)).where(
            Tournament.status == TournamentStatus.REGISTRATION
        )
    )
    open_count = result.scalar() or 0

    # Unique counties with players
    result = await db.execute(
        select(func.count(func.distinct(Player.county))).where(
            Player.county.isnot(None),
            Player.is_active == True
        )
    )
    county_count = result.scalar() or 0

    return {
        "players": player_count,
        "tournaments": tournament_count,
        "completed_tournaments": completed_count,
        "active_tournaments": active_count,
        "open_tournaments": open_count,
        "counties": county_count or 47,  # Default to 47 if no data
    }


@router.get("/upcoming-tournaments")
async def get_upcoming_tournaments(
    limit: int = 3,
    db: AsyncSession = Depends(get_db)
):
    """
    Get upcoming tournaments for homepage.
    Returns tournaments that are open for registration or starting soon.
    """
    # Get open tournaments ordered by start date
    result = await db.execute(
        select(Tournament)
        .where(Tournament.status == TournamentStatus.REGISTRATION)
        .order_by(Tournament.start_date.asc())
        .limit(limit)
    )
    tournaments = result.scalars().all()

    return [
        {
            "id": t.id,
            "name": t.name,
            "format": t.format.value if hasattr(t.format, 'value') else t.format,
            "time_control": t.time_control,
            "total_rounds": t.total_rounds,
            "max_players": t.max_players,
            "start_date": t.start_date.isoformat() if t.start_date else None,
            "registration_close": t.registration_close.isoformat() if t.registration_close else None,
        }
        for t in tournaments
    ]
