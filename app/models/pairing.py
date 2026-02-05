import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Boolean, Text, Enum as SQLEnum, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, TYPE_CHECKING
import enum

from app.database import Base

if TYPE_CHECKING:
    from app.models.tournament import Tournament


class GameResult(str, enum.Enum):
    PENDING = "pending"           # Not played yet
    WHITE_WINS = "white_wins"     # 1-0
    BLACK_WINS = "black_wins"     # 0-1
    DRAW = "draw"                 # 0.5-0.5
    WHITE_FORFEIT = "white_forfeit"  # White didn't show up
    BLACK_FORFEIT = "black_forfeit"  # Black didn't show up
    DOUBLE_FORFEIT = "double_forfeit"  # Neither showed up
    BYE = "bye"                   # Odd number of players, one gets a bye


class Pairing(Base):
    __tablename__ = "pairings"

    # Indexes for pairing queries
    __table_args__ = (
        Index("ix_pairings_tournament_round", "tournament_id", "round_number"),
        Index("ix_pairings_tournament_round_board", "tournament_id", "round_number", "board_number"),
        Index("ix_pairings_white_player", "white_player_id"),
        Index("ix_pairings_black_player", "black_player_id"),
        Index("ix_pairings_result", "tournament_id", "result"),  # Find pending games
        Index("ix_pairings_deadline", "deadline", "result"),  # For deadline processing
        Index("ix_pairings_claimed", "tournament_id", "claimed_result", "is_disputed"),  # Pending confirmations
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    tournament_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tournaments.id"), index=True
    )
    round_number: Mapped[int] = mapped_column(Integer, index=True)

    # Players (white_player or black_player can be null for BYE)
    white_player_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("players.id"), nullable=True
    )
    black_player_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("players.id"), nullable=True
    )

    # Board number (for display: "Board 1", "Board 2", etc.)
    board_number: Mapped[int] = mapped_column(Integer, default=1)

    # Result
    result: Mapped[GameResult] = mapped_column(
        SQLEnum(GameResult), default=GameResult.PENDING
    )

    # Chess.com game verification
    chess_com_game_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chess_com_game_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Notification tracking
    white_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    black_notified: Mapped[bool] = mapped_column(Boolean, default=False)

    # Scheduling & Deadlines
    scheduled_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    played_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # Auto-set to created_at + 24h

    # No-show tracking (for online tournaments)
    no_show_claimed_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("players.id"), nullable=True
    )  # Player who claimed opponent didn't show
    no_show_claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Result claim tracking (for in-person tournaments)
    claimed_result: Mapped[Optional[GameResult]] = mapped_column(
        SQLEnum(GameResult), nullable=True
    )  # The result being claimed (before confirmation)
    claimed_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("players.id"), nullable=True
    )  # Player who submitted the claim
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    confirmation_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Result confirmation (opponent confirms or disputes)
    confirmed_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("players.id"), nullable=True
    )  # Opponent who confirmed the result
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_disputed: Mapped[bool] = mapped_column(default=False)
    dispute_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    tournament: Mapped["Tournament"] = relationship(back_populates="pairings")

    def __repr__(self) -> str:
        return f"<Pairing R{self.round_number}: {self.white_player_id} vs {self.black_player_id}>"

    @property
    def is_bye(self) -> bool:
        return self.result == GameResult.BYE or (
            self.white_player_id is None or self.black_player_id is None
        )

    @property
    def has_pending_claim(self) -> bool:
        """Check if there's a result claim waiting for confirmation"""
        return (
            self.claimed_result is not None and
            self.result == GameResult.PENDING and
            not self.is_disputed and
            self.confirmed_at is None
        )

    @property
    def can_cancel_claim(self) -> bool:
        """Check if claim can still be cancelled (within 2 minutes)"""
        if not self.claimed_at:
            return False
        from datetime import datetime, timedelta
        return datetime.utcnow() < self.claimed_at + timedelta(minutes=2)
