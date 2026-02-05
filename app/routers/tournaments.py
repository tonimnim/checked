from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import List, Optional
import json

from app.database import get_db
from app.models.player import Player
from app.models.tournament import Tournament, TournamentPlayer, TournamentStatus, GenderRestriction
from app.schemas.tournament import (
    TournamentCreate,
    TournamentResponse,
    TournamentUpdate,
    TournamentPlayerResponse,
    StandingsResponse,
)
from app.services.auth import get_current_player, get_current_admin
from app.services.chess_com import chess_com_service
from app.utils.kenya import expand_county_restrictions

router = APIRouter(prefix="/api/tournaments", tags=["Tournaments"])


def tournament_to_response(tournament: Tournament, player_count: int = 0) -> TournamentResponse:
    """Convert Tournament model to response schema"""
    return TournamentResponse(
        id=tournament.id,
        name=tournament.name,
        description=tournament.description,
        format=tournament.format,
        total_rounds=tournament.total_rounds,
        current_round=tournament.current_round,
        time_control=tournament.time_control,
        status=tournament.status,
        max_players=tournament.max_players,
        registration_open=tournament.registration_open,
        registration_close=tournament.registration_close,
        start_date=tournament.start_date,
        end_date=tournament.end_date,
        # Restrictions
        county_restrictions=tournament.get_county_restrictions(),
        min_rating=tournament.min_rating,
        max_rating=tournament.max_rating,
        min_age=tournament.min_age,
        max_age=tournament.max_age,
        gender_restriction=tournament.gender_restriction,
        allowed_clubs=tournament.get_allowed_clubs(),
        # Payment
        entry_fee=tournament.entry_fee,
        prize_pool=tournament.prize_pool,
        is_paid=tournament.is_paid,
        player_count=player_count,
        created_at=tournament.created_at,
    )


@router.post("/", response_model=TournamentResponse, status_code=status.HTTP_201_CREATED)
async def create_tournament(
    tournament_data: TournamentCreate,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_admin)  # Admin only for now
):
    """Create a new tournament (admin only)"""
    tournament = Tournament(
        name=tournament_data.name,
        description=tournament_data.description,
        format=tournament_data.format,
        total_rounds=tournament_data.total_rounds,
        time_control=tournament_data.time_control,
        max_players=tournament_data.max_players,
        registration_close=tournament_data.registration_close,
        start_date=tournament_data.start_date,
        # Restrictions
        min_rating=tournament_data.min_rating,
        max_rating=tournament_data.max_rating,
        min_age=tournament_data.min_age,
        max_age=tournament_data.max_age,
        gender_restriction=tournament_data.gender_restriction,
        # Payment
        entry_fee=tournament_data.entry_fee,
        prize_pool=tournament_data.prize_pool,
        is_paid=tournament_data.entry_fee > 0,
        created_by=current_player.id,
    )

    # Set JSON fields
    tournament.set_county_restrictions(tournament_data.county_restrictions)
    tournament.set_allowed_clubs(tournament_data.allowed_clubs)

    db.add(tournament)
    await db.commit()
    await db.refresh(tournament)

    return tournament_to_response(tournament, 0)


@router.get("/", response_model=List[TournamentResponse])
async def list_tournaments(
    # Basic filters
    status: Optional[TournamentStatus] = None,
    format: Optional[str] = None,  # swiss, round_robin

    # Search
    search: Optional[str] = None,  # Search in name/description

    # Restriction filters (find tournaments I'm eligible for)
    county: Optional[str] = None,  # Filter by allowed county
    min_rating: Optional[int] = None,  # My rating (find tournaments I can join)
    max_rating: Optional[int] = None,
    age: Optional[int] = None,  # My age
    gender: Optional[str] = None,  # my gender

    # Entry fee filter
    free_only: bool = False,
    paid_only: bool = False,

    # Pagination
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db)
):
    """
    Search and filter tournaments.

    Filters:
    - status: registration, active, completed
    - format: swiss, round_robin
    - search: text search in name/description
    - county: find tournaments allowing this county
    - min_rating/max_rating: find tournaments for this rating range
    - age: find tournaments allowing this age
    - gender: find tournaments allowing this gender
    - free_only/paid_only: filter by entry fee
    """
    query = select(Tournament)

    # Status filter
    if status:
        query = query.where(Tournament.status == status)

    # Format filter
    if format:
        from app.models.tournament import TournamentFormat
        try:
            format_enum = TournamentFormat(format)
            query = query.where(Tournament.format == format_enum)
        except ValueError:
            pass

    # Text search (name or description)
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            (Tournament.name.ilike(search_pattern)) |
            (Tournament.description.ilike(search_pattern))
        )

    # Free/paid filter
    if free_only:
        query = query.where(Tournament.is_paid == False)
    elif paid_only:
        query = query.where(Tournament.is_paid == True)

    # Execute query
    query = query.offset(skip).limit(limit).order_by(Tournament.created_at.desc())
    result = await db.execute(query)
    tournaments = result.scalars().all()

    # Post-filter for eligibility (rating, age, gender, county)
    # These require checking JSON fields and complex logic
    filtered_tournaments = []
    for t in tournaments:
        # Check rating eligibility
        if min_rating is not None:
            if t.min_rating and min_rating < t.min_rating:
                continue
            if t.max_rating and min_rating > t.max_rating:
                continue

        if max_rating is not None:
            if t.min_rating and max_rating < t.min_rating:
                continue
            if t.max_rating and max_rating > t.max_rating:
                continue

        # Check age eligibility
        if age is not None:
            if t.min_age and age < t.min_age:
                continue
            if t.max_age and age > t.max_age:
                continue

        # Check gender eligibility
        if gender:
            if t.gender_restriction == GenderRestriction.MALE_ONLY and gender != "male":
                continue
            if t.gender_restriction == GenderRestriction.FEMALE_ONLY and gender != "female":
                continue

        # Check county eligibility
        if county:
            county_list = t.get_county_restrictions()
            if county_list:
                allowed = expand_county_restrictions(county_list)
                if county not in allowed:
                    continue

        filtered_tournaments.append(t)

    # Get player counts
    responses = []
    for t in filtered_tournaments:
        count_result = await db.execute(
            select(func.count(TournamentPlayer.id))
            .where(TournamentPlayer.tournament_id == t.id)
            .where(TournamentPlayer.is_withdrawn == False)
        )
        count = count_result.scalar() or 0
        responses.append(tournament_to_response(t, count))

    return responses


@router.get("/{tournament_id}", response_model=TournamentResponse)
async def get_tournament(
    tournament_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get tournament details"""
    result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()

    if not tournament:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tournament not found"
        )

    count_result = await db.execute(
        select(func.count(TournamentPlayer.id))
        .where(TournamentPlayer.tournament_id == tournament_id)
        .where(TournamentPlayer.is_withdrawn == False)
    )
    count = count_result.scalar() or 0

    return tournament_to_response(tournament, count)


@router.patch("/{tournament_id}", response_model=TournamentResponse)
async def update_tournament(
    tournament_id: str,
    update_data: TournamentUpdate,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_admin)
):
    """Update tournament (admin only)"""
    result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()

    if not tournament:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tournament not found"
        )

    update_dict = update_data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        setattr(tournament, field, value)

    await db.commit()
    await db.refresh(tournament)

    count_result = await db.execute(
        select(func.count(TournamentPlayer.id))
        .where(TournamentPlayer.tournament_id == tournament_id)
        .where(TournamentPlayer.is_withdrawn == False)
    )
    count = count_result.scalar() or 0

    return tournament_to_response(tournament, count)


def check_eligibility(player: Player, tournament: Tournament, seed_rating: int) -> Optional[str]:
    """
    Check if a player is eligible for a tournament.
    Returns error message if not eligible, None if eligible.
    """
    # Check gender restriction
    if tournament.gender_restriction == GenderRestriction.MALE_ONLY and player.gender != "male":
        return "This tournament is for male players only"
    if tournament.gender_restriction == GenderRestriction.FEMALE_ONLY and player.gender != "female":
        return "This tournament is for female players only"

    # Check age restrictions
    if tournament.min_age and player.age < tournament.min_age:
        return f"Minimum age for this tournament is {tournament.min_age}"
    if tournament.max_age and player.age > tournament.max_age:
        return f"Maximum age for this tournament is {tournament.max_age}"

    # Check rating restrictions
    if tournament.min_rating and seed_rating < tournament.min_rating:
        return f"Minimum rating for this tournament is {tournament.min_rating}"
    if tournament.max_rating and seed_rating > tournament.max_rating:
        return f"Maximum rating for this tournament is {tournament.max_rating}"

    # Check county restrictions
    county_list = tournament.get_county_restrictions()
    if county_list:
        # Expand regions to counties
        allowed_counties = expand_county_restrictions(county_list)
        if player.county and player.county not in allowed_counties:
            return f"This tournament is restricted to: {', '.join(county_list)}"
        if not player.county:
            return "Please update your profile with your county to join this tournament"

    # Check club restrictions
    club_list = tournament.get_allowed_clubs()
    if club_list:
        if player.club and player.club not in club_list:
            return f"This tournament is restricted to clubs: {', '.join(club_list)}"
        if not player.club:
            return "Please update your profile with your club to join this tournament"

    return None  # Eligible


@router.post("/{tournament_id}/join", response_model=TournamentPlayerResponse)
async def join_tournament(
    tournament_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Join a tournament"""
    # Get tournament
    result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()

    if not tournament:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tournament not found"
        )

    if tournament.status != TournamentStatus.REGISTRATION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tournament registration is closed"
        )

    # Fetch Chess.com rating for seeding (do this early for eligibility check)
    stats = await chess_com_service.get_player_stats(current_player.chess_com_username)
    seed_rating = 1200  # Default
    if stats:
        seed_rating = stats.chess_rapid or stats.chess_blitz or stats.chess_bullet or 1200

    # Check eligibility
    eligibility_error = check_eligibility(current_player, tournament, seed_rating)
    if eligibility_error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=eligibility_error
        )

    # Check if paid tournament (payment not implemented yet)
    if tournament.is_paid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Paid tournaments coming soon. M-Pesa integration pending."
        )

    # Check if already joined
    result = await db.execute(
        select(TournamentPlayer).where(
            TournamentPlayer.tournament_id == tournament_id,
            TournamentPlayer.player_id == current_player.id
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        if existing.is_withdrawn:
            # Re-join
            existing.is_withdrawn = False
            await db.commit()
            await db.refresh(existing)
            return TournamentPlayerResponse(
                player_id=current_player.id,
                chess_com_username=current_player.chess_com_username,
                chess_com_avatar=current_player.chess_com_avatar,
                county=current_player.county,
                seed_rating=existing.seed_rating,
                score=existing.score,
                wins=existing.wins,
                draws=existing.draws,
                losses=existing.losses,
                buchholz=existing.buchholz,
                is_withdrawn=existing.is_withdrawn,
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already joined this tournament"
        )

    # Check capacity
    if tournament.max_players:
        count_result = await db.execute(
            select(func.count(TournamentPlayer.id))
            .where(TournamentPlayer.tournament_id == tournament_id)
            .where(TournamentPlayer.is_withdrawn == False)
        )
        current_count = count_result.scalar() or 0
        if current_count >= tournament.max_players:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tournament is full"
            )

    # Create tournament player entry (seed_rating already fetched above)
    tp = TournamentPlayer(
        tournament_id=tournament_id,
        player_id=current_player.id,
        seed_rating=seed_rating,
    )

    db.add(tp)
    await db.commit()
    await db.refresh(tp)

    return TournamentPlayerResponse(
        player_id=current_player.id,
        chess_com_username=current_player.chess_com_username,
        chess_com_avatar=current_player.chess_com_avatar,
        county=current_player.county,
        seed_rating=tp.seed_rating,
        score=tp.score,
        wins=tp.wins,
        draws=tp.draws,
        losses=tp.losses,
        buchholz=tp.buchholz,
        is_withdrawn=tp.is_withdrawn,
    )


@router.get("/{tournament_id}/check-eligibility")
async def check_tournament_eligibility(
    tournament_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Check if current player is eligible to join a tournament"""
    result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()

    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")

    # Get rating for eligibility check
    stats = await chess_com_service.get_player_stats(current_player.chess_com_username)
    seed_rating = 1200
    if stats:
        seed_rating = stats.chess_rapid or stats.chess_blitz or stats.chess_bullet or 1200

    # Check eligibility
    error = check_eligibility(current_player, tournament, seed_rating)

    # Check if already joined
    result = await db.execute(
        select(TournamentPlayer).where(
            TournamentPlayer.tournament_id == tournament_id,
            TournamentPlayer.player_id == current_player.id,
            TournamentPlayer.is_withdrawn == False
        )
    )
    already_joined = result.scalar_one_or_none() is not None

    return {
        "eligible": error is None,
        "reason": error,
        "already_joined": already_joined,
        "seed_rating": seed_rating,
        "requires_payment": tournament.is_paid,
        "entry_fee": tournament.entry_fee if tournament.is_paid else 0
    }


@router.post("/{tournament_id}/withdraw")
async def withdraw_from_tournament(
    tournament_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Withdraw from a tournament"""
    result = await db.execute(
        select(TournamentPlayer).where(
            TournamentPlayer.tournament_id == tournament_id,
            TournamentPlayer.player_id == current_player.id
        )
    )
    tp = result.scalar_one_or_none()

    if not tp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not registered for this tournament"
        )

    tp.is_withdrawn = True
    await db.commit()

    return {"message": "Successfully withdrawn from tournament"}


@router.get("/{tournament_id}/players", response_model=List[TournamentPlayerResponse])
async def get_tournament_players(
    tournament_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get all players in a tournament"""
    result = await db.execute(
        select(TournamentPlayer)
        .options(selectinload(TournamentPlayer.player))
        .where(TournamentPlayer.tournament_id == tournament_id)
        .where(TournamentPlayer.is_withdrawn == False)
        .order_by(TournamentPlayer.seed_rating.desc())
    )
    tournament_players = result.scalars().all()

    return [
        TournamentPlayerResponse(
            player_id=tp.player.id,
            chess_com_username=tp.player.chess_com_username,
            chess_com_avatar=tp.player.chess_com_avatar,
            county=tp.player.county,
            seed_rating=tp.seed_rating,
            score=tp.score,
            wins=tp.wins,
            draws=tp.draws,
            losses=tp.losses,
            buchholz=tp.buchholz,
            is_withdrawn=tp.is_withdrawn,
        )
        for tp in tournament_players
    ]


@router.get("/{tournament_id}/standings", response_model=StandingsResponse)
async def get_standings(
    tournament_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get tournament standings (sorted by score, then tiebreakers)"""
    # Get tournament
    result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()

    if not tournament:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tournament not found"
        )

    # Get players sorted by score and tiebreakers
    result = await db.execute(
        select(TournamentPlayer)
        .options(selectinload(TournamentPlayer.player))
        .where(TournamentPlayer.tournament_id == tournament_id)
        .where(TournamentPlayer.is_withdrawn == False)
        .order_by(
            TournamentPlayer.score.desc(),
            TournamentPlayer.buchholz.desc(),
            TournamentPlayer.sonneborn_berger.desc(),
            TournamentPlayer.wins.desc(),
        )
    )
    tournament_players = result.scalars().all()

    standings = [
        TournamentPlayerResponse(
            player_id=tp.player.id,
            chess_com_username=tp.player.chess_com_username,
            chess_com_avatar=tp.player.chess_com_avatar,
            county=tp.player.county,
            seed_rating=tp.seed_rating,
            score=tp.score,
            wins=tp.wins,
            draws=tp.draws,
            losses=tp.losses,
            buchholz=tp.buchholz,
            is_withdrawn=tp.is_withdrawn,
            rank=idx + 1,
        )
        for idx, tp in enumerate(tournament_players)
    ]

    return StandingsResponse(
        tournament_id=tournament.id,
        tournament_name=tournament.name,
        current_round=tournament.current_round,
        total_rounds=tournament.total_rounds,
        standings=standings,
    )
