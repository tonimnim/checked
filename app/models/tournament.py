import uuid
import json
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Text, ForeignKey, Float, Enum as SQLEnum, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, List, TYPE_CHECKING
import enum

from app.database import Base

if TYPE_CHECKING:
    from app.models.player import Player
    from app.models.pairing import Pairing


class TournamentStatus(str, enum.Enum):
    REGISTRATION = "registration"  # Open for player registration
    ACTIVE = "active"              # Tournament in progress
    COMPLETED = "completed"        # Tournament finished
    CANCELLED = "cancelled"


class TournamentFormat(str, enum.Enum):
    SWISS = "swiss"
    ROUND_ROBIN = "round_robin"
    SINGLE_ELIMINATION = "single_elimination"
    DOUBLE_ELIMINATION = "double_elimination"


class GenderRestriction(str, enum.Enum):
    OPEN = "open"           # Anyone can join
    MALE_ONLY = "male_only"
    FEMALE_ONLY = "female_only"


class Tournament(Base):
    __tablename__ = "tournaments"

    # Indexes for fast lookups
    __table_args__ = (
        Index("ix_tournaments_status", "status"),
        Index("ix_tournaments_status_created", "status", "created_at"),
        Index("ix_tournaments_start_date", "start_date"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Basic info
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Tournament settings
    format: Mapped[TournamentFormat] = mapped_column(
        SQLEnum(TournamentFormat), default=TournamentFormat.SWISS
    )
    total_rounds: Mapped[int] = mapped_column(Integer, default=5)
    current_round: Mapped[int] = mapped_column(Integer, default=0)

    # Time control (Chess.com format like "10+0", "15+10", "5+3")
    time_control: Mapped[str] = mapped_column(String(20), default="10+0")

    # Status
    status: Mapped[TournamentStatus] = mapped_column(
        SQLEnum(TournamentStatus), default=TournamentStatus.REGISTRATION
    )

    # Capacity
    max_players: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # None = unlimited

    # Scheduling
    registration_open: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    registration_close: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Restrictions
    county_restrictions: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # JSON list of allowed counties/regions, null = open to all
    min_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    min_age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gender_restriction: Mapped[GenderRestriction] = mapped_column(
        SQLEnum(GenderRestriction), default=GenderRestriction.OPEN
    )
    allowed_clubs: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # JSON list of allowed clubs, null = open to all

    # Tournament type (online vs in-person)
    is_online: Mapped[bool] = mapped_column(default=True)  # False = in-person/OTB
    venue: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # Physical location for OTB
    result_confirmation_minutes: Mapped[int] = mapped_column(Integer, default=10)  # Time for opponent to confirm

    # Payment (for future M-Pesa integration)
    entry_fee: Mapped[float] = mapped_column(Float, default=0.0)  # 0 = free tournament
    prize_pool: Mapped[float] = mapped_column(Float, default=0.0)
    is_paid: Mapped[bool] = mapped_column(default=False)  # True if entry_fee > 0

    # Admin
    created_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("players.id"), nullable=True
    )

    # Helper methods for JSON fields
    def get_county_restrictions(self) -> Optional[List[str]]:
        if self.county_restrictions:
            return json.loads(self.county_restrictions)
        return None

    def set_county_restrictions(self, counties: Optional[List[str]]):
        if counties:
            self.county_restrictions = json.dumps(counties)
        else:
            self.county_restrictions = None

    def get_allowed_clubs(self) -> Optional[List[str]]:
        if self.allowed_clubs:
            return json.loads(self.allowed_clubs)
        return None

    def set_allowed_clubs(self, clubs: Optional[List[str]]):
        if clubs:
            self.allowed_clubs = json.dumps(clubs)
        else:
            self.allowed_clubs = None

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    players: Mapped[List["TournamentPlayer"]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan"
    )
    pairings: Mapped[List["Pairing"]] = relationship(
        back_populates="tournament", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Tournament {self.name}>"


class TournamentPlayer(Base):
    """Junction table for tournament participants with their scores"""
    __tablename__ = "tournament_players"

    # Indexes for standings queries (most critical for performance)
    __table_args__ = (
        Index("ix_tp_tournament_score", "tournament_id", "score"),  # Standings query
        Index("ix_tp_tournament_withdrawn", "tournament_id", "is_withdrawn"),
        Index("ix_tp_tournament_player", "tournament_id", "player_id", unique=True),  # Unique constraint
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    tournament_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tournaments.id"), index=True
    )
    player_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("players.id"), index=True
    )

    # Seeding (initial Chess.com rating when they joined)
    seed_rating: Mapped[int] = mapped_column(Integer, default=1200)

    # Swiss scoring
    score: Mapped[float] = mapped_column(Float, default=0.0)  # Points (1 for win, 0.5 for draw, 0 for loss)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    draws: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)

    # Tiebreakers
    buchholz: Mapped[float] = mapped_column(Float, default=0.0)  # Sum of opponents' scores
    sonneborn_berger: Mapped[float] = mapped_column(Float, default=0.0)
    games_as_white: Mapped[int] = mapped_column(Integer, default=0)
    games_as_black: Mapped[int] = mapped_column(Integer, default=0)

    # Final ranking (set when tournament completes)
    final_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Status
    is_withdrawn: Mapped[bool] = mapped_column(default=False)

    # Payment status (for paid tournaments)
    has_paid: Mapped[bool] = mapped_column(default=False)

    # Timestamps
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    tournament: Mapped["Tournament"] = relationship(back_populates="players")
    player: Mapped["Player"] = relationship(back_populates="tournament_entries")

    def __repr__(self) -> str:
        return f"<TournamentPlayer {self.player_id} in {self.tournament_id}>"
