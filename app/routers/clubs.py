"""
Clubs API router - CRUD operations for chess clubs
"""
import uuid
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.club import Club
from app.models.player import Player
from app.services.auth import get_current_player, get_current_admin


# ============== Schemas ==============

class ClubCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    logo_url: Optional[str] = None  # Club logo image URL
    county: Optional[str] = Field(None, max_length=50)  # Nullable for nationwide clubs
    club_type: str = Field(default="community")  # corporate, school, community, county
    description: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None


class ClubUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    logo_url: Optional[str] = None
    county: Optional[str] = Field(None, max_length=50)
    club_type: Optional[str] = None
    description: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None


class ClubResponse(BaseModel):
    id: str
    name: str
    logo_url: Optional[str]  # Cloudinary URL
    county: Optional[str]  # Nullable for nationwide clubs
    club_type: str
    description: Optional[str]
    contact_phone: Optional[str]
    contact_email: Optional[str]
    member_count: int
    tournament_count: int
    # Performance metrics
    total_points: int
    tournament_wins: int
    average_rating: int
    # Status
    is_active: bool
    is_verified: bool
    created_at: datetime

    @field_validator("county", mode="before")
    @classmethod
    def empty_county_to_none(cls, v):
        """Convert empty string county to None for cleaner API response"""
        return v if v else None

    class Config:
        from_attributes = True


class ClubDetailResponse(ClubResponse):
    members: List[dict]  # Simplified member info
    rank: Optional[int] = None  # Club's overall rank


class ClubListResponse(BaseModel):
    clubs: List[ClubResponse]
    total: int
    page: int
    page_size: int


# ============== Router ==============

router = APIRouter(prefix="/clubs", tags=["clubs"])


@router.get("", response_model=ClubListResponse)
async def list_clubs(
    county: Optional[str] = None,
    club_type: Optional[str] = None,
    search: Optional[str] = None,
    is_active: bool = True,
    sort_by: str = Query("performance", description="Sort by: performance, members, rating, name"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all clubs ranked by performance (default) with optional filtering"""
    query = select(Club).where(Club.is_active == is_active)

    if county:
        query = query.where(Club.county == county)

    if club_type:
        query = query.where(Club.club_type == club_type)

    if search:
        query = query.where(Club.name.ilike(f"%{search}%"))

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Sort by performance metrics (best clubs first)
    if sort_by == "performance":
        # Primary: total points, Secondary: tournament wins, Tertiary: average rating
        query = query.order_by(
            Club.total_points.desc(),
            Club.tournament_wins.desc(),
            Club.average_rating.desc(),
            Club.name
        )
    elif sort_by == "members":
        query = query.order_by(Club.member_count.desc(), Club.name)
    elif sort_by == "rating":
        query = query.order_by(Club.average_rating.desc(), Club.name)
    else:  # name
        query = query.order_by(Club.name)

    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    clubs = result.scalars().all()

    return ClubListResponse(
        clubs=[ClubResponse.model_validate(c) for c in clubs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/counties")
async def list_counties_with_clubs(db: AsyncSession = Depends(get_db)):
    """Get list of counties that have clubs"""
    query = (
        select(Club.county, func.count(Club.id).label("club_count"))
        .where(Club.is_active == True)
        .group_by(Club.county)
        .order_by(func.count(Club.id).desc())
    )
    result = await db.execute(query)
    rows = result.all()

    return [{"county": row[0], "club_count": row[1]} for row in rows]


@router.get("/{club_id}", response_model=ClubDetailResponse)
async def get_club(club_id: str, db: AsyncSession = Depends(get_db)):
    """Get club details with members and ranking"""
    query = select(Club).where(Club.id == club_id).options(selectinload(Club.members))
    result = await db.execute(query)
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Calculate club's rank (how many clubs have more points)
    rank_query = select(func.count()).select_from(Club).where(
        Club.is_active == True,
        Club.total_points > club.total_points
    )
    rank_result = await db.execute(rank_query)
    rank = (rank_result.scalar() or 0) + 1  # +1 because rank is 1-indexed

    # Build members list sorted by rating
    members = sorted(
        [
            {
                "id": m.id,
                "chess_com_username": m.chess_com_username,
                "rating_rapid": m.rating_rapid,
                "rating_blitz": m.rating_blitz,
                "avatar": m.chess_com_avatar,
            }
            for m in club.members
            if m.is_active
        ],
        key=lambda x: x["rating_rapid"] or 0,
        reverse=True
    )

    return ClubDetailResponse(
        id=club.id,
        name=club.name,
        county=club.county,
        club_type=club.club_type,
        description=club.description,
        contact_phone=club.contact_phone,
        contact_email=club.contact_email,
        member_count=club.member_count,
        tournament_count=club.tournament_count,
        total_points=club.total_points,
        tournament_wins=club.tournament_wins,
        average_rating=club.average_rating,
        is_active=club.is_active,
        is_verified=club.is_verified,
        created_at=club.created_at,
        members=members,
        rank=rank,
    )


@router.post("", response_model=ClubResponse, status_code=201)
async def create_club(
    club_data: ClubCreate,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Create a new club (admin only)"""
    # Check if name already exists
    existing = await db.execute(
        select(Club).where(Club.name.ilike(club_data.name))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Club with this name already exists")

    club = Club(
        id=str(uuid.uuid4()),
        name=club_data.name,
        logo_url=club_data.logo_url,
        county=club_data.county or "",  # Empty string for nationwide clubs (SQLite constraint)
        club_type=club_data.club_type,
        description=club_data.description,
        contact_phone=club_data.contact_phone,
        contact_email=club_data.contact_email,
        member_count=0,
        tournament_count=0,
        total_points=0,
        tournament_wins=0,
        average_rating=0,
    )

    db.add(club)
    await db.commit()
    await db.refresh(club)

    return ClubResponse.model_validate(club)


@router.patch("/{club_id}", response_model=ClubResponse)
async def update_club(
    club_id: str,
    club_data: ClubUpdate,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Update a club (admin only)"""
    result = await db.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Check for duplicate name if changing
    if club_data.name and club_data.name != club.name:
        existing = await db.execute(
            select(Club).where(Club.name.ilike(club_data.name))
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Club with this name already exists")

    # Update fields
    update_data = club_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(club, field, value)

    await db.commit()
    await db.refresh(club)

    return ClubResponse.model_validate(club)


@router.delete("/{club_id}", status_code=204)
async def delete_club(
    club_id: str,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Delete a club (admin only) - sets is_active=False and removes member associations"""
    result = await db.execute(
        select(Club).where(Club.id == club_id).options(selectinload(Club.members))
    )
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Remove all member associations
    for member in club.members:
        member.club_id = None

    # Soft delete
    club.is_active = False
    club.member_count = 0

    await db.commit()


@router.post("/{club_id}/join")
async def join_club(
    club_id: str,
    db: AsyncSession = Depends(get_db),
    player: Player = Depends(get_current_player),
):
    """Join a club (authenticated players)"""
    result = await db.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    if not club.is_active:
        raise HTTPException(status_code=400, detail="Club is not active")

    # Check if already in a club
    if player.club_id:
        if player.club_id == club_id:
            raise HTTPException(status_code=400, detail="Already a member of this club")
        raise HTTPException(
            status_code=400,
            detail="Already a member of another club. Leave current club first."
        )

    # Join club
    player.club_id = club_id
    player.club = club.name  # Keep legacy field in sync

    # Update member count
    club.member_count += 1

    await db.commit()

    return {"message": f"Successfully joined {club.name}"}


@router.post("/{club_id}/leave")
async def leave_club(
    club_id: str,
    db: AsyncSession = Depends(get_db),
    player: Player = Depends(get_current_player),
):
    """Leave a club (authenticated players)"""
    if player.club_id != club_id:
        raise HTTPException(status_code=400, detail="Not a member of this club")

    result = await db.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Leave club
    player.club_id = None
    player.club = None

    # Update member count
    club.member_count = max(0, club.member_count - 1)

    await db.commit()

    return {"message": f"Successfully left {club.name}"}


@router.post("/{club_id}/members/{player_id}", status_code=201)
async def add_member_to_club(
    club_id: str,
    player_id: str,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Add a player to a club (admin only)"""
    result = await db.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    if player.club_id == club_id:
        raise HTTPException(status_code=400, detail="Player is already a member")

    # Remove from old club if any
    if player.club_id:
        old_club_result = await db.execute(
            select(Club).where(Club.id == player.club_id)
        )
        old_club = old_club_result.scalar_one_or_none()
        if old_club:
            old_club.member_count = max(0, old_club.member_count - 1)

    # Add to new club
    player.club_id = club_id
    player.club = club.name
    club.member_count += 1

    await db.commit()

    return {"message": f"Added {player.chess_com_username} to {club.name}"}


@router.delete("/{club_id}/members/{player_id}", status_code=204)
async def remove_member_from_club(
    club_id: str,
    player_id: str,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Remove a player from a club (admin only)"""
    result = await db.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    if player.club_id != club_id:
        raise HTTPException(status_code=400, detail="Player is not a member of this club")

    # Remove from club
    player.club_id = None
    player.club = None
    club.member_count = max(0, club.member_count - 1)

    await db.commit()


@router.post("/{club_id}/refresh-stats")
async def refresh_club_stats(
    club_id: str,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Recalculate all stats for a club (admin only)"""
    result = await db.execute(
        select(Club).where(Club.id == club_id).options(selectinload(Club.members))
    )
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Count active members
    active_members = [m for m in club.members if m.is_active]
    club.member_count = len(active_members)

    # Calculate average rating (rapid) of members
    ratings = [m.rating_rapid for m in active_members if m.rating_rapid]
    club.average_rating = int(sum(ratings) / len(ratings)) if ratings else 0

    await db.commit()

    return {
        "member_count": club.member_count,
        "average_rating": club.average_rating,
        "total_points": club.total_points,
        "tournament_wins": club.tournament_wins,
    }


@router.post("/refresh-all-stats")
async def refresh_all_club_stats(
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Recalculate stats for all clubs (admin only)"""
    result = await db.execute(
        select(Club).where(Club.is_active == True).options(selectinload(Club.members))
    )
    clubs = result.scalars().all()

    updated = 0
    for club in clubs:
        active_members = [m for m in club.members if m.is_active]
        club.member_count = len(active_members)

        ratings = [m.rating_rapid for m in active_members if m.rating_rapid]
        club.average_rating = int(sum(ratings) / len(ratings)) if ratings else 0
        updated += 1

    await db.commit()

    return {"updated_clubs": updated}
