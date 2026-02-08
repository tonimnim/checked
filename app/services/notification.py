import json
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.notification import Notification


async def create_notification(
    db: AsyncSession,
    player_id: str,
    type: str,
    title: str,
    body: str,
    data: dict | None = None,
) -> Notification:
    """Create an in-app notification for a player."""
    notification = Notification(
        player_id=player_id,
        type=type,
        title=title,
        body=body,
        data=json.dumps(data or {}),
    )
    db.add(notification)
    # Don't commit - let the caller's existing commit handle it
    return notification
