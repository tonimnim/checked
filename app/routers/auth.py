from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from datetime import datetime, timedelta

from app.database import get_db
from app.models.player import Player
from app.models.otp import OTP, OTP_COOLDOWN_MINUTES, MAX_OTP_ATTEMPTS
from app.schemas.player import (
    PlayerCreate, PlayerResponse, Token, PlayerLogin,
    PasswordResetRequest, PasswordResetConfirm, PlayerLoginWithFingerprint
)
from app.schemas.security import DeviceFingerprintData, RiskAnalysisResponse
from app.services.auth import AuthService, get_current_player
from app.services.chess_com import chess_com_service
from app.services.sms import sms_service
from app.services.security import security_service

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(
    player_data: PlayerCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    Register a new player.
    1. Verifies Chess.com username exists
    2. Fetches profile data (avatar, etc.)
    3. Creates local account with password
    """
    # Check if username already registered locally
    result = await db.execute(
        select(Player).where(
            Player.chess_com_username == player_data.chess_com_username.lower()
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This Chess.com username is already registered"
        )

    # Verify Chess.com username exists
    profile = await chess_com_service.get_player_profile(player_data.chess_com_username)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chess.com username not found. Please check the username and try again."
        )

    # Check if phone already registered
    result = await db.execute(
        select(Player).where(Player.phone == player_data.phone)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This phone number is already registered"
        )

    # Extract country code from Chess.com country URL (e.g., "https://api.chess.com/pub/country/KE" -> "KE")
    chess_com_country = None
    if profile.country:
        # Country is a URL like "https://api.chess.com/pub/country/KE"
        chess_com_country = profile.country.rstrip("/").split("/")[-1].upper()

    # Block non-Kenyan accounts - no registration allowed
    if chess_com_country and chess_com_country != "KE":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Registration blocked: Your Chess.com account is registered in {chess_com_country}, not Kenya. Only Kenyan Chess.com accounts are allowed."
        )

    # Create player
    player = Player(
        chess_com_username=profile.username.lower(),
        chess_com_avatar=profile.avatar,
        chess_com_joined=profile.joined,
        chess_com_status=profile.status,
        chess_com_country=chess_com_country,
        password_hash=AuthService.hash_password(player_data.password),
        phone=player_data.phone,
        age=player_data.age,
        gender=player_data.gender,
        county=player_data.county,
        club=player_data.club,
    )

    db.add(player)
    await db.commit()
    await db.refresh(player)

    # Create access token
    access_token = AuthService.create_access_token(data={"sub": player.id})

    return Token(
        access_token=access_token,
        player=PlayerResponse.model_validate(player)
    )


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """
    Login with Chess.com username and password.
    Uses OAuth2PasswordRequestForm for compatibility with OpenAPI/Swagger.
    """
    # Find player by username
    result = await db.execute(
        select(Player).where(
            Player.chess_com_username == form_data.username.lower()
        )
    )
    player = result.scalar_one_or_none()

    if not player or not AuthService.verify_password(form_data.password, player.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not player.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated"
        )

    # Update avatar from Chess.com (in case it changed)
    profile = await chess_com_service.get_player_profile(player.chess_com_username)
    if profile and profile.avatar != player.chess_com_avatar:
        player.chess_com_avatar = profile.avatar
        await db.commit()
        await db.refresh(player)

    # Create access token
    access_token = AuthService.create_access_token(data={"sub": player.id})

    return Token(
        access_token=access_token,
        player=PlayerResponse.model_validate(player)
    )


@router.post("/login/json", response_model=Token)
async def login_json(
    credentials: PlayerLogin,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Login with JSON body (alternative to form-based login).
    Useful for frontend apps. Includes rate limiting.
    """
    # Get client IP
    ip_address = request.client.host if request.client else "unknown"

    # Check rate limit by IP
    is_blocked, remaining, lockout_until = await security_service.check_rate_limit(
        db, ip_address, "ip"
    )

    if is_blocked:
        minutes_left = int((lockout_until - datetime.utcnow()).total_seconds() / 60) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Try again in {minutes_left} minutes."
        )

    # Also check rate limit by username
    is_blocked_user, _, _ = await security_service.check_rate_limit(
        db, credentials.chess_com_username, "username"
    )

    if is_blocked_user:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="This account is temporarily locked due to too many failed attempts."
        )

    result = await db.execute(
        select(Player).where(
            Player.chess_com_username == credentials.chess_com_username.lower()
        )
    )
    player = result.scalar_one_or_none()

    if not player or not AuthService.verify_password(credentials.password, player.password_hash):
        # Record failed attempt
        await security_service.record_failed_login(
            db,
            credentials.chess_com_username,
            ip_address,
            request.headers.get("user-agent", "")
        )

        # Show remaining attempts
        detail = "Incorrect username or password"
        if remaining <= 3:
            detail += f". {remaining - 1} attempts remaining."

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail
        )

    if not player.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated"
        )

    access_token = AuthService.create_access_token(data={"sub": player.id})

    return Token(
        access_token=access_token,
        player=PlayerResponse.model_validate(player)
    )


@router.get("/me", response_model=PlayerResponse)
async def get_me(current_player: Player = Depends(get_current_player)):
    """Get current authenticated player's profile"""
    return PlayerResponse.model_validate(current_player)


@router.get("/verify/{username}")
async def verify_chess_com_username(username: str):
    """
    Check if a Chess.com username exists.
    Useful for frontend validation before registration.
    Returns country code from Chess.com profile.
    """
    profile = await chess_com_service.get_player_profile(username)

    if not profile:
        return {
            "exists": False,
            "message": "Username not found on Chess.com"
        }

    # Extract country code from URL (e.g., "https://api.chess.com/pub/country/KE" -> "KE")
    country_code = None
    if profile.country:
        country_code = profile.country.rstrip("/").split("/")[-1].upper()

    return {
        "exists": True,
        "username": profile.username,
        "avatar": profile.avatar,
        "status": profile.status,
        "country": country_code
    }


@router.post("/request-reset")
async def request_password_reset(
    request: PasswordResetRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Request a password reset OTP.

    The OTP will be sent via SMS (when integrated).
    For testing, the OTP is returned in the response.
    """
    # Check if phone exists
    result = await db.execute(
        select(Player).where(Player.phone == request.phone)
    )
    player = result.scalar_one_or_none()

    if not player:
        # Don't reveal if phone exists or not (security)
        # But return same response format
        return {
            "message": "If this phone is registered, you will receive an OTP",
            "expires_in_minutes": 10
        }

    # Check for recent OTP requests (rate limiting)
    cooldown_time = datetime.utcnow() - timedelta(minutes=OTP_COOLDOWN_MINUTES)
    result = await db.execute(
        select(OTP).where(
            OTP.phone == request.phone,
            OTP.purpose == "password_reset",
            OTP.created_at > cooldown_time
        ).order_by(OTP.created_at.desc())
    )
    recent_otp = result.scalar_one_or_none()

    if recent_otp:
        wait_seconds = int((recent_otp.created_at + timedelta(minutes=OTP_COOLDOWN_MINUTES) - datetime.utcnow()).total_seconds())
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {wait_seconds} seconds before requesting another OTP"
        )

    # Invalidate any existing unused OTPs
    result = await db.execute(
        select(OTP).where(
            OTP.phone == request.phone,
            OTP.purpose == "password_reset",
            OTP.is_used == False
        )
    )
    old_otps = result.scalars().all()
    for old_otp in old_otps:
        old_otp.is_used = True

    # Create new OTP
    otp_instance, raw_otp = OTP.create_for_phone(request.phone, "password_reset")
    db.add(otp_instance)
    await db.commit()

    # Send SMS via Africa's Talking
    sms_result = await sms_service.send_otp(request.phone, raw_otp)

    response = {
        "message": "If this phone is registered, you will receive an OTP",
        "expires_in_minutes": 10
    }

    # Include debug OTP only if SMS not configured (for development)
    if not sms_service.is_configured():
        response["_debug_otp"] = raw_otp
        response["_debug_note"] = "SMS not configured. Set AT_USERNAME and AT_API_KEY in .env"

    return response


@router.post("/reset-password")
async def reset_password(
    request: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db)
):
    """
    Reset password using OTP.

    Requires:
    - phone: The phone number
    - otp: 6-digit OTP received via SMS
    - new_password: New password (min 6 characters)
    """
    # Find the player
    result = await db.execute(
        select(Player).where(Player.phone == request.phone)
    )
    player = result.scalar_one_or_none()

    if not player:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone number or OTP"
        )

    # Find valid OTP
    result = await db.execute(
        select(OTP).where(
            OTP.phone == request.phone,
            OTP.purpose == "password_reset",
            OTP.is_used == False,
            OTP.expires_at > datetime.utcnow()
        ).order_by(OTP.created_at.desc())
    )
    otp_record = result.scalar_one_or_none()

    if not otp_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP. Please request a new one."
        )

    # Check attempts
    if otp_record.attempts >= MAX_OTP_ATTEMPTS:
        otp_record.is_used = True
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many failed attempts. Please request a new OTP."
        )

    # Verify OTP
    if not otp_record.verify_otp(request.otp):
        otp_record.attempts += 1
        await db.commit()
        remaining = MAX_OTP_ATTEMPTS - otp_record.attempts
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid OTP. {remaining} attempts remaining."
        )

    # OTP is valid - update password
    player.password_hash = AuthService.hash_password(request.new_password)
    otp_record.is_used = True
    otp_record.used_at = datetime.utcnow()

    await db.commit()

    return {
        "message": "Password reset successful. You can now login with your new password."
    }


@router.get("/push/vapid-key")
async def get_vapid_public_key():
    """
    Get the VAPID public key for Web Push subscription.

    Frontend uses this to subscribe to push notifications:

    ```javascript
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: vapidPublicKey  // from this endpoint
    });
    // Then POST subscription to /api/auth/push/subscribe
    ```
    """
    from app.services.push import push_service

    public_key = push_service.get_public_key()
    if not public_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Push notifications not configured"
        )

    return {"vapid_public_key": public_key}


@router.post("/push/subscribe")
async def subscribe_to_push(
    subscription: dict,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Register Web Push subscription for the current player.

    Args:
        subscription: Push subscription object from browser
            {
                "endpoint": "https://...",
                "keys": {
                    "p256dh": "...",
                    "auth": "..."
                }
            }
    """
    import json

    # Validate subscription format
    if not subscription.get("endpoint") or not subscription.get("keys"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid subscription format. Need endpoint and keys."
        )

    current_player.push_subscription = json.dumps(subscription)
    current_player.push_enabled = True
    await db.commit()

    return {"message": "Push subscription registered successfully"}


@router.post("/push/unsubscribe")
async def unsubscribe_from_push(
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Unsubscribe from push notifications"""
    current_player.push_subscription = None
    current_player.push_enabled = False
    await db.commit()

    return {"message": "Push notifications disabled"}


@router.post("/push/toggle")
async def toggle_push_notifications(
    enabled: bool,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """Enable or disable push notifications without removing subscription"""
    if enabled and not current_player.push_subscription:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No push subscription. Subscribe first via /push/subscribe"
        )

    current_player.push_enabled = enabled
    await db.commit()

    return {"message": f"Push notifications {'enabled' if enabled else 'disabled'}"}


# ============ Security / Fingerprint Tracking ============

def get_client_ip(request: Request) -> str:
    """Extract client IP address from request"""
    # Check for forwarded headers (when behind proxy/load balancer)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP in the list (original client)
        return forwarded_for.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    # Fall back to direct connection IP
    return request.client.host if request.client else "unknown"


@router.post("/fingerprint")
async def record_fingerprint(
    fingerprint: DeviceFingerprintData,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Record device fingerprint after login.

    Call this endpoint immediately after successful login to track
    the device for security monitoring. This helps detect:
    - Account sharing (same device used by multiple accounts)
    - Suspicious login patterns (new device during tournament)
    - Unusual location changes

    Frontend should collect fingerprint data using a library like
    FingerprintJS or custom collection, then POST here.
    """
    ip_address = get_client_ip(request)

    login_record, risk_score = await security_service.record_login(
        db=db,
        player=current_player,
        fingerprint_data=fingerprint,
        ip_address=ip_address,
        session_type="login",
    )

    # Determine risk level for response
    risk_level = "low"
    if risk_score >= 80:
        risk_level = "critical"
    elif risk_score >= 60:
        risk_level = "high"
    elif risk_score >= 40:
        risk_level = "medium"

    return {
        "message": "Fingerprint recorded",
        "is_new_device": login_record.is_new_device,
        "risk_score": risk_score,
        "risk_level": risk_level,
    }


@router.post("/fingerprint/register")
async def record_registration_fingerprint(
    fingerprint: DeviceFingerprintData,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Record device fingerprint for new registration.

    Call this immediately after registration completes.
    This establishes the "baseline" device for the account.
    """
    ip_address = get_client_ip(request)

    login_record, risk_score = await security_service.record_login(
        db=db,
        player=current_player,
        fingerprint_data=fingerprint,
        ip_address=ip_address,
        session_type="register",
    )

    return {
        "message": "Registration fingerprint recorded",
        "device_trusted": True,
    }


@router.post("/login/secure", response_model=Token)
async def login_with_fingerprint(
    credentials: PlayerLoginWithFingerprint,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Login with optional device fingerprint tracking.

    This is the recommended login endpoint that includes
    security tracking in a single request.
    """
    # Find player by username
    result = await db.execute(
        select(Player).where(
            Player.chess_com_username == credentials.chess_com_username.lower()
        )
    )
    player = result.scalar_one_or_none()

    if not player or not AuthService.verify_password(credentials.password, player.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )

    if not player.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated"
        )

    # Track login if fingerprint provided
    risk_score = 0.0
    if credentials.fingerprint:
        try:
            fingerprint_data = DeviceFingerprintData(**credentials.fingerprint)
            ip_address = get_client_ip(request)

            login_record, risk_score = await security_service.record_login(
                db=db,
                player=player,
                fingerprint_data=fingerprint_data,
                ip_address=ip_address,
                session_type="login",
            )
        except Exception:
            # Don't fail login if fingerprint tracking fails
            pass

    # Check if player is restricted due to security concerns
    if player.security_risk_level == "restricted":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account restricted due to security concerns. Please contact support."
        )

    # Create access token
    access_token = AuthService.create_access_token(data={"sub": player.id})

    return Token(
        access_token=access_token,
        player=PlayerResponse.model_validate(player)
    )


@router.get("/security/status")
async def get_security_status(
    db: AsyncSession = Depends(get_db),
    current_player: Player = Depends(get_current_player)
):
    """
    Get current player's security status.

    Returns:
    - Account risk level
    - Whether account is flagged
    - Number of known devices
    - Recent login count
    """
    stats = await security_service.get_player_security_stats(db, current_player.id)

    return {
        "risk_level": current_player.security_risk_level,
        "is_flagged": current_player.is_flagged,
        "device_count": stats["device_count"],
        "total_logins": stats["total_logins"],
        "unique_ips": stats["unique_ips"],
        "open_flags": stats["open_flags"],
        "recent_avg_risk_score": stats["avg_risk_score"],
    }
