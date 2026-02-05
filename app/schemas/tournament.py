from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime

from app.models.tournament import TournamentStatus, TournamentFormat, GenderRestriction


class TournamentCreate(BaseModel):
    """Schema for creating a tournament"""
    name: str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = None
    format: TournamentFormat = TournamentFormat.SWISS
    total_rounds: int = Field(5, ge=1, le=15)
    time_control: str = Field("10+0", pattern=r"^\d+\+\d+$")  # e.g., "10+0", "15+10"
    max_players: Optional[int] = Field(None, ge=2)
    registration_close: Optional[datetime] = None
    start_date: Optional[datetime] = None

    # Tournament type
    is_online: bool = True  # False = in-person/OTB tournament
    venue: Optional[str] = Field(None, max_length=200)  # Physical location for OTB
    result_confirmation_minutes: int = Field(10, ge=1, le=60)  # Time for opponent to confirm

    # Restrictions
    county_restrictions: Optional[List[str]] = None  # List of counties or regions
    min_rating: Optional[int] = Field(None, ge=0, le=3500)
    max_rating: Optional[int] = Field(None, ge=0, le=3500)
    min_age: Optional[int] = Field(None, ge=5, le=120)
    max_age: Optional[int] = Field(None, ge=5, le=120)
    gender_restriction: GenderRestriction = GenderRestriction.OPEN
    allowed_clubs: Optional[List[str]] = None

    # Payment (ready for M-Pesa)
    entry_fee: float = Field(0.0, ge=0)
    prize_pool: float = Field(0.0, ge=0)

    @field_validator('max_rating')
    @classmethod
    def validate_rating_range(cls, v, info):
        min_rating = info.data.get('min_rating')
        if v is not None and min_rating is not None and v < min_rating:
            raise ValueError('max_rating must be >= min_rating')
        return v

    @field_validator('max_age')
    @classmethod
    def validate_age_range(cls, v, info):
        min_age = info.data.get('min_age')
        if v is not None and min_age is not None and v < min_age:
            raise ValueError('max_age must be >= min_age')
        return v


class TournamentUpdate(BaseModel):
    """Schema for updating a tournament"""
    name: Optional[str] = Field(None, min_length=3, max_length=200)
    description: Optional[str] = None
    total_rounds: Optional[int] = Field(None, ge=1, le=15)
    time_control: Optional[str] = None
    max_players: Optional[int] = Field(None, ge=2)
    registration_close: Optional[datetime] = None
    start_date: Optional[datetime] = None
    status: Optional[TournamentStatus] = None

    # Tournament type (can only change before tournament starts)
    is_online: Optional[bool] = None
    venue: Optional[str] = Field(None, max_length=200)
    result_confirmation_minutes: Optional[int] = Field(None, ge=1, le=60)

    # Restrictions (can be updated before tournament starts)
    county_restrictions: Optional[List[str]] = None
    min_rating: Optional[int] = Field(None, ge=0, le=3500)
    max_rating: Optional[int] = Field(None, ge=0, le=3500)
    min_age: Optional[int] = Field(None, ge=5, le=120)
    max_age: Optional[int] = Field(None, ge=5, le=120)
    gender_restriction: Optional[GenderRestriction] = None
    allowed_clubs: Optional[List[str]] = None


class TournamentPlayerResponse(BaseModel):
    """Player info within a tournament"""
    player_id: str
    chess_com_username: str
    chess_com_avatar: Optional[str]
    county: Optional[str] = None
    seed_rating: int
    score: float
    wins: int
    draws: int
    losses: int
    buchholz: float
    is_withdrawn: bool
    rank: Optional[int] = None  # Calculated at runtime

    class Config:
        from_attributes = True


class TournamentResponse(BaseModel):
    """Schema for tournament response"""
    id: str
    name: str
    description: Optional[str]
    format: TournamentFormat
    total_rounds: int
    current_round: int
    time_control: str
    status: TournamentStatus
    max_players: Optional[int]
    registration_open: datetime
    registration_close: Optional[datetime]
    start_date: Optional[datetime]
    end_date: Optional[datetime]

    # Tournament type
    is_online: bool = True
    venue: Optional[str] = None
    result_confirmation_minutes: int = 10

    # Restrictions
    county_restrictions: Optional[List[str]] = None
    min_rating: Optional[int] = None
    max_rating: Optional[int] = None
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    gender_restriction: GenderRestriction
    allowed_clubs: Optional[List[str]] = None

    # Payment
    entry_fee: float
    prize_pool: float
    is_paid: bool = False

    player_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class StandingsResponse(BaseModel):
    """Tournament standings"""
    tournament_id: str
    tournament_name: str
    current_round: int
    total_rounds: int
    standings: List[TournamentPlayerResponse]
