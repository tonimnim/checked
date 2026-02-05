"""
Script to make a player an admin by their Chess.com username.
Usage: python scripts/make_admin.py <chess_com_username>
"""
import asyncio
import sys
from sqlalchemy import select

# Add parent directory to path
sys.path.insert(0, ".")

from app.database import async_session_maker, init_db
from app.models.player import Player


async def make_admin(username: str):
    await init_db()

    async with async_session_maker() as db:
        result = await db.execute(
            select(Player).where(Player.chess_com_username == username.lower())
        )
        player = result.scalar_one_or_none()

        if not player:
            print(f"Player with username '{username}' not found.")
            return

        player.is_admin = True
        await db.commit()
        print(f"Success! {player.full_name} ({player.chess_com_username}) is now an admin.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/make_admin.py <chess_com_username>")
        sys.exit(1)

    username = sys.argv[1]
    asyncio.run(make_admin(username))
