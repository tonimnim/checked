from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ============ Device Fingerprint ============

class DeviceFingerprintData(BaseModel):
    """Fingerprint data sent from frontend"""
    user_agent: str
    platform: Optional[str] = None
    screen_resolution: Optional[str] = None
    timezone: Optional[str] = None
    language: Optional[str] = None
    canvas_hash: Optional[str] = None
    webgl_renderer: Optional[str] = None
    webgl_vendor: Optional[str] = None
    fonts_hash: Optional[str] = None
    audio_hash: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "platform": "Windows",
                "screen_resolution": "1920x1080",
                "timezone": "Africa/Nairobi",
                "language": "en-US",
                "canvas_hash": "abc123...",
                "webgl_renderer": "ANGLE (Intel, Intel(R) UHD Graphics 620)"
            }
        }


class DeviceFingerprintResponse(BaseModel):
    id: str
    fingerprint_hash: str
    device_name: Optional[str]
    platform: Optional[str]
    browser: Optional[str]
    is_primary: bool
    is_trusted: bool
    login_count: int
    first_seen_at: datetime
    last_seen_at: datetime

    class Config:
        from_attributes = True


# ============ Login History ============

class LoginHistoryResponse(BaseModel):
    id: str
    player_id: str
    fingerprint_hash: str
    platform: Optional[str]
    browser: Optional[str]
    ip_address: str
    country: Optional[str]
    city: Optional[str]
    login_successful: bool
    session_type: str
    is_new_device: bool
    is_new_location: bool
    risk_score: float
    created_at: datetime

    class Config:
        from_attributes = True


class LoginHistoryWithPlayer(LoginHistoryResponse):
    """Login history with player info (for admin views)"""
    player_username: Optional[str] = None
    player_avatar: Optional[str] = None


# ============ Security Flags ============

class SecurityFlagCreate(BaseModel):
    flag_type: str
    severity: str = "low"
    title: str
    description: str
    extra_data: Optional[str] = None
    related_login_id: Optional[str] = None
    related_tournament_id: Optional[str] = None


class SecurityFlagResponse(BaseModel):
    id: str
    player_id: str
    flag_type: str
    severity: str
    title: str
    description: str
    metadata: Optional[str]
    status: str
    resolved_by: Optional[str]
    resolved_at: Optional[datetime]
    resolution_notes: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class SecurityFlagWithPlayer(SecurityFlagResponse):
    """Security flag with player info (for admin views)"""
    player_username: Optional[str] = None
    player_avatar: Optional[str] = None


class SecurityFlagUpdate(BaseModel):
    status: Optional[str] = None
    resolution_notes: Optional[str] = None


# ============ Shared Device Alerts ============

class SharedDeviceAlertResponse(BaseModel):
    id: str
    fingerprint_hash: str
    player_ids: str
    player_count: int
    status: str
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    notes: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class SharedDeviceAlertWithPlayers(SharedDeviceAlertResponse):
    """Shared device alert with player details"""
    players: List[dict] = []  # List of {id, username, avatar}


# ============ Security Dashboard ============

class SecurityOverview(BaseModel):
    """Security dashboard overview stats"""
    total_logins_today: int
    total_logins_week: int
    new_devices_today: int
    new_devices_week: int
    open_flags_count: int
    high_severity_flags: int
    flagged_players_count: int
    shared_device_alerts: int
    recent_flags: List[SecurityFlagWithPlayer]
    recent_suspicious_logins: List[LoginHistoryWithPlayer]


class PlayerSecurityProfile(BaseModel):
    """Detailed security profile for a player"""
    player_id: str
    player_username: str
    player_avatar: Optional[str]
    registration_date: datetime
    registration_ip: Optional[str]
    registration_fingerprint: Optional[str]
    security_risk_level: str
    is_flagged: bool

    # Device info
    total_devices: int
    primary_device: Optional[DeviceFingerprintResponse]
    devices: List[DeviceFingerprintResponse]

    # Login stats
    total_logins: int
    last_login: Optional[datetime]
    unique_ips: int
    unique_locations: List[str]

    # Flags
    open_flags: int
    total_flags: int
    flags: List[SecurityFlagResponse]

    # Risk indicators
    risk_score: float
    risk_factors: List[str]


class PlayerSecurityListItem(BaseModel):
    """Player item for security list view"""
    player_id: str
    player_username: str
    player_avatar: Optional[str]
    security_risk_level: str
    is_flagged: bool
    open_flags_count: int
    device_count: int
    last_login_at: Optional[datetime]
    recent_risk_score: float


# ============ Risk Analysis ============

class RiskAnalysisRequest(BaseModel):
    """Request to analyze risk for a login attempt"""
    player_id: str
    fingerprint: DeviceFingerprintData
    ip_address: str


class RiskAnalysisResponse(BaseModel):
    """Risk analysis result"""
    risk_score: float  # 0-100
    risk_level: str  # low, medium, high, critical
    is_new_device: bool
    is_new_location: bool
    risk_factors: List[str]
    recommendation: str  # allow, challenge, block
