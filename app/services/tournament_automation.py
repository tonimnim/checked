"""
Background service for fully automated tournament management.

This service runs every 5 minutes and:
1. Scans Chess.com for games between paired players (auto-detects results)
2. Processes expired deadlines (forfeits)
3. Auto-generates next round when current round is complete
4. Auto-finalizes tournaments when all rounds are done
5. Calculates final rankings
"""
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session_maker
from app.models.player import Player
from app.models.tournament import Tournament, TournamentPlayer, TournamentStatus, TournamentFormat
from app.models.pairing import Pairing, GameResult
from app.services.chess_com import chess_com_service
from app.services.swiss import SwissPairingEngine, SwissPlayer, RoundRobinEngine

logger = logging.getLogger(__name__)

# Run automation every 5 minutes
AUTOMATION_INTERVAL = 5 * 60

# Default deadline hours for new pairings
PAIRING_DEADLINE_HOURS = 24


async def auto_detect_game_result(db: AsyncSession, pairing: Pairing) -> bool:
    """
    Try to automatically find and verify a game between paired players on Chess.com.
    Returns True if a result was found and recorded.
    """
    if pairing.result != GameResult.PENDING:
        return False

    if not pairing.white_player_id or not pairing.black_player_id:
        return False

    # Get player usernames
    white_result = await db.execute(
        select(Player).where(Player.id == pairing.white_player_id)
    )
    white_player = white_result.scalar_one_or_none()

    black_result = await db.execute(
        select(Player).where(Player.id == pairing.black_player_id)
    )
    black_player = black_result.scalar_one_or_none()

    if not white_player or not black_player:
        return False

    # Get tournament to know time control
    tournament_result = await db.execute(
        select(Tournament).where(Tournament.id == pairing.tournament_id)
    )
    tournament = tournament_result.scalar_one_or_none()

    # Determine time class from tournament time control
    time_class = "rapid"  # default
    if tournament and tournament.time_control:
        tc = tournament.time_control.lower()
        if "bullet" in tc or (tc.isdigit() and int(tc) < 180):
            time_class = "bullet"
        elif "blitz" in tc or (tc.isdigit() and int(tc) < 600):
            time_class = "blitz"

    try:
        # Search for a game between these players after pairing was created
        pairing_timestamp = int(pairing.created_at.timestamp()) if pairing.created_at else None

        game = await chess_com_service.find_game_between_players(
            player1=white_player.chess_com_username,
            player2=black_player.chess_com_username,
            time_class=time_class,
            after_timestamp=pairing_timestamp
        )

        if not game:
            return False

        # Determine result
        white_is_white_in_game = game.white_username.lower() == white_player.chess_com_username.lower()

        if white_is_white_in_game:
            if game.white_result == "win":
                pairing.result = GameResult.WHITE_WINS
            elif game.black_result == "win":
                pairing.result = GameResult.BLACK_WINS
            else:
                pairing.result = GameResult.DRAW
        else:
            # Colors are swapped in the actual game
            if game.black_result == "win":
                pairing.result = GameResult.WHITE_WINS
            elif game.white_result == "win":
                pairing.result = GameResult.BLACK_WINS
            else:
                pairing.result = GameResult.DRAW

        pairing.chess_com_game_url = game.url
        pairing.played_at = datetime.fromtimestamp(game.end_time) if game.end_time else datetime.utcnow()

        # Update player scores
        await update_player_scores(db, pairing.tournament_id, pairing)

        logger.info(
            f"Auto-detected game: {white_player.chess_com_username} vs {black_player.chess_com_username} "
            f"= {pairing.result.value}"
        )
        return True

    except Exception as e:
        logger.warning(f"Error detecting game for pairing {pairing.id}: {e}")
        return False


async def update_player_scores(db: AsyncSession, tournament_id: str, pairing: Pairing):
    """Update player scores based on game result."""
    if pairing.is_bye:
        return

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
        if white_tp:
            white_tp.losses += 1
        if black_tp:
            black_tp.losses += 1


async def process_expired_deadlines(db: AsyncSession, tournament: Tournament) -> int:
    """Process all expired pairings - forfeit no-shows."""
    now = datetime.utcnow()

    result = await db.execute(
        select(Pairing).where(
            Pairing.tournament_id == tournament.id,
            Pairing.result == GameResult.PENDING,
            Pairing.deadline < now
        )
    )
    expired_pairings = result.scalars().all()

    processed = 0
    for pairing in expired_pairings:
        if pairing.no_show_claimed_by:
            # Forfeit the player who was claimed as no-show
            if pairing.no_show_claimed_by == pairing.white_player_id:
                pairing.result = GameResult.BLACK_FORFEIT
            else:
                pairing.result = GameResult.WHITE_FORFEIT
        else:
            # Neither player submitted - double forfeit
            pairing.result = GameResult.DOUBLE_FORFEIT

        pairing.played_at = now
        await update_player_scores(db, tournament.id, pairing)
        processed += 1

    if processed > 0:
        logger.info(f"Processed {processed} expired pairings for tournament {tournament.name}")

    return processed


async def check_round_complete(db: AsyncSession, tournament: Tournament) -> bool:
    """Check if current round is complete (all games resolved)."""
    if tournament.current_round == 0:
        return False

    result = await db.execute(
        select(Pairing).where(
            Pairing.tournament_id == tournament.id,
            Pairing.round_number == tournament.current_round,
            Pairing.result == GameResult.PENDING
        )
    )
    pending = result.scalars().all()
    return len(pending) == 0


async def generate_next_round(db: AsyncSession, tournament: Tournament) -> bool:
    """Auto-generate pairings for the next round."""
    next_round = tournament.current_round + 1

    if next_round > tournament.total_rounds:
        return False

    # Get tournament players
    result = await db.execute(
        select(TournamentPlayer)
        .options(selectinload(TournamentPlayer.player))
        .where(
            TournamentPlayer.tournament_id == tournament.id,
            TournamentPlayer.is_withdrawn == False
        )
    )
    tournament_players = result.scalars().all()

    if len(tournament_players) < 2:
        return False

    # Get previous pairings for opponent history
    result = await db.execute(
        select(Pairing).where(
            Pairing.tournament_id == tournament.id,
            Pairing.result != GameResult.BYE
        )
    )
    previous_pairings = result.scalars().all()

    # Build opponent sets
    opponents_map = {tp.player_id: set() for tp in tournament_players}
    for p in previous_pairings:
        if p.white_player_id and p.black_player_id:
            if p.white_player_id in opponents_map:
                opponents_map[p.white_player_id].add(p.black_player_id)
            if p.black_player_id in opponents_map:
                opponents_map[p.black_player_id].add(p.white_player_id)

    # Build player objects
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

    # Generate pairings
    if tournament.format == TournamentFormat.ROUND_ROBIN:
        engine = RoundRobinEngine(pairing_players)
        new_pairings = engine.generate_round(next_round)
    else:
        engine = SwissPairingEngine(pairing_players)
        new_pairings = engine.generate_pairings(next_round)

    # Create pairing records
    deadline = datetime.utcnow() + timedelta(hours=PAIRING_DEADLINE_HOURS)

    for sp in new_pairings:
        pairing = Pairing(
            tournament_id=tournament.id,
            round_number=next_round,
            white_player_id=sp.white_id if sp.white_id else None,
            black_player_id=sp.black_id if sp.black_id else None,
            board_number=sp.board_number,
            result=GameResult.BYE if sp.is_bye else GameResult.PENDING,
            deadline=None if sp.is_bye else deadline
        )

        # Handle bye
        if sp.is_bye:
            result = await db.execute(
                select(TournamentPlayer).where(
                    TournamentPlayer.tournament_id == tournament.id,
                    TournamentPlayer.player_id == sp.white_id
                )
            )
            tp = result.scalar_one_or_none()
            if tp:
                tp.score += 1.0
                tp.wins += 1

        db.add(pairing)

    tournament.current_round = next_round
    logger.info(f"Auto-generated round {next_round} for tournament {tournament.name}")
    return True


async def finalize_tournament(db: AsyncSession, tournament: Tournament):
    """Calculate final rankings and mark tournament as completed."""
    # Get all tournament players
    result = await db.execute(
        select(TournamentPlayer)
        .where(
            TournamentPlayer.tournament_id == tournament.id,
            TournamentPlayer.is_withdrawn == False
        )
    )
    players = result.scalars().all()

    # Get all pairings for head-to-head lookup
    result = await db.execute(
        select(Pairing).where(Pairing.tournament_id == tournament.id)
    )
    all_pairings = result.scalars().all()

    # Build head-to-head results map
    # h2h[player1_id][player2_id] = 1 (won), 0 (lost), 0.5 (draw)
    h2h = {}
    for p in all_pairings:
        if not p.white_player_id or not p.black_player_id:
            continue
        if p.white_player_id not in h2h:
            h2h[p.white_player_id] = {}
        if p.black_player_id not in h2h:
            h2h[p.black_player_id] = {}

        if p.result == GameResult.WHITE_WINS:
            h2h[p.white_player_id][p.black_player_id] = 1
            h2h[p.black_player_id][p.white_player_id] = 0
        elif p.result == GameResult.BLACK_WINS:
            h2h[p.white_player_id][p.black_player_id] = 0
            h2h[p.black_player_id][p.white_player_id] = 1
        elif p.result == GameResult.DRAW:
            h2h[p.white_player_id][p.black_player_id] = 0.5
            h2h[p.black_player_id][p.white_player_id] = 0.5

    # Calculate Buchholz tiebreaker scores (sum of opponents' scores)
    player_scores = {tp.player_id: tp.score for tp in players}

    for tp in players:
        buchholz = 0.0
        for p in all_pairings:
            if p.white_player_id == tp.player_id and p.black_player_id:
                buchholz += player_scores.get(p.black_player_id, 0)
            elif p.black_player_id == tp.player_id and p.white_player_id:
                buchholz += player_scores.get(p.white_player_id, 0)
        tp.buchholz = buchholz

    # Sort with tiebreakers: Score > Buchholz > Head-to-head
    def compare_players(tp1, tp2):
        # Higher score wins
        if tp1.score != tp2.score:
            return tp2.score - tp1.score
        # Higher Buchholz wins
        if tp1.buchholz != tp2.buchholz:
            return tp2.buchholz - tp1.buchholz
        # Head-to-head: if they played, winner ranks higher
        if tp1.player_id in h2h and tp2.player_id in h2h.get(tp1.player_id, {}):
            h2h_result = h2h[tp1.player_id][tp2.player_id]
            if h2h_result == 1:
                return -1  # tp1 beat tp2, tp1 ranks higher
            elif h2h_result == 0:
                return 1   # tp1 lost to tp2, tp2 ranks higher
        # More wins as final tiebreaker
        return tp2.wins - tp1.wins

    from functools import cmp_to_key
    players_sorted = sorted(players, key=cmp_to_key(compare_players))

    # Assign final ranks
    for i, tp in enumerate(players_sorted):
        tp.final_rank = i + 1

    tournament.status = TournamentStatus.COMPLETED
    tournament.end_date = datetime.utcnow()

    logger.info(f"Tournament {tournament.name} completed. Winner: rank 1")


async def process_tournament(db: AsyncSession, tournament: Tournament):
    """Process a single active tournament."""
    # 1. Try to auto-detect game results for pending pairings
    result = await db.execute(
        select(Pairing).where(
            Pairing.tournament_id == tournament.id,
            Pairing.result == GameResult.PENDING
        )
    )
    pending_pairings = result.scalars().all()

    for pairing in pending_pairings:
        await auto_detect_game_result(db, pairing)
        # Small delay between Chess.com API calls
        await asyncio.sleep(1)

    # 2. Process expired deadlines
    await process_expired_deadlines(db, tournament)

    # 3. Check if current round is complete
    if await check_round_complete(db, tournament):
        if tournament.current_round >= tournament.total_rounds:
            # Tournament complete - finalize
            await finalize_tournament(db, tournament)
        else:
            # Generate next round
            await generate_next_round(db, tournament)


async def run_automation_cycle():
    """Run one automation cycle for all active tournaments."""
    async with async_session_maker() as db:
        try:
            # Get all active tournaments
            result = await db.execute(
                select(Tournament).where(Tournament.status == TournamentStatus.ACTIVE)
            )
            tournaments = result.scalars().all()

            for tournament in tournaments:
                try:
                    await process_tournament(db, tournament)
                except Exception as e:
                    logger.error(f"Error processing tournament {tournament.id}: {e}")

            await db.commit()

        except Exception as e:
            logger.error(f"Tournament automation cycle failed: {e}")
            await db.rollback()


async def tournament_automation_loop():
    """Main automation loop - runs forever."""
    logger.info("Starting tournament automation service (5 min interval)")

    # Wait before first run
    await asyncio.sleep(30)

    while True:
        try:
            await run_automation_cycle()
        except Exception as e:
            logger.error(f"Tournament automation error: {e}")

        await asyncio.sleep(AUTOMATION_INTERVAL)


# Global task reference
_automation_task: asyncio.Task | None = None


def start_tournament_automation():
    """Start the tournament automation task."""
    global _automation_task
    if _automation_task is None or _automation_task.done():
        _automation_task = asyncio.create_task(tournament_automation_loop())
        logger.info("Tournament automation task started")


def stop_tournament_automation():
    """Stop the tournament automation task."""
    global _automation_task
    if _automation_task and not _automation_task.done():
        _automation_task.cancel()
        logger.info("Tournament automation task stopped")
