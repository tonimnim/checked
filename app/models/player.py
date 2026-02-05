import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, Integer, DateTime, Text, Index, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, List, TYPE_CHECKING

from app.database import Base

if TYPE_CHECKING:
    from app.models.tournament import TournamentPlayer
    from app.models.security import LoginHistory, DeviceFingerprint, SecurityFlag
    from app.models.club import Club


class Player(Base):
    __tablename__ = "players"

    # Indexes for fast lookups at scale
    __table_args__ = (
        Index("ix_players_county", "county"),
        Index("ix_players_gender", "gender"),
        Index("ix_players_age", "age"),
        Index("ix_players_created_at", "created_at"),
        Index("ix_players_county_gender", "county", "gender"),  # Composite for filtered queries
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Chess.com integration
    chess_com_username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    chess_com_avatar: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chess_com_joined: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chess_com_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # premium, basic, etc.
    chess_com_country: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)  # ISO country code (KE, US, etc.)

    # Chess.com ratings
    rating_rapid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rating_blitz: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rating_bullet: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ratings_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Local auth
    password_hash: Mapped[str] = mapped_column(String(128))

    # Personal info (required)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # Kenyan format +254...
    age: Mapped[int] = mapped_column(Integer)
    gender: Mapped[str] = mapped_column(String(20))  # male, female, other

    # Kenya-specific
    county: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # Nairobi, Mombasa, etc.
    club: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # Legacy: free text club name
    club_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("clubs.id", ondelete="SET NULL"), nullable=True, index=True
    )  # Proper club reference

    # Account status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    # Web Push notifications (subscription JSON)
    push_subscription: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON subscription object
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Security tracking
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)  # Has open security flags
    security_risk_level: Mapped[str] = mapped_column(String(20), default="normal")  # normal, elevated, high, restricted
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    registration_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    registration_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Relationships
    tournament_entries: Mapped[List["TournamentPlayer"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )
    login_history: Mapped[List["LoginHistory"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )
    devices: Mapped[List["DeviceFingerprint"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )
    security_flags: Mapped[List["SecurityFlag"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )
    club_ref: Mapped[Optional["Club"]] = relationship(
        "Club", back_populates="members", foreign_keys=[club_id]
    )

    def __repr__(self) -> str:
        return f"<Player {self.chess_com_username}>"
