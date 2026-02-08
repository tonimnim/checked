from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, and_
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

from app.database import get_db
from app.models.player import Player
from app.models.notification import Notification
from app.services.auth import get_current_player

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


class NotificationResponse(BaseModel):
    id: str
    player_id: str
    type: str
    title: str
    body: str
    data: str  # JSON string
    is_read: bool
    created_at: datetime


class UnreadCountResponse(BaseModel):
    count: int


@router.get("", response_model=List[NotificationResponse])
async def list_notifications(
    skip: int = 0,
    limit: int = 30,
    unread_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    """List notifications for current player with pagination."""
    query = select(Notification).where(Notification.player_id == current_player.id)
    if unread_only:
        query = query.where(Notification.is_read == False)
    query = query.order_by(Notification.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    """Get count of unread notifications (for bell badge)."""
    result = await db.execute(
        select(func.count(Notification.id)).where(
            and_(
                Notification.player_id == current_player.id,
                Notification.is_read == False,
            )
        )
    )
    count = result.scalar() or 0
    return UnreadCountResponse(count=count)


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
async def mark_read(
    notification_id: str,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    """Mark a single notification as read."""
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.player_id == current_player.id,
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    notification.is_read = True
    await db.commit()
    await db.refresh(notification)
    return notification


@router.patch("/read-all")
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player),
):
    """Mark all notifications as read."""
    await db.execute(
        update(Notification)
        .where(
            and_(
                Notification.player_id == current_player.id,
                Notification.is_read == False,
            )
        )
        .values(is_read=True)
    )
    await db.commit()
    return {"message": "All notifications marked as read"}
