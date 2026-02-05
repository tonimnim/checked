from sqlalchemy import Column, String, Integer, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import List, Optional, TYPE_CHECKING

from app.database import Base

if TYPE_CHECKING:
    from app.models.player import Player


class Club(Base):
    """Chess club model"""
    __tablename__ = "clubs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    logo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Club logo image URL
    county: Mapped[str] = mapped_column(String(50), default="", index=True)  # Empty string for nationwide clubs
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    club_type: Mapped[str] = mapped_column(String(20), default="community")  # corporate, school, community, county

    # Contact info
    contact_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Club stats (cached for performance, updated after tournaments)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    tournament_count: Mapped[int] = mapped_column(Integer, default=0)  # Tournaments participated in

    # Performance metrics (what makes a club "the best")
    total_points: Mapped[int] = mapped_column(Integer, default=0)  # Total tournament points by all members
    tournament_wins: Mapped[int] = mapped_column(Integer, default=0)  # Number of 1st place finishes by members
    average_rating: Mapped[int] = mapped_column(Integer, default=0)  # Average rapid rating of members

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    members: Mapped[List["Player"]] = relationship(
        "Player", back_populates="club_ref", foreign_keys="Player.club_id"
    )

    def __repr__(self) -> str:
        return f"<Club {self.name} ({self.county})>"
