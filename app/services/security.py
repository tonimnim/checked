import json
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, distinct
from user_agents import parse as parse_user_agent

from app.models.player import Player
from app.models.security import (
    LoginHistory,
    DeviceFingerprint,
    SecurityFlag,
    SharedDeviceAlert,
)
from app.schemas.security import DeviceFingerprintData, RiskAnalysisResponse


class SecurityService:
    """Service for anti-cheat and security monitoring"""

    # Rate limiting settings
    MAX_FAILED_ATTEMPTS = 5  # Max failed attempts before lockout
    LOCKOUT_DURATION_MINUTES = 15  # Lockout duration
    FAILED_ATTEMPT_WINDOW_MINUTES = 15  # Window to count failed attempts

    # Risk scoring weights
    RISK_NEW_DEVICE = 25
    RISK_NEW_LOCATION = 15
    RISK_UNUSUAL_TIME = 10
    RISK_RAPID_DEVICE_SWITCH = 30
    RISK_SHARED_DEVICE = 40
    RISK_TOURNAMENT_PROXIMITY = 20

    @staticmethod
    def generate_fingerprint_hash(data: DeviceFingerprintData) -> str:
        """Generate a stable hash from fingerprint components"""
        stable_parts = [
            data.platform or "",
            data.screen_resolution or "",
            data.timezone or "",
            data.canvas_hash or "",
            data.webgl_renderer or "",
        ]
        fingerprint_string = "|".join(str(p) for p in stable_parts)
        return hashlib.sha256(fingerprint_string.encode()).hexdigest()

    @staticmethod
    def parse_user_agent_details(user_agent: str) -> Tuple[str, str, str]:
        """Parse user agent to extract platform, browser, and device name"""
        try:
            ua = parse_user_agent(user_agent)
            platform = ua.os.family  # Windows, iOS, Android, etc.
            browser = ua.browser.family  # Chrome, Safari, Firefox, etc.
            device_name = f"{browser} on {platform}"
            return platform, browser, device_name
        except Exception:
            return "Unknown", "Unknown", "Unknown Device"

    async def record_login(
        self,
        db: AsyncSession,
        player: Player,
        fingerprint_data: DeviceFingerprintData,
        ip_address: str,
        session_type: str = "login",
        login_successful: bool = True,
    ) -> Tuple[LoginHistory, float]:
        """
        Record a login attempt and calculate risk score.
        Returns the login record and risk score.
        """
        fingerprint_hash = self.generate_fingerprint_hash(fingerprint_data)
        platform, browser, device_name = self.parse_user_agent_details(
            fingerprint_data.user_agent
        )

        # Check if this is a known device
        is_new_device = await self._is_new_device(db, player.id, fingerprint_hash)

        # Check if this is a new location (based on IP)
        is_new_location = await self._is_new_location(db, player.id, ip_address)

        # Calculate risk score
        risk_score, risk_factors = await self._calculate_risk_score(
            db, player, fingerprint_hash, ip_address, is_new_device, is_new_location
        )

        # Create login record
        login = LoginHistory(
            player_id=player.id,
            fingerprint_hash=fingerprint_hash,
            user_agent=fingerprint_data.user_agent,
            platform=platform,
            browser=browser,
            screen_resolution=fingerprint_data.screen_resolution,
            timezone=fingerprint_data.timezone,
            language=fingerprint_data.language,
            ip_address=ip_address,
            login_successful=login_successful,
            session_type=session_type,
            is_new_device=is_new_device,
            is_new_location=is_new_location,
            risk_score=risk_score,
        )

        db.add(login)

        # Update or create device record
        await self._update_device_record(
            db, player.id, fingerprint_hash, platform, browser, device_name,
            is_primary=(session_type == "register")
        )

        # Update player's last login
        player.last_login_at = datetime.utcnow()

        # If this is registration, store registration info
        if session_type == "register":
            player.registration_ip = ip_address
            player.registration_fingerprint = fingerprint_hash

        # Check for shared device and create alert if needed
        await self._check_shared_device(db, fingerprint_hash, player.id)

        # Create security flags for high-risk logins
        if risk_score >= 50:
            await self._create_risk_flag(
                db, player, login, risk_score, risk_factors
            )

        await db.commit()
        await db.refresh(login)

        return login, risk_score

    async def _is_new_device(
        self, db: AsyncSession, player_id: str, fingerprint_hash: str
    ) -> bool:
        """Check if this fingerprint is new for the player"""
        result = await db.execute(
            select(DeviceFingerprint).where(
                DeviceFingerprint.player_id == player_id,
                DeviceFingerprint.fingerprint_hash == fingerprint_hash,
            )
        )
        return result.scalar_one_or_none() is None

    async def _is_new_location(
        self, db: AsyncSession, player_id: str, ip_address: str
    ) -> bool:
        """Check if this IP is new for the player"""
        result = await db.execute(
            select(LoginHistory).where(
                LoginHistory.player_id == player_id,
                LoginHistory.ip_address == ip_address,
            ).limit(1)
        )
        return result.scalar_one_or_none() is None

    async def _calculate_risk_score(
        self,
        db: AsyncSession,
        player: Player,
        fingerprint_hash: str,
        ip_address: str,
        is_new_device: bool,
        is_new_location: bool,
    ) -> Tuple[float, List[str]]:
        """Calculate risk score based on various factors"""
        risk_score = 0.0
        risk_factors = []

        # Factor 1: New device
        if is_new_device:
            risk_score += self.RISK_NEW_DEVICE
            risk_factors.append("new_device")

        # Factor 2: New location
        if is_new_location:
            risk_score += self.RISK_NEW_LOCATION
            risk_factors.append("new_location")

        # Factor 3: Rapid device switching (multiple devices in last 24 hours)
        recent_devices = await self._get_recent_device_count(db, player.id, hours=24)
        if recent_devices >= 3:
            risk_score += self.RISK_RAPID_DEVICE_SWITCH
            risk_factors.append("rapid_device_switch")

        # Factor 4: Shared device (fingerprint used by other accounts)
        shared_players = await self._get_shared_device_players(db, fingerprint_hash, player.id)
        if shared_players:
            risk_score += self.RISK_SHARED_DEVICE
            risk_factors.append(f"shared_device_with_{len(shared_players)}_accounts")

        # Factor 5: Unusual login time (configurable - using 2am-5am as unusual)
        current_hour = datetime.utcnow().hour
        if 2 <= current_hour <= 5:
            risk_score += self.RISK_UNUSUAL_TIME
            risk_factors.append("unusual_time")

        # Factor 6: Active tournament proximity (new device during active tournament)
        if is_new_device:
            has_active_tournament = await self._has_active_tournament(db, player.id)
            if has_active_tournament:
                risk_score += self.RISK_TOURNAMENT_PROXIMITY
                risk_factors.append("new_device_during_tournament")

        return min(risk_score, 100.0), risk_factors

    async def _get_recent_device_count(
        self, db: AsyncSession, player_id: str, hours: int = 24
    ) -> int:
        """Get count of unique devices used in the last N hours"""
        since = datetime.utcnow() - timedelta(hours=hours)
        result = await db.execute(
            select(func.count(distinct(LoginHistory.fingerprint_hash))).where(
                LoginHistory.player_id == player_id,
                LoginHistory.created_at >= since,
            )
        )
        return result.scalar() or 0

    async def _get_shared_device_players(
        self, db: AsyncSession, fingerprint_hash: str, exclude_player_id: str
    ) -> List[str]:
        """Get list of other player IDs who have used this device"""
        result = await db.execute(
            select(distinct(LoginHistory.player_id)).where(
                LoginHistory.fingerprint_hash == fingerprint_hash,
                LoginHistory.player_id != exclude_player_id,
            )
        )
        return [row[0] for row in result.fetchall()]

    async def _has_active_tournament(self, db: AsyncSession, player_id: str) -> bool:
        """Check if player is in an active tournament"""
        from app.models.tournament import TournamentPlayer, Tournament

        result = await db.execute(
            select(TournamentPlayer).join(Tournament).where(
                TournamentPlayer.player_id == player_id,
                Tournament.status == "active",
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _update_device_record(
        self,
        db: AsyncSession,
        player_id: str,
        fingerprint_hash: str,
        platform: str,
        browser: str,
        device_name: str,
        is_primary: bool = False,
    ):
        """Update or create device fingerprint record"""
        result = await db.execute(
            select(DeviceFingerprint).where(
                DeviceFingerprint.player_id == player_id,
                DeviceFingerprint.fingerprint_hash == fingerprint_hash,
            )
        )
        device = result.scalar_one_or_none()

        if device:
            device.login_count += 1
            device.last_seen_at = datetime.utcnow()
        else:
            device = DeviceFingerprint(
                player_id=player_id,
                fingerprint_hash=fingerprint_hash,
                device_name=device_name,
                platform=platform,
                browser=browser,
                is_primary=is_primary,
                trust_reason="registration" if is_primary else "first_use",
            )
            db.add(device)

    async def _check_shared_device(
        self, db: AsyncSession, fingerprint_hash: str, current_player_id: str
    ):
        """Check for shared device usage and create alert if needed"""
        other_players = await self._get_shared_device_players(
            db, fingerprint_hash, current_player_id
        )

        if not other_players:
            return

        # Include current player in the list
        all_players = [current_player_id] + other_players

        # Check if alert already exists for this fingerprint
        result = await db.execute(
            select(SharedDeviceAlert).where(
                SharedDeviceAlert.fingerprint_hash == fingerprint_hash,
                SharedDeviceAlert.status.in_(["pending", "reviewed"]),
            )
        )
        existing_alert = result.scalar_one_or_none()

        if existing_alert:
            # Update existing alert with new player count
            existing_player_ids = set(json.loads(existing_alert.player_ids))
            existing_player_ids.update(all_players)
            existing_alert.player_ids = json.dumps(list(existing_player_ids))
            existing_alert.player_count = len(existing_player_ids)
        else:
            # Create new alert
            alert = SharedDeviceAlert(
                fingerprint_hash=fingerprint_hash,
                player_ids=json.dumps(all_players),
                player_count=len(all_players),
            )
            db.add(alert)

    async def _create_risk_flag(
        self,
        db: AsyncSession,
        player: Player,
        login: LoginHistory,
        risk_score: float,
        risk_factors: List[str],
    ):
        """Create a security flag for high-risk login"""
        severity = "low"
        if risk_score >= 80:
            severity = "critical"
        elif risk_score >= 60:
            severity = "high"
        elif risk_score >= 40:
            severity = "medium"

        flag = SecurityFlag(
            player_id=player.id,
            flag_type="high_risk_login",
            severity=severity,
            title=f"High-risk login detected (score: {risk_score:.0f})",
            description=f"Risk factors: {', '.join(risk_factors)}",
            extra_data=json.dumps({
                "risk_score": risk_score,
                "risk_factors": risk_factors,
                "ip_address": login.ip_address,
                "fingerprint_hash": login.fingerprint_hash,
            }),
            related_login_id=login.id,
        )
        db.add(flag)

        # Update player flagged status
        player.is_flagged = True
        if risk_score >= 80:
            player.security_risk_level = "high"
        elif risk_score >= 60:
            player.security_risk_level = "elevated"

    async def analyze_risk(
        self,
        db: AsyncSession,
        player_id: str,
        fingerprint_data: DeviceFingerprintData,
        ip_address: str,
    ) -> RiskAnalysisResponse:
        """Analyze risk for a login attempt without recording it"""
        result = await db.execute(select(Player).where(Player.id == player_id))
        player = result.scalar_one_or_none()

        if not player:
            return RiskAnalysisResponse(
                risk_score=100,
                risk_level="critical",
                is_new_device=True,
                is_new_location=True,
                risk_factors=["player_not_found"],
                recommendation="block",
            )

        fingerprint_hash = self.generate_fingerprint_hash(fingerprint_data)
        is_new_device = await self._is_new_device(db, player_id, fingerprint_hash)
        is_new_location = await self._is_new_location(db, player_id, ip_address)

        risk_score, risk_factors = await self._calculate_risk_score(
            db, player, fingerprint_hash, ip_address, is_new_device, is_new_location
        )

        risk_level = "low"
        recommendation = "allow"
        if risk_score >= 80:
            risk_level = "critical"
            recommendation = "block"
        elif risk_score >= 60:
            risk_level = "high"
            recommendation = "challenge"
        elif risk_score >= 40:
            risk_level = "medium"
            recommendation = "challenge"

        return RiskAnalysisResponse(
            risk_score=risk_score,
            risk_level=risk_level,
            is_new_device=is_new_device,
            is_new_location=is_new_location,
            risk_factors=risk_factors,
            recommendation=recommendation,
        )

    async def get_player_security_stats(
        self, db: AsyncSession, player_id: str
    ) -> dict:
        """Get security statistics for a player"""
        # Total logins
        result = await db.execute(
            select(func.count(LoginHistory.id)).where(
                LoginHistory.player_id == player_id
            )
        )
        total_logins = result.scalar() or 0

        # Unique IPs
        result = await db.execute(
            select(func.count(distinct(LoginHistory.ip_address))).where(
                LoginHistory.player_id == player_id
            )
        )
        unique_ips = result.scalar() or 0

        # Device count
        result = await db.execute(
            select(func.count(DeviceFingerprint.id)).where(
                DeviceFingerprint.player_id == player_id
            )
        )
        device_count = result.scalar() or 0

        # Open flags
        result = await db.execute(
            select(func.count(SecurityFlag.id)).where(
                SecurityFlag.player_id == player_id,
                SecurityFlag.status == "open",
            )
        )
        open_flags = result.scalar() or 0

        # Recent risk score (average of last 5 logins)
        result = await db.execute(
            select(LoginHistory.risk_score).where(
                LoginHistory.player_id == player_id
            ).order_by(LoginHistory.created_at.desc()).limit(5)
        )
        recent_scores = [row[0] for row in result.fetchall()]
        avg_risk_score = sum(recent_scores) / len(recent_scores) if recent_scores else 0

        return {
            "total_logins": total_logins,
            "unique_ips": unique_ips,
            "device_count": device_count,
            "open_flags": open_flags,
            "avg_risk_score": avg_risk_score,
        }


    async def check_rate_limit(
        self,
        db: AsyncSession,
        identifier: str,
        identifier_type: str = "ip"  # "ip" or "username"
    ) -> Tuple[bool, int, Optional[datetime]]:
        """
        Check if login attempts are rate limited.
        Returns (is_blocked, remaining_attempts, lockout_until)
        """
        since = datetime.utcnow() - timedelta(minutes=self.FAILED_ATTEMPT_WINDOW_MINUTES)

        # Build query based on identifier type
        if identifier_type == "ip":
            query = select(func.count(LoginHistory.id)).where(
                LoginHistory.ip_address == identifier,
                LoginHistory.login_successful == False,
                LoginHistory.created_at >= since,
            )
        else:  # username
            # Join with player to get by username
            query = select(func.count(LoginHistory.id)).join(Player).where(
                Player.chess_com_username == identifier.lower(),
                LoginHistory.login_successful == False,
                LoginHistory.created_at >= since,
            )

        result = await db.execute(query)
        failed_count = result.scalar() or 0

        if failed_count >= self.MAX_FAILED_ATTEMPTS:
            # Get the most recent failed attempt to calculate lockout end
            if identifier_type == "ip":
                recent_query = select(LoginHistory.created_at).where(
                    LoginHistory.ip_address == identifier,
                    LoginHistory.login_successful == False,
                ).order_by(LoginHistory.created_at.desc()).limit(1)
            else:
                recent_query = select(LoginHistory.created_at).join(Player).where(
                    Player.chess_com_username == identifier.lower(),
                    LoginHistory.login_successful == False,
                ).order_by(LoginHistory.created_at.desc()).limit(1)

            result = await db.execute(recent_query)
            last_failed = result.scalar()

            if last_failed:
                lockout_until = last_failed + timedelta(minutes=self.LOCKOUT_DURATION_MINUTES)
                if datetime.utcnow() < lockout_until:
                    return True, 0, lockout_until

        remaining = max(0, self.MAX_FAILED_ATTEMPTS - failed_count)
        return False, remaining, None

    async def record_failed_login(
        self,
        db: AsyncSession,
        username: str,
        ip_address: str,
        user_agent: str = "",
    ):
        """Record a failed login attempt for rate limiting"""
        # Try to find the player
        result = await db.execute(
            select(Player).where(Player.chess_com_username == username.lower())
        )
        player = result.scalar_one_or_none()

        # Create a minimal login record for rate limiting
        login = LoginHistory(
            player_id=player.id if player else "unknown",
            fingerprint_hash="failed_attempt",
            user_agent=user_agent,
            ip_address=ip_address,
            login_successful=False,
            session_type="failed_login",
            risk_score=0,
        )

        db.add(login)
        await db.commit()

    async def flag_country_mismatch(
        self,
        db: AsyncSession,
        player: Player,
        chess_com_country: str,
    ) -> SecurityFlag:
        """
        Flag and suspend a player who registered with a non-Kenyan Chess.com account.

        Policy: Using someone else's Chess.com account (identity fraud) results in
        immediate suspension pending admin review.
        """
        flag = SecurityFlag(
            player_id=player.id,
            flag_type="country_mismatch",
            severity="critical",
            title=f"Non-Kenyan Chess.com account: {chess_com_country}",
            description=(
                f"Player registered with a Chess.com account from {chess_com_country}, not Kenya (KE). "
                f"This may indicate identity fraud - using someone else's Chess.com account. "
                f"Account has been automatically suspended pending admin review."
            ),
            extra_data=json.dumps({
                "chess_com_username": player.chess_com_username,
                "chess_com_country": chess_com_country,
                "expected_country": "KE",
                "action_taken": "suspended",
            }),
        )
        db.add(flag)

        # Suspend the account
        player.is_active = False
        player.is_flagged = True
        player.security_risk_level = "restricted"

        await db.commit()
        return flag


# Singleton instance
security_service = SecurityService()
