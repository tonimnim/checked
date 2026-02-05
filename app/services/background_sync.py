"""
Background service to sync Chess.com data periodically.
Runs every 30 minutes to keep ratings and profiles fresh.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker
from app.models.player import Player
from app.services.chess_com import chess_com_service

logger = logging.getLogger(__name__)

# Sync interval in seconds (30 minutes)
SYNC_INTERVAL = 30 * 60

# Don't re-sync players updated within this window (avoid redundant calls)
SKIP_IF_UPDATED_WITHIN = timedelta(minutes=25)

# Delay between individual player syncs to avoid rate limiting
PLAYER_SYNC_DELAY = 1.0  # 1 second between players


async def sync_player_data(db: AsyncSession, player: Player) -> bool:
    """Sync a single player's Chess.com data."""
    try:
        # Fetch profile and stats in sequence (not parallel to avoid rate limits)
        profile = await chess_com_service.get_player_profile(player.chess_com_username)

        if profile:
            player.chess_com_avatar = profile.avatar
            player.chess_com_status = profile.status
            player.chess_com_joined = profile.joined

        # Small delay before next API call
        await asyncio.sleep(0.5)

        stats = await chess_com_service.get_player_stats(player.chess_com_username)

        if stats:
            player.rating_rapid = stats.chess_rapid
            player.rating_blitz = stats.chess_blitz
            player.rating_bullet = stats.chess_bullet
            player.ratings_updated_at = datetime.utcnow()

        return True
    except Exception as e:
        logger.warning(f"Failed to sync {player.chess_com_username}: {e}")
        return False


async def run_sync_cycle():
    """Run one sync cycle for all players that need updating."""
    async with async_session_maker() as db:
        try:
            # Get all active players
            result = await db.execute(
                select(Player).where(Player.is_active == True)
            )
            players = result.scalars().all()

            now = datetime.utcnow()
            synced = 0
            skipped = 0
            failed = 0

            for player in players:
                # Skip if recently updated
                if player.ratings_updated_at:
                    time_since_update = now - player.ratings_updated_at
                    if time_since_update < SKIP_IF_UPDATED_WITHIN:
                        skipped += 1
                        continue

                # Sync this player
                success = await sync_player_data(db, player)

                if success:
                    synced += 1
                else:
                    failed += 1

                # Delay between players to respect rate limits
                await asyncio.sleep(PLAYER_SYNC_DELAY)

            # Commit all changes
            await db.commit()

            logger.info(
                f"Background sync complete: {synced} synced, {skipped} skipped, {failed} failed"
            )

        except Exception as e:
            logger.error(f"Background sync cycle failed: {e}")
            await db.rollback()


async def background_sync_loop():
    """Main background sync loop - runs forever."""
    logger.info("Starting background Chess.com sync service (30 min interval)")

    # Wait a bit before first sync to let the app fully start
    await asyncio.sleep(10)

    while True:
        try:
            await run_sync_cycle()
        except Exception as e:
            logger.error(f"Background sync error: {e}")

        # Wait for next cycle
        await asyncio.sleep(SYNC_INTERVAL)


# Global task reference
_sync_task: asyncio.Task | None = None


def start_background_sync():
    """Start the background sync task."""
    global _sync_task
    if _sync_task is None or _sync_task.done():
        _sync_task = asyncio.create_task(background_sync_loop())
        logger.info("Background sync task started")


def stop_background_sync():
    """Stop the background sync task."""
    global _sync_task
    if _sync_task and not _sync_task.done():
        _sync_task.cancel()
        logger.info("Background sync task stopped")
