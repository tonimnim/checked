from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime

from app.database import get_db
from app.models.player import Player
from app.models.tournament import Tournament, TournamentPlayer, TournamentStatus, TournamentFormat
from app.models.pairing import Pairing, GameResult
from app.schemas.pairing import (
    PairingResponse, PairingResultUpdate, PlayerBrief,
    GameUrlSubmission, GameVerificationResult, NoShowClaim, DeadlineProcessingResult,
    ResultClaim, ResultConfirmation, ResultDispute, CancelClaim,
    PendingConfirmationResponse, AdminOverrideResult,
    TournamentBrief, MatchResponse
)
from app.services.chess_com import chess_com_service
from app.services.websocket import (
    notify_pairing_created, notify_result_submitted,
    notify_no_show_claimed, notify_standings_updated, notify_round_started,
    notify_result_claimed, notify_result_confirmed, notify_result_dispute, notify_claim_cancelled
)
from app.services.push import (
    notify_pairing_push, notify_round_started_push,
    notify_result_push, notify_no_show_push,
    notify_result_claim_push, notify_result_confirmed_push,
    notify_result_disputed_push, notify_admin_disputed_push
)
import json
from datetime import timedelta

from app.services.notification import create_notification

# Default deadline: 24 hours after pairing created
PAIRING_DEADLINE_HOURS = 24
from app.services.auth import get_current_player, get_current_admin
from app.services.swiss import SwissPairingEngine, SwissPlayer, RoundRobinEngine, calculate_buchholz

router = APIRouter(prefix="/api/tournaments", tags=["Pairings"])


async def get_player_brief(db: AsyncSession, player_id: str) -> Optional[PlayerBrief]:
    """Helper to get brief player info"""
    if not player_id:
        return None
    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()
    if not player:
        return None
    return PlayerBrief(
        id=player.id,
        chess_com_username=player.chess_com_username,
        chess_com_avatar=player.chess_com_avatar,
        county=player.county
    )


async def build_pairing_response(db: AsyncSession, pairing: Pairing) -> PairingResponse:
    """Convert Pairing model to response with player details"""
    white_player = await get_player_brief(db, pairing.white_player_id)
    black_player = await get_player_brief(db, pairing.black_player_id)

    return PairingResponse(
        id=pairing.id,
        tournament_id=pairing.tournament_id,
        round_number=pairing.round_number,
        board_number=pairing.board_number,
        white_player=white_player,
        black_player=black_player,
        result=pairing.result,
        chess_com_game_url=pairing.chess_com_game_url,
        scheduled_time=pairing.scheduled_time,
        played_at=pairing.played_at,
        deadline=pairing.deadline,
        no_show_claimed_by=pairing.no_show_claimed_by,
        is_bye=pairing.is_bye,
        # In-person claim/confirmation fields
        claimed_result=pairing.claimed_result,
        claimed_by=pairing.claimed_by,
        claimed_at=pairing.claimed_at,
        confirmation_deadline=pairing.confirmation_deadline,
        confirmed_by=pairing.confirmed_by,
        confirmed_at=pairing.confirmed_at,
        is_disputed=pairing.is_disputed,
        dispute_reason=pairing.dispute_reason,
        has_pending_claim=pairing.has_pending_claim,
        can_cancel_claim=pairing.can_cancel_claim
    )


@router.post("/{tournament_id}/generate-pairings", response_model=List[PairingResponse])
async def generate_pairings(
    tournament_id: str,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_admin)
):
    """
    Generate pairings for the next round (admin only).
    Supports Swiss and Round Robin formats.
    """
    # Get tournament
    result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()

    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")

    # Check tournament status
    if tournament.status == TournamentStatus.REGISTRATION:
        # Start the tournament
        tournament.status = TournamentStatus.ACTIVE
    elif tournament.status == TournamentStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Tournament is already completed")

    next_round = tournament.current_round + 1

    if next_round > tournament.total_rounds:
        tournament.status = TournamentStatus.COMPLETED
        await db.commit()
        raise HTTPException(status_code=400, detail="All rounds completed")

    # Check if current round pairings exist and are all resolved
    if tournament.current_round > 0:
        result = await db.execute(
            select(Pairing).where(
                and_(
                    Pairing.tournament_id == tournament_id,
                    Pairing.round_number == tournament.current_round,
                    Pairing.result == GameResult.PENDING
                )
            )
        )
        pending = result.scalars().all()
        if pending:
            raise HTTPException(
                status_code=400,
                detail=f"Round {tournament.current_round} has unfinished games"
            )

    # Get tournament players with their history
    result = await db.execute(
        select(TournamentPlayer)
        .options(selectinload(TournamentPlayer.player))
        .where(
            TournamentPlayer.tournament_id == tournament_id,
            TournamentPlayer.is_withdrawn == False
        )
    )
    tournament_players = result.scalars().all()

    if len(tournament_players) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 players")

    # Round Robin validation
    if tournament.format == TournamentFormat.ROUND_ROBIN:
        n = len(tournament_players)
        required_rounds = n - 1 if n % 2 == 0 else n
        if tournament.total_rounds < required_rounds:
            raise HTTPException(
                status_code=400,
                detail=f"Round Robin with {n} players requires {required_rounds} rounds. Update tournament settings."
            )

    # Get previous pairings to build opponent history
    result = await db.execute(
        select(Pairing).where(
            Pairing.tournament_id == tournament_id,
            Pairing.result != GameResult.BYE
        )
    )
    previous_pairings = result.scalars().all()

    # Build opponent sets for each player
    opponents_map = {tp.player_id: set() for tp in tournament_players}
    for p in previous_pairings:
        if p.white_player_id and p.black_player_id:
            if p.white_player_id in opponents_map:
                opponents_map[p.white_player_id].add(p.black_player_id)
            if p.black_player_id in opponents_map:
                opponents_map[p.black_player_id].add(p.white_player_id)

    # Build player objects for pairing engines
    pairing_players = []
    for tp in tournament_players:
        pairing_players.append(SwissPlayer(
            id=tp.player_id,
            score=tp.score,
            rating=tp.seed_rating,
            games_as_white=tp.games_as_white,
            games_as_black=tp.games_as_black,
            opponents=opponents_map.get(tp.player_id, set()),
            is_withdrawn=tp.is_withdrawn
        ))

    # Generate pairings based on tournament format
    if tournament.format == TournamentFormat.ROUND_ROBIN:
        engine = RoundRobinEngine(pairing_players)
        new_pairings = engine.generate_round(next_round)
    else:
        # Default to Swiss for SWISS and other formats
        engine = SwissPairingEngine(pairing_players)
        new_pairings = engine.generate_pairings(next_round)

    # Create Pairing records
    created_pairings = []
    deadline = datetime.utcnow() + timedelta(hours=PAIRING_DEADLINE_HOURS)

    for sp in new_pairings:
        pairing = Pairing(
            tournament_id=tournament_id,
            round_number=next_round,
            white_player_id=sp.white_id if sp.white_id else None,
            black_player_id=sp.black_id if sp.black_id else None,
            board_number=sp.board_number,
            result=GameResult.BYE if sp.is_bye else GameResult.PENDING,
            deadline=None if sp.is_bye else deadline  # No deadline for byes
        )

        # If bye, automatically give the player a win (1 point)
        if sp.is_bye:
            # Update player's score for bye
            result = await db.execute(
                select(TournamentPlayer).where(
                    TournamentPlayer.tournament_id == tournament_id,
                    TournamentPlayer.player_id == sp.white_id
                )
            )
            tp = result.scalar_one_or_none()
            if tp:
                tp.score += 1.0
                tp.wins += 1

        db.add(pairing)
        created_pairings.append(pairing)

    # Update tournament round
    tournament.current_round = next_round
    await db.commit()

    # Refresh and build responses
    responses = []
    for p in created_pairings:
        await db.refresh(p)
        responses.append(await build_pairing_response(db, p))

    # Send WebSocket and Push notifications for each pairing
    push_tokens = []
    for p in created_pairings:
        if not p.is_bye and p.white_player_id and p.black_player_id:
            # WebSocket notification
            await notify_pairing_created(
                tournament_id=tournament_id,
                white_player_id=p.white_player_id,
                black_player_id=p.black_player_id,
                pairing_data={
                    "pairing_id": p.id,
                    "round": p.round_number,
                    "board": p.board_number,
                    "deadline": p.deadline.isoformat() if p.deadline else None
                }
            )

            # Push notifications - get player info
            import json
            white_result = await db.execute(select(Player).where(Player.id == p.white_player_id))
            white_player = white_result.scalar_one_or_none()
            black_result = await db.execute(select(Player).where(Player.id == p.black_player_id))
            black_player = black_result.scalar_one_or_none()

            if white_player and white_player.push_subscription and white_player.push_enabled:
                try:
                    subscription = json.loads(white_player.push_subscription)
                    await notify_pairing_push(
                        subscription=subscription,
                        opponent_username=black_player.chess_com_username if black_player else "Unknown",
                        tournament_name=tournament.name,
                        color="white",
                        round_number=p.round_number,
                        tournament_id=tournament_id,
                        pairing_id=p.id
                    )
                except json.JSONDecodeError:
                    pass

            if black_player and black_player.push_subscription and black_player.push_enabled:
                try:
                    subscription = json.loads(black_player.push_subscription)
                    await notify_pairing_push(
                        subscription=subscription,
                        opponent_username=white_player.chess_com_username if white_player else "Unknown",
                        tournament_name=tournament.name,
                        color="black",
                        round_number=p.round_number,
                        tournament_id=tournament_id,
                        pairing_id=p.id
                    )
                except json.JSONDecodeError:
                    pass

            # In-app notifications for both players
            white_opponent_name = black_player.chess_com_username if black_player else "Unknown"
            black_opponent_name = white_player.chess_com_username if white_player else "Unknown"
            notif_data = {
                "tournament_id": tournament_id,
                "pairing_id": p.id,
                "round_number": p.round_number,
            }

            if white_player:
                white_data = {**notif_data, "opponent_phone": black_player.phone if black_player else None}
                await create_notification(
                    db, white_player.id, "pairing",
                    f"Round {p.round_number} Pairing",
                    f"You play as White vs {white_opponent_name} in {tournament.name}",
                    white_data,
                )

            if black_player:
                black_data = {**notif_data, "opponent_phone": white_player.phone if white_player else None}
                await create_notification(
                    db, black_player.id, "pairing",
                    f"Round {p.round_number} Pairing",
                    f"You play as Black vs {black_opponent_name} in {tournament.name}",
                    black_data,
                )

    await db.commit()

    # Notify all players in tournament that new round started (WebSocket)
    await notify_round_started(tournament_id, next_round)

    return responses


@router.get("/{tournament_id}/pairings", response_model=List[PairingResponse])
async def get_tournament_pairings(
    tournament_id: str,
    round_number: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """Get pairings for a tournament, optionally filtered by round"""
    query = select(Pairing).where(Pairing.tournament_id == tournament_id)

    if round_number:
        query = query.where(Pairing.round_number == round_number)

    query = query.order_by(Pairing.round_number, Pairing.board_number)

    result = await db.execute(query)
    pairings = result.scalars().all()

    responses = []
    for p in pairings:
        responses.append(await build_pairing_response(db, p))

    return responses


@router.get("/{tournament_id}/pairings/{pairing_id}", response_model=PairingResponse)
async def get_pairing(
    tournament_id: str,
    pairing_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific pairing"""
    result = await db.execute(
        select(Pairing).where(
            Pairing.id == pairing_id,
            Pairing.tournament_id == tournament_id
        )
    )
    pairing = result.scalar_one_or_none()

    if not pairing:
        raise HTTPException(status_code=404, detail="Pairing not found")

    return await build_pairing_response(db, pairing)


@router.patch("/{tournament_id}/pairings/{pairing_id}/result", response_model=PairingResponse)
async def update_pairing_result(
    tournament_id: str,
    pairing_id: str,
    result_data: PairingResultUpdate,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Update the result of a pairing.
    Can be done by admin or by one of the players in the pairing.
    """
    # Get pairing
    result = await db.execute(
        select(Pairing).where(
            Pairing.id == pairing_id,
            Pairing.tournament_id == tournament_id
        )
    )
    pairing = result.scalar_one_or_none()

    if not pairing:
        raise HTTPException(status_code=404, detail="Pairing not found")

    # Check authorization (admin or participant)
    is_participant = (
        current_player.id == pairing.white_player_id or
        current_player.id == pairing.black_player_id
    )
    if not current_player.is_admin and not is_participant:
        raise HTTPException(status_code=403, detail="Not authorized to update this result")

    if pairing.result not in [GameResult.PENDING, GameResult.BYE]:
        raise HTTPException(status_code=400, detail="Result already recorded")

    # Update pairing
    pairing.result = result_data.result
    pairing.chess_com_game_url = result_data.chess_com_game_url
    pairing.played_at = datetime.utcnow()

    # Update player scores
    await _update_player_scores(db, tournament_id, pairing)

    await db.commit()
    await db.refresh(pairing)

    # Send WebSocket notifications
    result_str = result_data.result.value if hasattr(result_data.result, 'value') else str(result_data.result)
    await notify_result_submitted(
        tournament_id=tournament_id,
        pairing_id=pairing_id,
        white_player_id=pairing.white_player_id,
        black_player_id=pairing.black_player_id,
        result=result_str
    )
    await notify_standings_updated(tournament_id)

    return await build_pairing_response(db, pairing)


async def _update_player_scores(db: AsyncSession, tournament_id: str, pairing: Pairing):
    """Update player scores based on game result"""
    if pairing.is_bye:
        return

    # Get tournament players
    white_tp = None
    black_tp = None

    if pairing.white_player_id:
        result = await db.execute(
            select(TournamentPlayer).where(
                TournamentPlayer.tournament_id == tournament_id,
                TournamentPlayer.player_id == pairing.white_player_id
            )
        )
        white_tp = result.scalar_one_or_none()

    if pairing.black_player_id:
        result = await db.execute(
            select(TournamentPlayer).where(
                TournamentPlayer.tournament_id == tournament_id,
                TournamentPlayer.player_id == pairing.black_player_id
            )
        )
        black_tp = result.scalar_one_or_none()

    # Update based on result
    if pairing.result == GameResult.WHITE_WINS:
        if white_tp:
            white_tp.score += 1.0
            white_tp.wins += 1
            white_tp.games_as_white += 1
        if black_tp:
            black_tp.losses += 1
            black_tp.games_as_black += 1

    elif pairing.result == GameResult.BLACK_WINS:
        if black_tp:
            black_tp.score += 1.0
            black_tp.wins += 1
            black_tp.games_as_black += 1
        if white_tp:
            white_tp.losses += 1
            white_tp.games_as_white += 1

    elif pairing.result == GameResult.DRAW:
        if white_tp:
            white_tp.score += 0.5
            white_tp.draws += 1
            white_tp.games_as_white += 1
        if black_tp:
            black_tp.score += 0.5
            black_tp.draws += 1
            black_tp.games_as_black += 1

    elif pairing.result == GameResult.WHITE_FORFEIT:
        if black_tp:
            black_tp.score += 1.0
            black_tp.wins += 1
        if white_tp:
            white_tp.losses += 1

    elif pairing.result == GameResult.BLACK_FORFEIT:
        if white_tp:
            white_tp.score += 1.0
            white_tp.wins += 1
        if black_tp:
            black_tp.losses += 1

    elif pairing.result == GameResult.DOUBLE_FORFEIT:
        # Both players lose, neither gets points
        if white_tp:
            white_tp.losses += 1
        if black_tp:
            black_tp.losses += 1


@router.post("/{tournament_id}/pairings/{pairing_id}/submit-game", response_model=GameVerificationResult)
async def submit_game_url(
    tournament_id: str,
    pairing_id: str,
    submission: GameUrlSubmission,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Submit a Chess.com game URL to automatically verify and record the result.

    Either player in the pairing can submit the game URL.
    The system will:
    1. Fetch the game from Chess.com
    2. Verify both players match the pairing
    3. Verify the game was played after the pairing was created
    4. Automatically record the result
    """
    # Get pairing
    result = await db.execute(
        select(Pairing).where(
            Pairing.id == pairing_id,
            Pairing.tournament_id == tournament_id
        )
    )
    pairing = result.scalar_one_or_none()

    if not pairing:
        raise HTTPException(status_code=404, detail="Pairing not found")

    # Check authorization (admin or participant)
    is_participant = (
        current_player.id == pairing.white_player_id or
        current_player.id == pairing.black_player_id
    )
    if not current_player.is_admin and not is_participant:
        raise HTTPException(status_code=403, detail="Not authorized to submit result for this pairing")

    # Check if result already recorded
    if pairing.result not in [GameResult.PENDING]:
        raise HTTPException(status_code=400, detail="Result already recorded for this pairing")

    # Get player usernames
    white_player = await db.execute(
        select(Player).where(Player.id == pairing.white_player_id)
    )
    white_player = white_player.scalar_one_or_none()

    black_player = await db.execute(
        select(Player).where(Player.id == pairing.black_player_id)
    )
    black_player = black_player.scalar_one_or_none()

    if not white_player or not black_player:
        raise HTTPException(status_code=400, detail="Could not find players for this pairing")

    # Verify game via Chess.com API
    verification = await chess_com_service.verify_game_result(
        game_url=submission.game_url,
        expected_white=white_player.chess_com_username,
        expected_black=black_player.chess_com_username,
        pairing_created_at=pairing.created_at
    )

    if not verification.get("valid"):
        return GameVerificationResult(
            valid=False,
            error=verification.get("error", "Unknown error"),
            pairing_updated=False
        )

    # Map result string to GameResult enum
    result_map = {
        "white_wins": GameResult.WHITE_WINS,
        "black_wins": GameResult.BLACK_WINS,
        "draw": GameResult.DRAW
    }
    game_result = result_map.get(verification["result"])

    if not game_result:
        return GameVerificationResult(
            valid=False,
            error=f"Unknown result: {verification['result']}",
            pairing_updated=False
        )

    # Update pairing
    pairing.result = game_result
    pairing.chess_com_game_url = submission.game_url
    pairing.chess_com_game_id = verification.get("game_id")
    pairing.played_at = verification.get("played_at") or datetime.utcnow()

    # Update player scores
    await _update_player_scores(db, tournament_id, pairing)

    await db.commit()

    # Send WebSocket notifications
    await notify_result_submitted(
        tournament_id=tournament_id,
        pairing_id=pairing_id,
        white_player_id=pairing.white_player_id,
        black_player_id=pairing.black_player_id,
        result=verification["result"]
    )
    await notify_standings_updated(tournament_id)

    return GameVerificationResult(
        valid=True,
        result=verification["result"],
        game_id=verification.get("game_id"),
        played_at=verification.get("played_at"),
        pairing_updated=True
    )


@router.get("/{tournament_id}/my-pairings", response_model=List[PairingResponse])
async def get_my_pairings(
    tournament_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Get all pairings for the current player in a tournament"""
    result = await db.execute(
        select(Pairing).where(
            Pairing.tournament_id == tournament_id,
            (Pairing.white_player_id == current_player.id) |
            (Pairing.black_player_id == current_player.id)
        ).order_by(Pairing.round_number)
    )
    pairings = result.scalars().all()

    responses = []
    for p in pairings:
        responses.append(await build_pairing_response(db, p))

    return responses


@router.get("/{tournament_id}/current-round", response_model=List[PairingResponse])
async def get_current_round_pairings(
    tournament_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get pairings for the current round"""
    # Get tournament
    result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()

    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")

    if tournament.current_round == 0:
        return []

    result = await db.execute(
        select(Pairing).where(
            Pairing.tournament_id == tournament_id,
            Pairing.round_number == tournament.current_round
        ).order_by(Pairing.board_number)
    )
    pairings = result.scalars().all()

    responses = []
    for p in pairings:
        responses.append(await build_pairing_response(db, p))

    return responses


@router.post("/{tournament_id}/pairings/{pairing_id}/claim-no-show")
async def claim_opponent_no_show(
    tournament_id: str,
    pairing_id: str,
    claim: NoShowClaim,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Claim that your opponent didn't show up for the game.

    If opponent doesn't submit a game URL by the deadline, they will be forfeited.
    """
    # Get pairing
    result = await db.execute(
        select(Pairing).where(
            Pairing.id == pairing_id,
            Pairing.tournament_id == tournament_id
        )
    )
    pairing = result.scalar_one_or_none()

    if not pairing:
        raise HTTPException(status_code=404, detail="Pairing not found")

    # Must be a participant
    is_white = current_player.id == pairing.white_player_id
    is_black = current_player.id == pairing.black_player_id

    if not is_white and not is_black:
        raise HTTPException(status_code=403, detail="You are not a participant in this pairing")

    # Check if result already recorded
    if pairing.result != GameResult.PENDING:
        raise HTTPException(status_code=400, detail="Result already recorded")

    # Check if already claimed
    if pairing.no_show_claimed_by:
        if pairing.no_show_claimed_by == current_player.id:
            return {"message": "You have already claimed no-show", "claimed_at": pairing.no_show_claimed_at}
        else:
            raise HTTPException(
                status_code=400,
                detail="Your opponent has claimed you didn't show up. Submit the game URL to dispute."
            )

    # Record the claim
    pairing.no_show_claimed_by = current_player.id
    pairing.no_show_claimed_at = datetime.utcnow()

    await db.commit()

    # Notify the accused player via WebSocket
    accused_player_id = pairing.black_player_id if is_white else pairing.white_player_id
    await notify_no_show_claimed(
        tournament_id=tournament_id,
        pairing_id=pairing_id,
        claimed_by=current_player.id,
        accused_player_id=accused_player_id
    )

    # Send push notification to accused player
    import json
    result = await db.execute(select(Player).where(Player.id == accused_player_id))
    accused_player = result.scalar_one_or_none()

    if accused_player and accused_player.push_subscription and accused_player.push_enabled:
        # Get tournament name
        t_result = await db.execute(select(Tournament).where(Tournament.id == tournament_id))
        tournament = t_result.scalar_one_or_none()
        tournament_name = tournament.name if tournament else "Tournament"

        try:
            subscription = json.loads(accused_player.push_subscription)
            await notify_no_show_push(
                subscription=subscription,
                tournament_name=tournament_name,
                tournament_id=tournament_id,
                pairing_id=pairing_id
            )
        except json.JSONDecodeError:
            pass

    # In-app notification to accused player
    t_result2 = await db.execute(select(Tournament).where(Tournament.id == tournament_id))
    tournament_obj = t_result2.scalar_one_or_none()
    await create_notification(
        db, accused_player_id, "no_show",
        "No-Show Claimed",
        f"{current_player.chess_com_username} claims you didn't show up in {tournament_obj.name if tournament_obj else 'a tournament'}. Submit game URL to dispute.",
        {"tournament_id": tournament_id, "pairing_id": pairing_id},
    )
    await db.commit()

    return {
        "message": "No-show claim recorded. If opponent doesn't submit game URL by deadline, they will be forfeited.",
        "deadline": pairing.deadline,
        "claimed_at": pairing.no_show_claimed_at
    }


@router.post("/{tournament_id}/process-deadlines", response_model=DeadlineProcessingResult)
async def process_expired_deadlines(
    tournament_id: str,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_admin)
):
    """
    Process all expired pairings in a tournament (admin only).

    This will:
    1. Forfeit players who had no-show claimed against them and didn't submit game URL
    2. Double-forfeit pairings where neither player submitted anything

    Call this periodically or manually after round deadlines pass.
    """
    now = datetime.utcnow()

    # Get all pending pairings past deadline
    result = await db.execute(
        select(Pairing).where(
            Pairing.tournament_id == tournament_id,
            Pairing.result == GameResult.PENDING,
            Pairing.deadline < now
        )
    )
    expired_pairings = result.scalars().all()

    processed = 0
    forfeits = 0
    double_forfeits = 0
    details = []

    for pairing in expired_pairings:
        processed += 1

        if pairing.no_show_claimed_by:
            # Someone claimed no-show - forfeit the other player
            if pairing.no_show_claimed_by == pairing.white_player_id:
                # White claimed black didn't show
                pairing.result = GameResult.BLACK_FORFEIT
                forfeits += 1
                details.append({
                    "pairing_id": pairing.id,
                    "round": pairing.round_number,
                    "action": "black_forfeited",
                    "reason": "No-show claimed by white, no game submitted"
                })
            else:
                # Black claimed white didn't show
                pairing.result = GameResult.WHITE_FORFEIT
                forfeits += 1
                details.append({
                    "pairing_id": pairing.id,
                    "round": pairing.round_number,
                    "action": "white_forfeited",
                    "reason": "No-show claimed by black, no game submitted"
                })
        else:
            # Neither player did anything - double forfeit
            pairing.result = GameResult.DOUBLE_FORFEIT
            double_forfeits += 1
            details.append({
                "pairing_id": pairing.id,
                "round": pairing.round_number,
                "action": "double_forfeit",
                "reason": "Neither player submitted result by deadline"
            })

        pairing.played_at = now

        # Update scores
        await _update_player_scores(db, tournament_id, pairing)

        # Notify both players about the result
        await notify_result_submitted(
            tournament_id=tournament_id,
            pairing_id=pairing.id,
            white_player_id=pairing.white_player_id,
            black_player_id=pairing.black_player_id,
            result=pairing.result.value
        )

    await db.commit()

    # Notify standings update if any pairings were processed
    if processed > 0:
        await notify_standings_updated(tournament_id)

    return DeadlineProcessingResult(
        processed_count=processed,
        forfeits=forfeits,
        double_forfeits=double_forfeits,
        details=details
    )


@router.get("/{tournament_id}/expired-pairings")
async def get_expired_pairings(
    tournament_id: str,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_admin)
):
    """
    Get all pairings past their deadline that haven't been resolved (admin only).
    Use this to see what will be processed by process-deadlines.
    """
    now = datetime.utcnow()

    result = await db.execute(
        select(Pairing).where(
            Pairing.tournament_id == tournament_id,
            Pairing.result == GameResult.PENDING,
            Pairing.deadline < now
        ).order_by(Pairing.round_number, Pairing.board_number)
    )
    expired = result.scalars().all()

    pairings_info = []
    for p in expired:
        # Get player names
        white_name = None
        black_name = None

        if p.white_player_id:
            r = await db.execute(select(Player).where(Player.id == p.white_player_id))
            wp = r.scalar_one_or_none()
            white_name = wp.chess_com_username if wp else None

        if p.black_player_id:
            r = await db.execute(select(Player).where(Player.id == p.black_player_id))
            bp = r.scalar_one_or_none()
            black_name = bp.chess_com_username if bp else None

        pairings_info.append({
            "pairing_id": p.id,
            "round": p.round_number,
            "board": p.board_number,
            "white": white_name,
            "black": black_name,
            "deadline": p.deadline,
            "hours_overdue": round((now - p.deadline).total_seconds() / 3600, 1),
            "no_show_claimed_by": "white" if p.no_show_claimed_by == p.white_player_id else (
                "black" if p.no_show_claimed_by == p.black_player_id else None
            )
        })

    return {
        "count": len(pairings_info),
        "pairings": pairings_info
    }


# ============================================================================
# IN-PERSON TOURNAMENT RESULT CLAIM/CONFIRMATION ENDPOINTS
# ============================================================================

@router.post("/{tournament_id}/pairings/{pairing_id}/claim-result", response_model=PairingResponse)
async def claim_result(
    tournament_id: str,
    pairing_id: str,
    claim: ResultClaim,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Claim a game result (for in-person tournaments).

    Either player can submit a claim. The opponent must confirm within
    the tournament's result_confirmation_minutes setting.

    Valid results: white_wins, black_wins, draw
    """
    # Get tournament to check if it's in-person
    result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()

    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")

    if tournament.is_online:
        raise HTTPException(
            status_code=400,
            detail="This is an online tournament. Please submit the Chess.com game URL instead."
        )

    if tournament.status != TournamentStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Tournament is not active")

    # Get pairing
    result = await db.execute(
        select(Pairing).where(Pairing.id == pairing_id, Pairing.tournament_id == tournament_id)
    )
    pairing = result.scalar_one_or_none()

    if not pairing:
        raise HTTPException(status_code=404, detail="Pairing not found")

    # Verify current player is in this pairing
    is_white = current_player.id == pairing.white_player_id
    is_black = current_player.id == pairing.black_player_id

    if not is_white and not is_black:
        raise HTTPException(status_code=403, detail="You are not a player in this pairing")

    # Check if result already recorded
    if pairing.result != GameResult.PENDING:
        raise HTTPException(status_code=400, detail="Result already recorded for this game")

    # Check if there's already a pending claim
    if pairing.has_pending_claim:
        raise HTTPException(
            status_code=400,
            detail="A result claim is already pending. Wait for opponent to confirm or dispute."
        )

    # Validate result
    if claim.result not in [GameResult.WHITE_WINS, GameResult.BLACK_WINS, GameResult.DRAW]:
        raise HTTPException(status_code=400, detail="Invalid result. Use white_wins, black_wins, or draw.")

    # Set the claim
    now = datetime.utcnow()
    pairing.claimed_result = claim.result
    pairing.claimed_by = current_player.id
    pairing.claimed_at = now
    pairing.confirmation_deadline = now + timedelta(minutes=tournament.result_confirmation_minutes)

    await db.commit()
    await db.refresh(pairing)

    # Determine opponent
    opponent_id = pairing.black_player_id if is_white else pairing.white_player_id

    # Get opponent for notifications
    result = await db.execute(select(Player).where(Player.id == opponent_id))
    opponent = result.scalar_one_or_none()

    # Send WebSocket notification to opponent
    await notify_result_claimed(
        tournament_id=tournament_id,
        pairing_id=pairing_id,
        claimer_id=current_player.id,
        opponent_id=opponent_id,
        claimed_result=claim.result.value,
        confirmation_deadline=pairing.confirmation_deadline.isoformat()
    )

    # Send push notification to opponent
    if opponent and opponent.push_subscription and opponent.push_enabled:
        try:
            subscription = json.loads(opponent.push_subscription)
            await notify_result_claim_push(
                subscription=subscription,
                claimer_username=current_player.chess_com_username,
                claimed_result=claim.result.value,
                tournament_name=tournament.name,
                tournament_id=tournament_id,
                pairing_id=pairing_id,
                minutes_to_confirm=tournament.result_confirmation_minutes
            )
        except Exception as e:
            print(f"[PUSH] Failed to send claim notification: {e}")

    # In-app notification to opponent
    await create_notification(
        db, opponent_id, "claim",
        "Result Claimed",
        f"{current_player.chess_com_username} claims {claim.result.value} in {tournament.name}. Please confirm or dispute.",
        {"tournament_id": tournament_id, "pairing_id": pairing_id},
    )
    await db.commit()

    return await build_pairing_response(db, pairing)


@router.post("/{tournament_id}/pairings/{pairing_id}/confirm", response_model=PairingResponse)
async def confirm_result(
    tournament_id: str,
    pairing_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Confirm a claimed result (opponent confirms).

    Only the opponent (not the claimer) can confirm.
    Once confirmed, the result is saved and standings are updated.
    """
    # Get pairing
    result = await db.execute(
        select(Pairing).where(Pairing.id == pairing_id, Pairing.tournament_id == tournament_id)
    )
    pairing = result.scalar_one_or_none()

    if not pairing:
        raise HTTPException(status_code=404, detail="Pairing not found")

    # Check if there's a pending claim
    if not pairing.has_pending_claim:
        raise HTTPException(status_code=400, detail="No pending result claim to confirm")

    # Verify current player is the opponent (not the claimer)
    is_white = current_player.id == pairing.white_player_id
    is_black = current_player.id == pairing.black_player_id

    if not is_white and not is_black:
        raise HTTPException(status_code=403, detail="You are not a player in this pairing")

    if current_player.id == pairing.claimed_by:
        raise HTTPException(status_code=400, detail="You cannot confirm your own claim")

    # Confirm the result
    now = datetime.utcnow()
    pairing.result = pairing.claimed_result
    pairing.confirmed_by = current_player.id
    pairing.confirmed_at = now
    pairing.played_at = now

    # Update player scores
    await _update_player_scores(db, tournament_id, pairing)

    await db.commit()
    await db.refresh(pairing)

    # Get claimer for notifications
    result = await db.execute(select(Player).where(Player.id == pairing.claimed_by))
    claimer = result.scalar_one_or_none()

    # Get tournament name
    result = await db.execute(select(Tournament).where(Tournament.id == tournament_id))
    tournament = result.scalar_one_or_none()

    # Send WebSocket notifications
    await notify_result_confirmed(
        tournament_id=tournament_id,
        pairing_id=pairing_id,
        claimer_id=pairing.claimed_by,
        confirmer_id=current_player.id,
        final_result=pairing.result.value
    )

    # Send push notification to claimer
    if claimer and claimer.push_subscription and claimer.push_enabled:
        try:
            subscription = json.loads(claimer.push_subscription)
            await notify_result_confirmed_push(
                subscription=subscription,
                confirmer_username=current_player.chess_com_username,
                result=pairing.result.value,
                tournament_name=tournament.name if tournament else "Tournament",
                tournament_id=tournament_id
            )
        except Exception as e:
            print(f"[PUSH] Failed to send confirmation notification: {e}")

    # In-app notification to claimer
    await create_notification(
        db, pairing.claimed_by, "confirm",
        "Result Confirmed",
        f"{current_player.chess_com_username} confirmed {pairing.result.value} in {tournament.name if tournament else 'a tournament'}.",
        {"tournament_id": tournament_id, "pairing_id": pairing_id},
    )
    await db.commit()

    return await build_pairing_response(db, pairing)


@router.post("/{tournament_id}/pairings/{pairing_id}/dispute", response_model=PairingResponse)
async def dispute_result(
    tournament_id: str,
    pairing_id: str,
    dispute: ResultDispute,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Dispute a claimed result (opponent disagrees).

    Only the opponent (not the claimer) can dispute.
    Disputed pairings are escalated to the arbiter/admin for resolution.
    """
    # Get pairing
    result = await db.execute(
        select(Pairing).where(Pairing.id == pairing_id, Pairing.tournament_id == tournament_id)
    )
    pairing = result.scalar_one_or_none()

    if not pairing:
        raise HTTPException(status_code=404, detail="Pairing not found")

    # Check if there's a pending claim
    if not pairing.has_pending_claim:
        raise HTTPException(status_code=400, detail="No pending result claim to dispute")

    # Verify current player is the opponent (not the claimer)
    is_white = current_player.id == pairing.white_player_id
    is_black = current_player.id == pairing.black_player_id

    if not is_white and not is_black:
        raise HTTPException(status_code=403, detail="You are not a player in this pairing")

    if current_player.id == pairing.claimed_by:
        raise HTTPException(status_code=400, detail="You cannot dispute your own claim")

    # Mark as disputed
    pairing.is_disputed = True
    pairing.dispute_reason = dispute.reason

    await db.commit()
    await db.refresh(pairing)

    # Get claimer and tournament for notifications
    result = await db.execute(select(Player).where(Player.id == pairing.claimed_by))
    claimer = result.scalar_one_or_none()

    result = await db.execute(select(Tournament).where(Tournament.id == tournament_id))
    tournament = result.scalar_one_or_none()

    # Get player names for admin notification
    white_player = await get_player_brief(db, pairing.white_player_id)
    black_player = await get_player_brief(db, pairing.black_player_id)

    # Send WebSocket notification to claimer
    await notify_result_dispute(
        tournament_id=tournament_id,
        pairing_id=pairing_id,
        claimer_id=pairing.claimed_by,
        disputer_id=current_player.id,
        reason=dispute.reason
    )

    # Send push notification to claimer
    if claimer and claimer.push_subscription and claimer.push_enabled:
        try:
            subscription = json.loads(claimer.push_subscription)
            await notify_result_disputed_push(
                subscription=subscription,
                disputer_username=current_player.chess_com_username,
                tournament_name=tournament.name if tournament else "Tournament",
                tournament_id=tournament_id,
                pairing_id=pairing_id,
                reason=dispute.reason
            )
        except Exception as e:
            print(f"[PUSH] Failed to send dispute notification: {e}")

    # Notify admins about the dispute
    admin_result = await db.execute(
        select(Player).where(Player.is_admin == True, Player.push_enabled == True)
    )
    admins = admin_result.scalars().all()

    for admin in admins:
        if admin.push_subscription:
            try:
                subscription = json.loads(admin.push_subscription)
                await notify_admin_disputed_push(
                    subscription=subscription,
                    tournament_name=tournament.name if tournament else "Tournament",
                    tournament_id=tournament_id,
                    pairing_id=pairing_id,
                    white_username=white_player.chess_com_username if white_player else "Unknown",
                    black_username=black_player.chess_com_username if black_player else "Unknown"
                )
            except Exception as e:
                print(f"[PUSH] Failed to notify admin {admin.id}: {e}")

    # In-app notification to claimer about dispute
    await create_notification(
        db, pairing.claimed_by, "dispute",
        "Result Disputed",
        f"{current_player.chess_com_username} disputed your result claim in {tournament.name if tournament else 'a tournament'}.",
        {"tournament_id": tournament_id, "pairing_id": pairing_id, "reason": dispute.reason},
    )
    await db.commit()

    return await build_pairing_response(db, pairing)


@router.post("/{tournament_id}/pairings/{pairing_id}/cancel-claim", response_model=PairingResponse)
async def cancel_claim(
    tournament_id: str,
    pairing_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Cancel your own result claim (within 2 minutes).

    Only the claimer can cancel, and only within 2 minutes of claiming.
    Use this if you accidentally submitted the wrong result.
    """
    # Get pairing
    result = await db.execute(
        select(Pairing).where(Pairing.id == pairing_id, Pairing.tournament_id == tournament_id)
    )
    pairing = result.scalar_one_or_none()

    if not pairing:
        raise HTTPException(status_code=404, detail="Pairing not found")

    # Check if there's a pending claim
    if not pairing.has_pending_claim:
        raise HTTPException(status_code=400, detail="No pending claim to cancel")

    # Verify current player is the claimer
    if current_player.id != pairing.claimed_by:
        raise HTTPException(status_code=403, detail="Only the claimer can cancel the claim")

    # Check if still within cancellation window (2 minutes)
    if not pairing.can_cancel_claim:
        raise HTTPException(
            status_code=400,
            detail="Cancellation window expired. Claims can only be cancelled within 2 minutes."
        )

    # Determine opponent for notification
    opponent_id = (
        pairing.black_player_id
        if current_player.id == pairing.white_player_id
        else pairing.white_player_id
    )

    # Clear the claim
    pairing.claimed_result = None
    pairing.claimed_by = None
    pairing.claimed_at = None
    pairing.confirmation_deadline = None

    await db.commit()
    await db.refresh(pairing)

    # Notify opponent that claim was cancelled
    if opponent_id:
        await notify_claim_cancelled(
            tournament_id=tournament_id,
            pairing_id=pairing_id,
            opponent_id=opponent_id
        )

    return await build_pairing_response(db, pairing)


@router.get("/{tournament_id}/pending-confirmations", response_model=List[PendingConfirmationResponse])
async def get_pending_confirmations(
    tournament_id: str,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_admin)
):
    """
    Get all pairings with pending result confirmations (admin only).

    Returns pairings where a result was claimed but not yet confirmed.
    Useful for the arbiter dashboard.
    """
    # Get tournament
    result = await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )
    tournament = result.scalar_one_or_none()

    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")

    # Get pairings with pending claims
    result = await db.execute(
        select(Pairing).where(
            Pairing.tournament_id == tournament_id,
            Pairing.claimed_result != None,
            Pairing.result == GameResult.PENDING
        ).order_by(Pairing.round_number, Pairing.board_number)
    )
    pairings = result.scalars().all()

    responses = []
    for pairing in pairings:
        white_player = await get_player_brief(db, pairing.white_player_id)
        black_player = await get_player_brief(db, pairing.black_player_id)

        # Get claimer username
        claimer_result = await db.execute(select(Player).where(Player.id == pairing.claimed_by))
        claimer = claimer_result.scalar_one_or_none()

        responses.append(PendingConfirmationResponse(
            pairing_id=pairing.id,
            tournament_id=tournament_id,
            tournament_name=tournament.name,
            round_number=pairing.round_number,
            board_number=pairing.board_number,
            white_player=white_player,
            black_player=black_player,
            claimed_result=pairing.claimed_result,
            claimed_by=pairing.claimed_by,
            claimed_by_username=claimer.chess_com_username if claimer else "Unknown",
            claimed_at=pairing.claimed_at,
            confirmation_deadline=pairing.confirmation_deadline,
            is_disputed=pairing.is_disputed,
            dispute_reason=pairing.dispute_reason
        ))

    return responses


@router.get("/{tournament_id}/disputed-results")
async def get_disputed_results(
    tournament_id: str,
    db: AsyncSession = Depends(get_db),
    _: Player = Depends(get_current_admin)
):
    """
    Get all disputed pairings that need arbiter resolution (admin only).
    """
    # Get pairings with disputes
    result = await db.execute(
        select(Pairing).where(
            Pairing.tournament_id == tournament_id,
            Pairing.is_disputed == True,
            Pairing.result == GameResult.PENDING
        ).order_by(Pairing.round_number, Pairing.board_number)
    )
    pairings = result.scalars().all()

    disputes = []
    for pairing in pairings:
        white_player = await get_player_brief(db, pairing.white_player_id)
        black_player = await get_player_brief(db, pairing.black_player_id)

        # Get claimer username
        claimer_result = await db.execute(select(Player).where(Player.id == pairing.claimed_by))
        claimer = claimer_result.scalar_one_or_none()

        disputes.append({
            "pairing_id": pairing.id,
            "round_number": pairing.round_number,
            "board_number": pairing.board_number,
            "white_player": white_player.dict() if white_player else None,
            "black_player": black_player.dict() if black_player else None,
            "claimed_result": pairing.claimed_result.value if pairing.claimed_result else None,
            "claimed_by": pairing.claimed_by,
            "claimed_by_username": claimer.chess_com_username if claimer else "Unknown",
            "dispute_reason": pairing.dispute_reason,
            "claimed_at": pairing.claimed_at.isoformat() if pairing.claimed_at else None
        })

    return {
        "count": len(disputes),
        "disputes": disputes
    }


@router.post("/{tournament_id}/pairings/{pairing_id}/admin-override", response_model=PairingResponse)
async def admin_override_result(
    tournament_id: str,
    pairing_id: str,
    override: AdminOverrideResult,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin)
):
    """
    Admin/arbiter override to set or change a result.

    Can be used to:
    - Resolve disputed results
    - Correct mistakes
    - Set result when confirmation times out

    This bypasses the normal claim/confirm flow.
    """
    # Get pairing
    result = await db.execute(
        select(Pairing).where(Pairing.id == pairing_id, Pairing.tournament_id == tournament_id)
    )
    pairing = result.scalar_one_or_none()

    if not pairing:
        raise HTTPException(status_code=404, detail="Pairing not found")

    # Only allow setting results for pending games or overriding existing
    was_pending = pairing.result == GameResult.PENDING

    # Set the result
    now = datetime.utcnow()
    old_result = pairing.result

    pairing.result = override.result
    pairing.played_at = now
    pairing.confirmed_by = admin.id  # Admin is the confirmer
    pairing.confirmed_at = now
    pairing.is_disputed = False  # Clear dispute flag

    # Clear claim fields if this was an override
    if pairing.claimed_result:
        pairing.claimed_result = None
        pairing.claimed_by = None
        pairing.claimed_at = None
        pairing.confirmation_deadline = None

    # Update player scores (only if it was pending before)
    if was_pending:
        await _update_player_scores(db, tournament_id, pairing)

    await db.commit()
    await db.refresh(pairing)

    # Notify both players
    await notify_result_submitted(
        tournament_id=tournament_id,
        pairing_id=pairing_id,
        white_player_id=pairing.white_player_id,
        black_player_id=pairing.black_player_id,
        result=pairing.result.value
    )

    # Update standings
    await notify_standings_updated(tournament_id)

    return await build_pairing_response(db, pairing)


# ============================================================================
# PLAYER MATCHES ENDPOINT (All matches across tournaments)
# ============================================================================

matches_router = APIRouter(prefix="/api/matches", tags=["Matches"])


async def build_match_response(db: AsyncSession, pairing: Pairing, tournament: Tournament) -> MatchResponse:
    """Convert Pairing model to MatchResponse with tournament details"""
    white_player = await get_player_brief(db, pairing.white_player_id)
    black_player = await get_player_brief(db, pairing.black_player_id)

    tournament_brief = TournamentBrief(
        id=tournament.id,
        name=tournament.name,
        is_online=tournament.is_online,
        time_control=tournament.time_control,
        status=tournament.status.value
    )

    return MatchResponse(
        id=pairing.id,
        tournament_id=pairing.tournament_id,
        tournament=tournament_brief,
        round_number=pairing.round_number,
        board_number=pairing.board_number,
        white_player=white_player,
        black_player=black_player,
        result=pairing.result,
        chess_com_game_url=pairing.chess_com_game_url,
        scheduled_time=pairing.scheduled_time,
        played_at=pairing.played_at,
        deadline=pairing.deadline,
        no_show_claimed_by=pairing.no_show_claimed_by,
        is_bye=pairing.is_bye,
        claimed_result=pairing.claimed_result,
        claimed_by=pairing.claimed_by,
        claimed_at=pairing.claimed_at,
        confirmation_deadline=pairing.confirmation_deadline,
        confirmed_by=pairing.confirmed_by,
        confirmed_at=pairing.confirmed_at,
        is_disputed=pairing.is_disputed,
        dispute_reason=pairing.dispute_reason,
        has_pending_claim=pairing.has_pending_claim,
        can_cancel_claim=pairing.can_cancel_claim
    )


@matches_router.get("/my-matches", response_model=List[MatchResponse])
async def get_my_matches(
    status: Optional[str] = None,  # pending, completed, action_required
    tournament_type: Optional[str] = None,  # online, inperson
    tournament_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Get all matches for the current player across all tournaments.

    Filters:
    - status: pending, completed, action_required (needs confirmation or has pending claim)
    - tournament_type: online, inperson
    - tournament_id: specific tournament
    """
    # Build base query for pairings where player is involved
    query = select(Pairing).where(
        (Pairing.white_player_id == current_player.id) |
        (Pairing.black_player_id == current_player.id)
    )

    # Filter by tournament if specified
    if tournament_id:
        query = query.where(Pairing.tournament_id == tournament_id)

    # Filter by status
    if status == "pending":
        query = query.where(Pairing.result == GameResult.PENDING)
    elif status == "completed":
        query = query.where(Pairing.result != GameResult.PENDING)
    elif status == "action_required":
        # Matches where player needs to take action:
        # 1. Opponent claimed result and needs confirmation
        # 2. Player's claim was disputed
        query = query.where(
            and_(
                Pairing.result == GameResult.PENDING,
                (
                    # Opponent made a claim that needs confirmation
                    (Pairing.claimed_by != None) & (Pairing.claimed_by != current_player.id) |
                    # Player's claim was disputed
                    ((Pairing.claimed_by == current_player.id) & (Pairing.is_disputed == True))
                )
            )
        )

    # Order by most recent first (pending games first, then by date)
    query = query.order_by(
        Pairing.result == GameResult.PENDING,  # Pending first
        Pairing.played_at.desc().nulls_first()  # Most recent
    ).offset(skip).limit(limit)

    result = await db.execute(query)
    pairings = result.scalars().all()

    # Get tournaments for filtering by type and building responses
    tournament_ids = list(set(p.tournament_id for p in pairings))
    tournaments_map = {}

    if tournament_ids:
        t_result = await db.execute(
            select(Tournament).where(Tournament.id.in_(tournament_ids))
        )
        tournaments = t_result.scalars().all()
        tournaments_map = {t.id: t for t in tournaments}

    # Build responses, filtering by tournament type if needed
    responses = []
    for pairing in pairings:
        tournament = tournaments_map.get(pairing.tournament_id)
        if not tournament:
            continue

        # Filter by tournament type
        if tournament_type:
            if tournament_type == "online" and not tournament.is_online:
                continue
            if tournament_type == "inperson" and tournament.is_online:
                continue

        responses.append(await build_match_response(db, pairing, tournament))

    return responses


@matches_router.get("/action-required/count")
async def get_action_required_count(
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Get count of matches requiring player action.

    Used for notification badges.
    """
    from sqlalchemy import func

    # Count matches where player needs to confirm opponent's claim
    result = await db.execute(
        select(func.count(Pairing.id)).where(
            and_(
                Pairing.result == GameResult.PENDING,
                (
                    (Pairing.white_player_id == current_player.id) |
                    (Pairing.black_player_id == current_player.id)
                ),
                Pairing.claimed_by != None,
                Pairing.claimed_by != current_player.id
            )
        )
    )
    needs_confirmation = result.scalar() or 0

    # Count matches where player's claim was disputed
    result = await db.execute(
        select(func.count(Pairing.id)).where(
            and_(
                Pairing.result == GameResult.PENDING,
                Pairing.claimed_by == current_player.id,
                Pairing.is_disputed == True
            )
        )
    )
    disputed = result.scalar() or 0

    return {
        "total": needs_confirmation + disputed,
        "needs_confirmation": needs_confirmation,
        "disputed": disputed
    }
