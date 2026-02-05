from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct, and_, desc
from datetime import datetime, timedelta
from typing import Optional, List
import json

from app.database import get_db
from app.models.player import Player
from app.models.security import (
    LoginHistory,
    DeviceFingerprint,
    SecurityFlag,
    SharedDeviceAlert,
)
from app.schemas.security import (
    SecurityOverview,
    SecurityFlagResponse,
    SecurityFlagWithPlayer,
    SecurityFlagUpdate,
    LoginHistoryResponse,
    LoginHistoryWithPlayer,
    DeviceFingerprintResponse,
    PlayerSecurityProfile,
    PlayerSecurityListItem,
    SharedDeviceAlertResponse,
    SharedDeviceAlertWithPlayers,
)
from app.services.auth import get_current_admin

router = APIRouter(prefix="/api/admin/security", tags=["Admin Security"])


# ============ Dashboard Overview ============

@router.get("/overview", response_model=SecurityOverview)
async def get_security_overview(
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Get security dashboard overview statistics"""
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)

    # Logins today
    result = await db.execute(
        select(func.count(LoginHistory.id)).where(
            LoginHistory.created_at >= today_start
        )
    )
    total_logins_today = result.scalar() or 0

    # Logins this week
    result = await db.execute(
        select(func.count(LoginHistory.id)).where(
            LoginHistory.created_at >= week_start
        )
    )
    total_logins_week = result.scalar() or 0

    # New devices today
    result = await db.execute(
        select(func.count(LoginHistory.id)).where(
            LoginHistory.created_at >= today_start,
            LoginHistory.is_new_device == True,
        )
    )
    new_devices_today = result.scalar() or 0

    # New devices this week
    result = await db.execute(
        select(func.count(LoginHistory.id)).where(
            LoginHistory.created_at >= week_start,
            LoginHistory.is_new_device == True,
        )
    )
    new_devices_week = result.scalar() or 0

    # Open flags count
    result = await db.execute(
        select(func.count(SecurityFlag.id)).where(
            SecurityFlag.status == "open"
        )
    )
    open_flags_count = result.scalar() or 0

    # High severity flags
    result = await db.execute(
        select(func.count(SecurityFlag.id)).where(
            SecurityFlag.status == "open",
            SecurityFlag.severity.in_(["high", "critical"]),
        )
    )
    high_severity_flags = result.scalar() or 0

    # Flagged players count
    result = await db.execute(
        select(func.count(Player.id)).where(Player.is_flagged == True)
    )
    flagged_players_count = result.scalar() or 0

    # Shared device alerts (pending)
    result = await db.execute(
        select(func.count(SharedDeviceAlert.id)).where(
            SharedDeviceAlert.status == "pending"
        )
    )
    shared_device_alerts = result.scalar() or 0

    # Recent flags (last 10)
    result = await db.execute(
        select(SecurityFlag, Player).join(
            Player, SecurityFlag.player_id == Player.id
        ).where(
            SecurityFlag.status == "open"
        ).order_by(desc(SecurityFlag.created_at)).limit(10)
    )
    recent_flags = []
    for flag, player in result.fetchall():
        flag_dict = SecurityFlagWithPlayer.model_validate(flag).model_dump()
        flag_dict["player_username"] = player.chess_com_username
        flag_dict["player_avatar"] = player.chess_com_avatar
        recent_flags.append(SecurityFlagWithPlayer(**flag_dict))

    # Recent suspicious logins (risk_score >= 40)
    result = await db.execute(
        select(LoginHistory, Player).join(
            Player, LoginHistory.player_id == Player.id
        ).where(
            LoginHistory.risk_score >= 40
        ).order_by(desc(LoginHistory.created_at)).limit(10)
    )
    recent_suspicious_logins = []
    for login, player in result.fetchall():
        login_dict = LoginHistoryWithPlayer.model_validate(login).model_dump()
        login_dict["player_username"] = player.chess_com_username
        login_dict["player_avatar"] = player.chess_com_avatar
        recent_suspicious_logins.append(LoginHistoryWithPlayer(**login_dict))

    return SecurityOverview(
        total_logins_today=total_logins_today,
        total_logins_week=total_logins_week,
        new_devices_today=new_devices_today,
        new_devices_week=new_devices_week,
        open_flags_count=open_flags_count,
        high_severity_flags=high_severity_flags,
        flagged_players_count=flagged_players_count,
        shared_device_alerts=shared_device_alerts,
        recent_flags=recent_flags,
        recent_suspicious_logins=recent_suspicious_logins,
    )


# ============ Players Security List ============

@router.get("/players", response_model=List[PlayerSecurityListItem])
async def get_players_security_list(
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
    search: Optional[str] = Query(None, description="Search by username"),
    flagged_only: bool = Query(False, description="Only show flagged players"),
    risk_level: Optional[str] = Query(None, description="Filter by risk level"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Get list of players with security info"""
    query = select(Player)

    if search:
        query = query.where(Player.chess_com_username.ilike(f"%{search}%"))

    if flagged_only:
        query = query.where(Player.is_flagged == True)

    if risk_level:
        query = query.where(Player.security_risk_level == risk_level)

    query = query.order_by(
        desc(Player.is_flagged),
        desc(Player.last_login_at)
    ).offset(offset).limit(limit)

    result = await db.execute(query)
    players = result.scalars().all()

    # Get additional stats for each player
    player_list = []
    for player in players:
        # Count open flags
        result = await db.execute(
            select(func.count(SecurityFlag.id)).where(
                SecurityFlag.player_id == player.id,
                SecurityFlag.status == "open",
            )
        )
        open_flags = result.scalar() or 0

        # Count devices
        result = await db.execute(
            select(func.count(DeviceFingerprint.id)).where(
                DeviceFingerprint.player_id == player.id
            )
        )
        device_count = result.scalar() or 0

        # Get recent risk score
        result = await db.execute(
            select(LoginHistory.risk_score).where(
                LoginHistory.player_id == player.id
            ).order_by(desc(LoginHistory.created_at)).limit(1)
        )
        recent_risk = result.scalar() or 0

        player_list.append(PlayerSecurityListItem(
            player_id=player.id,
            player_username=player.chess_com_username,
            player_avatar=player.chess_com_avatar,
            security_risk_level=player.security_risk_level,
            is_flagged=player.is_flagged,
            open_flags_count=open_flags,
            device_count=device_count,
            last_login_at=player.last_login_at,
            recent_risk_score=recent_risk,
        ))

    return player_list


# ============ Player Security Profile ============

@router.get("/players/{player_id}", response_model=PlayerSecurityProfile)
async def get_player_security_profile(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Get detailed security profile for a player"""
    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Player not found"
        )

    # Get devices
    result = await db.execute(
        select(DeviceFingerprint).where(
            DeviceFingerprint.player_id == player_id
        ).order_by(desc(DeviceFingerprint.last_seen_at))
    )
    devices = [DeviceFingerprintResponse.model_validate(d) for d in result.scalars().all()]
    primary_device = next((d for d in devices if d.is_primary), None)

    # Get login stats
    result = await db.execute(
        select(func.count(LoginHistory.id)).where(
            LoginHistory.player_id == player_id
        )
    )
    total_logins = result.scalar() or 0

    result = await db.execute(
        select(func.count(distinct(LoginHistory.ip_address))).where(
            LoginHistory.player_id == player_id
        )
    )
    unique_ips = result.scalar() or 0

    # Get unique locations (cities)
    result = await db.execute(
        select(distinct(LoginHistory.city)).where(
            LoginHistory.player_id == player_id,
            LoginHistory.city.isnot(None),
        )
    )
    unique_locations = [row[0] for row in result.fetchall() if row[0]]

    # Get security flags
    result = await db.execute(
        select(SecurityFlag).where(
            SecurityFlag.player_id == player_id
        ).order_by(desc(SecurityFlag.created_at))
    )
    flags = [SecurityFlagResponse.model_validate(f) for f in result.scalars().all()]
    open_flags = len([f for f in flags if f.status == "open"])

    # Calculate risk score (average of recent logins)
    result = await db.execute(
        select(LoginHistory.risk_score).where(
            LoginHistory.player_id == player_id
        ).order_by(desc(LoginHistory.created_at)).limit(10)
    )
    recent_scores = [row[0] for row in result.fetchall()]
    risk_score = sum(recent_scores) / len(recent_scores) if recent_scores else 0

    # Build risk factors list
    risk_factors = []
    if len(devices) > 3:
        risk_factors.append(f"Multiple devices ({len(devices)})")
    if unique_ips > 10:
        risk_factors.append(f"Many unique IPs ({unique_ips})")
    if open_flags > 0:
        risk_factors.append(f"Open security flags ({open_flags})")

    return PlayerSecurityProfile(
        player_id=player.id,
        player_username=player.chess_com_username,
        player_avatar=player.chess_com_avatar,
        registration_date=player.created_at,
        registration_ip=player.registration_ip,
        registration_fingerprint=player.registration_fingerprint,
        security_risk_level=player.security_risk_level,
        is_flagged=player.is_flagged,
        total_devices=len(devices),
        primary_device=primary_device,
        devices=devices,
        total_logins=total_logins,
        last_login=player.last_login_at,
        unique_ips=unique_ips,
        unique_locations=unique_locations,
        open_flags=open_flags,
        total_flags=len(flags),
        flags=flags,
        risk_score=risk_score,
        risk_factors=risk_factors,
    )


# ============ Login History ============

@router.get("/players/{player_id}/logins", response_model=List[LoginHistoryResponse])
async def get_player_login_history(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get login history for a specific player"""
    result = await db.execute(
        select(LoginHistory).where(
            LoginHistory.player_id == player_id
        ).order_by(desc(LoginHistory.created_at)).offset(offset).limit(limit)
    )
    logins = result.scalars().all()
    return [LoginHistoryResponse.model_validate(l) for l in logins]


@router.get("/logins/suspicious", response_model=List[LoginHistoryWithPlayer])
async def get_suspicious_logins(
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
    min_risk_score: float = Query(40, ge=0, le=100),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=200),
):
    """Get suspicious logins across all players"""
    since = datetime.utcnow() - timedelta(days=days)

    result = await db.execute(
        select(LoginHistory, Player).join(
            Player, LoginHistory.player_id == Player.id
        ).where(
            LoginHistory.risk_score >= min_risk_score,
            LoginHistory.created_at >= since,
        ).order_by(desc(LoginHistory.risk_score), desc(LoginHistory.created_at)).limit(limit)
    )

    logins = []
    for login, player in result.fetchall():
        login_dict = LoginHistoryWithPlayer.model_validate(login).model_dump()
        login_dict["player_username"] = player.chess_com_username
        login_dict["player_avatar"] = player.chess_com_avatar
        logins.append(LoginHistoryWithPlayer(**login_dict))

    return logins


# ============ Security Flags ============

@router.get("/flags", response_model=List[SecurityFlagWithPlayer])
async def get_security_flags(
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
    status_filter: Optional[str] = Query(None, alias="status"),
    severity: Optional[str] = Query(None),
    flag_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get all security flags"""
    query = select(SecurityFlag, Player).join(
        Player, SecurityFlag.player_id == Player.id
    )

    if status_filter:
        query = query.where(SecurityFlag.status == status_filter)
    if severity:
        query = query.where(SecurityFlag.severity == severity)
    if flag_type:
        query = query.where(SecurityFlag.flag_type == flag_type)

    query = query.order_by(
        desc(SecurityFlag.severity == "critical"),
        desc(SecurityFlag.severity == "high"),
        desc(SecurityFlag.created_at)
    ).offset(offset).limit(limit)

    result = await db.execute(query)

    flags = []
    for flag, player in result.fetchall():
        flag_dict = SecurityFlagWithPlayer.model_validate(flag).model_dump()
        flag_dict["player_username"] = player.chess_com_username
        flag_dict["player_avatar"] = player.chess_com_avatar
        flags.append(SecurityFlagWithPlayer(**flag_dict))

    return flags


@router.patch("/flags/{flag_id}")
async def update_security_flag(
    flag_id: str,
    update: SecurityFlagUpdate,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Update a security flag (resolve, dismiss, etc.)"""
    result = await db.execute(
        select(SecurityFlag).where(SecurityFlag.id == flag_id)
    )
    flag = result.scalar_one_or_none()

    if not flag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Security flag not found"
        )

    if update.status:
        flag.status = update.status
        if update.status in ["resolved", "dismissed"]:
            flag.resolved_by = admin.id
            flag.resolved_at = datetime.utcnow()

    if update.resolution_notes:
        flag.resolution_notes = update.resolution_notes

    # If resolving all flags for player, update player flagged status
    if update.status in ["resolved", "dismissed"]:
        result = await db.execute(
            select(func.count(SecurityFlag.id)).where(
                SecurityFlag.player_id == flag.player_id,
                SecurityFlag.status == "open",
                SecurityFlag.id != flag_id,
            )
        )
        remaining_flags = result.scalar() or 0

        if remaining_flags == 0:
            result = await db.execute(
                select(Player).where(Player.id == flag.player_id)
            )
            player = result.scalar_one_or_none()
            if player:
                player.is_flagged = False
                player.security_risk_level = "normal"

    await db.commit()
    return {"message": "Flag updated successfully"}


# ============ Shared Device Alerts ============

@router.get("/shared-devices", response_model=List[SharedDeviceAlertWithPlayers])
async def get_shared_device_alerts(
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
):
    """Get shared device alerts"""
    query = select(SharedDeviceAlert)

    if status_filter:
        query = query.where(SharedDeviceAlert.status == status_filter)

    query = query.order_by(desc(SharedDeviceAlert.created_at)).limit(limit)

    result = await db.execute(query)
    alerts = result.scalars().all()

    response_list = []
    for alert in alerts:
        player_ids = json.loads(alert.player_ids)

        # Get player details
        result = await db.execute(
            select(Player).where(Player.id.in_(player_ids))
        )
        players = result.scalars().all()

        player_details = [
            {
                "id": p.id,
                "username": p.chess_com_username,
                "avatar": p.chess_com_avatar,
            }
            for p in players
        ]

        response = SharedDeviceAlertWithPlayers(
            id=alert.id,
            fingerprint_hash=alert.fingerprint_hash,
            player_ids=alert.player_ids,
            player_count=alert.player_count,
            status=alert.status,
            reviewed_by=alert.reviewed_by,
            reviewed_at=alert.reviewed_at,
            notes=alert.notes,
            created_at=alert.created_at,
            players=player_details,
        )
        response_list.append(response)

    return response_list


@router.patch("/shared-devices/{alert_id}")
async def update_shared_device_alert(
    alert_id: str,
    status: str = Query(..., description="New status: reviewed, legitimate, suspicious"),
    notes: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Update shared device alert status"""
    result = await db.execute(
        select(SharedDeviceAlert).where(SharedDeviceAlert.id == alert_id)
    )
    alert = result.scalar_one_or_none()

    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alert not found"
        )

    alert.status = status
    alert.reviewed_by = admin.id
    alert.reviewed_at = datetime.utcnow()
    if notes:
        alert.notes = notes

    await db.commit()
    return {"message": "Alert updated successfully"}


# ============ Player Actions ============

@router.post("/players/{player_id}/clear-flags")
async def clear_player_flags(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Clear all open flags for a player"""
    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Player not found"
        )

    # Update all open flags to dismissed
    result = await db.execute(
        select(SecurityFlag).where(
            SecurityFlag.player_id == player_id,
            SecurityFlag.status == "open",
        )
    )
    flags = result.scalars().all()

    for flag in flags:
        flag.status = "dismissed"
        flag.resolved_by = admin.id
        flag.resolved_at = datetime.utcnow()
        flag.resolution_notes = "Cleared by admin"

    player.is_flagged = False
    player.security_risk_level = "normal"

    await db.commit()
    return {"message": f"Cleared {len(flags)} flags for player"}


@router.post("/players/{player_id}/set-risk-level")
async def set_player_risk_level(
    player_id: str,
    risk_level: str = Query(..., description="Risk level: normal, elevated, high, restricted"),
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Manually set player's security risk level"""
    if risk_level not in ["normal", "elevated", "high", "restricted"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid risk level"
        )

    result = await db.execute(select(Player).where(Player.id == player_id))
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Player not found"
        )

    player.security_risk_level = risk_level
    await db.commit()

    return {"message": f"Risk level set to {risk_level}"}


@router.post("/players/{player_id}/trust-device/{device_id}")
async def trust_player_device(
    player_id: str,
    device_id: str,
    db: AsyncSession = Depends(get_db),
    admin: Player = Depends(get_current_admin),
):
    """Mark a device as trusted for a player"""
    result = await db.execute(
        select(DeviceFingerprint).where(
            DeviceFingerprint.id == device_id,
            DeviceFingerprint.player_id == player_id,
        )
    )
    device = result.scalar_one_or_none()

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )

    device.is_trusted = True
    device.trust_reason = "admin_approved"
    await db.commit()

    return {"message": "Device marked as trusted"}
