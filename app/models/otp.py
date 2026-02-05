"""
OTP (One-Time Password) model for password reset and verification
"""
import uuid
import secrets
import hashlib
from datetime import datetime, timedelta
from sqlalchemy import String, DateTime, Integer, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# OTP settings
OTP_LENGTH = 6
OTP_EXPIRY_MINUTES = 10
MAX_OTP_ATTEMPTS = 3
OTP_COOLDOWN_MINUTES = 1  # Time between OTP requests


class OTP(Base):
    __tablename__ = "otps"

    __table_args__ = (
        Index("ix_otps_phone_purpose", "phone", "purpose"),
        Index("ix_otps_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    phone: Mapped[str] = mapped_column(String(20), index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # password_reset, phone_verify, etc.

    # Store hashed OTP for security
    otp_hash: Mapped[str] = mapped_column(String(64))

    # Tracking
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    @staticmethod
    def generate_otp() -> str:
        """Generate a random 6-digit OTP"""
        return ''.join([str(secrets.randbelow(10)) for _ in range(OTP_LENGTH)])

    @staticmethod
    def hash_otp(otp: str) -> str:
        """Hash OTP for secure storage"""
        return hashlib.sha256(otp.encode()).hexdigest()

    def verify_otp(self, otp: str) -> bool:
        """Verify if provided OTP matches"""
        return self.otp_hash == self.hash_otp(otp)

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.is_used and not self.is_expired and self.attempts < MAX_OTP_ATTEMPTS

    @classmethod
    def create_for_phone(cls, phone: str, purpose: str = "password_reset") -> tuple["OTP", str]:
        """
        Create a new OTP for a phone number.
        Returns (OTP instance, raw OTP string)
        """
        raw_otp = cls.generate_otp()
        otp_instance = cls(
            phone=phone,
            purpose=purpose,
            otp_hash=cls.hash_otp(raw_otp),
            expires_at=datetime.utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES)
        )
        return otp_instance, raw_otp
