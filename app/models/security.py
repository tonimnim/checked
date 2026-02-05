import uuid
import hashlib
from datetime import datetime
from sqlalchemy import String, Boolean, Integer, DateTime, Text, Index, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, TYPE_CHECKING

from app.database import Base

if TYPE_CHECKING:
    from app.models.player import Player


class LoginHistory(Base):
    """Track every login attempt with device and location info"""
    __tablename__ = "login_history"

    __table_args__ = (
        Index("ix_login_history_player_created", "player_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    player_id: Mapped[str] = mapped_column(String(36), ForeignKey("players.id", ondelete="CASCADE"), index=True)

    # Device fingerprint (hashed for comparison)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), index=True)

    # Raw fingerprint components (for analysis)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    platform: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # Windows, macOS, Android, iOS
    browser: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    screen_resolution: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # e.g., "1920x1080"
    timezone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # e.g., "Africa/Nairobi"
    language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # e.g., "en-US"

    # Location info
    ip_address: Mapped[str] = mapped_column(String(45))  # Supports IPv6
    country: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Session info
    login_successful: Mapped[bool] = mapped_column(Boolean, default=True)
    session_type: Mapped[str] = mapped_column(String(20), default="login")  # login, register, token_refresh

    # Risk assessment
    is_new_device: Mapped[bool] = mapped_column(Boolean, default=False)
    is_new_location: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    player: Mapped["Player"] = relationship(back_populates="login_history")

    @staticmethod
    def generate_fingerprint_hash(components: dict) -> str:
        """Generate a consistent hash from fingerprint components"""
        # Use stable components for hashing
        stable_parts = [
            components.get("platform", ""),
            components.get("screen_resolution", ""),
            components.get("timezone", ""),
            components.get("canvas_hash", ""),
            components.get("webgl_renderer", ""),
        ]
        fingerprint_string = "|".join(str(p) for p in stable_parts)
        return hashlib.sha256(fingerprint_string.encode()).hexdigest()


class DeviceFingerprint(Base):
    """Store known devices for each player"""
    __tablename__ = "device_fingerprints"

    __table_args__ = (
        Index("ix_device_fingerprints_player_id", "player_id"),
        Index("ix_device_fingerprints_hash", "fingerprint_hash"),
        Index("ix_device_fingerprints_player_hash", "player_id", "fingerprint_hash", unique=True),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    player_id: Mapped[str] = mapped_column(String(36), ForeignKey("players.id", ondelete="CASCADE"))

    fingerprint_hash: Mapped[str] = mapped_column(String(64))

    # Device details
    device_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # e.g., "Chrome on Windows"
    platform: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    browser: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Trust status
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)  # First registered device
    is_trusted: Mapped[bool] = mapped_column(Boolean, default=True)
    trust_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # "registration", "admin_approved", etc.

    # Usage stats
    login_count: Mapped[int] = mapped_column(Integer, default=1)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    player: Mapped["Player"] = relationship(back_populates="devices")


class SecurityFlag(Base):
    """Track suspicious activities and security alerts"""
    __tablename__ = "security_flags"

    __table_args__ = (
        Index("ix_security_flags_player_id", "player_id"),
        Index("ix_security_flags_severity", "severity"),
        Index("ix_security_flags_status", "status"),
        Index("ix_security_flags_created_at", "created_at"),
        Index("ix_security_flags_flag_type", "flag_type"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    player_id: Mapped[str] = mapped_column(String(36), ForeignKey("players.id", ondelete="CASCADE"))

    # Flag details
    flag_type: Mapped[str] = mapped_column(String(50))  # new_device, location_change, rapid_device_switch, shared_device, performance_anomaly
    severity: Mapped[str] = mapped_column(String(20), default="low")  # low, medium, high, critical

    # Context
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[Text] = mapped_column(Text)
    extra_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON with additional context

    # Related records
    related_login_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    related_tournament_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # Status
    status: Mapped[str] = mapped_column(String(20), default="open")  # open, investigating, resolved, dismissed
    resolved_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)  # Admin player_id
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    player: Mapped["Player"] = relationship(back_populates="security_flags")


class SharedDeviceAlert(Base):
    """Track when same device is used by multiple accounts"""
    __tablename__ = "shared_device_alerts"

    __table_args__ = (
        Index("ix_shared_device_alerts_fingerprint", "fingerprint_hash"),
        Index("ix_shared_device_alerts_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    fingerprint_hash: Mapped[str] = mapped_column(String(64))

    # Players involved (store as comma-separated IDs for simplicity)
    player_ids: Mapped[str] = mapped_column(Text)  # JSON array of player IDs
    player_count: Mapped[int] = mapped_column(Integer, default=2)

    # Alert status
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, reviewed, legitimate, suspicious
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
