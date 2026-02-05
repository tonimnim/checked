"""
Script to promote a user to admin.
Usage: python scripts/promote_admin.py <username>
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, update
from app.database import async_session_maker
from app.models.player import Player


async def promote_to_admin(username: str):
    async with async_session_maker() as session:
        # Find the player
        result = await session.execute(
            select(Player).where(Player.chess_com_username == username.lower())
        )
        player = result.scalar_one_or_none()

        if not player:
            print(f"Player '{username}' not found!")
            return False

        if player.is_admin:
            print(f"Player '{username}' is already an admin!")
            return True

        # Promote to admin
        player.is_admin = True
        await session.commit()

        print(f"Successfully promoted '{username}' to admin!")
        return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/promote_admin.py <username>")
        print("Example: python scripts/promote_admin.py 7byt3")
        sys.exit(1)

    username = sys.argv[1]
    asyncio.run(promote_to_admin(username))
