from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.models.pairing import GameResult


class PlayerBrief(BaseModel):
    """Brief player info for pairings"""
    id: str
    chess_com_username: str
    chess_com_avatar: Optional[str]
    county: Optional[str] = None

    class Config:
        from_attributes = True


class PairingResponse(BaseModel):
    """Schema for pairing response"""
    id: str
    tournament_id: str
    round_number: int
    board_number: int
    white_player: Optional[PlayerBrief]
    black_player: Optional[PlayerBrief]
    result: GameResult
    chess_com_game_url: Optional[str]
    scheduled_time: Optional[datetime]
    played_at: Optional[datetime]
    deadline: Optional[datetime]
    no_show_claimed_by: Optional[str] = None  # player_id who claimed no-show
    is_bye: bool

    # In-person claim/confirmation fields
    claimed_result: Optional[GameResult] = None
    claimed_by: Optional[str] = None
    claimed_at: Optional[datetime] = None
    confirmation_deadline: Optional[datetime] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    is_disputed: bool = False
    dispute_reason: Optional[str] = None
    has_pending_claim: bool = False
    can_cancel_claim: bool = False

    class Config:
        from_attributes = True


class PairingResultUpdate(BaseModel):
    """Schema for updating a pairing result (manual)"""
    result: GameResult
    chess_com_game_url: Optional[str] = None


class GameUrlSubmission(BaseModel):
    """Schema for submitting a Chess.com game URL for automatic verification"""
    game_url: str


class GameVerificationResult(BaseModel):
    """Response from game URL verification"""
    valid: bool
    error: Optional[str] = None
    result: Optional[str] = None  # white_wins, black_wins, draw
    game_id: Optional[str] = None
    played_at: Optional[datetime] = None
    pairing_updated: bool = False


class NoShowClaim(BaseModel):
    """Claim that opponent didn't show up for the game"""
    reason: Optional[str] = None  # Optional explanation


class DeadlineProcessingResult(BaseModel):
    """Result of processing expired pairings"""
    processed_count: int
    forfeits: int
    double_forfeits: int
    details: list


# In-person tournament result claim/confirmation schemas

class ResultClaim(BaseModel):
    """Schema for claiming a result (in-person tournaments)"""
    result: GameResult  # white_wins, black_wins, or draw


class ResultConfirmation(BaseModel):
    """Schema for confirming a claimed result"""
    confirmed: bool = True  # Just needs to be called


class ResultDispute(BaseModel):
    """Schema for disputing a claimed result"""
    reason: str  # Why they disagree with the claim


class CancelClaim(BaseModel):
    """Schema for cancelling own claim (within time limit)"""
    pass  # No fields needed


class PendingConfirmationResponse(BaseModel):
    """Response for a pairing with pending confirmation"""
    pairing_id: str
    tournament_id: str
    tournament_name: str
    round_number: int
    board_number: int
    white_player: Optional[PlayerBrief]
    black_player: Optional[PlayerBrief]
    claimed_result: GameResult
    claimed_by: str  # player_id
    claimed_by_username: str
    claimed_at: datetime
    confirmation_deadline: datetime
    is_disputed: bool
    dispute_reason: Optional[str] = None

    class Config:
        from_attributes = True


class AdminOverrideResult(BaseModel):
    """Schema for admin to override/resolve a result"""
    result: GameResult
    reason: Optional[str] = None  # Why admin is overriding


class TournamentBrief(BaseModel):
    """Brief tournament info for match listings"""
    id: str
    name: str
    is_online: bool
    time_control: str
    status: str

    class Config:
        from_attributes = True


class MatchResponse(BaseModel):
    """Extended pairing response with tournament info for matches page"""
    id: str
    tournament_id: str
    tournament: TournamentBrief
    round_number: int
    board_number: int
    white_player: Optional[PlayerBrief]
    black_player: Optional[PlayerBrief]
    result: GameResult
    chess_com_game_url: Optional[str]
    scheduled_time: Optional[datetime]
    played_at: Optional[datetime]
    deadline: Optional[datetime]
    no_show_claimed_by: Optional[str] = None
    is_bye: bool

    # In-person claim/confirmation fields
    claimed_result: Optional[GameResult] = None
    claimed_by: Optional[str] = None
    claimed_at: Optional[datetime] = None
    confirmation_deadline: Optional[datetime] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    is_disputed: bool = False
    dispute_reason: Optional[str] = None
    has_pending_claim: bool = False
    can_cancel_claim: bool = False

    class Config:
        from_attributes = True
