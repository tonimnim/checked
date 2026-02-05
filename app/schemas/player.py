import re
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime


def normalize_kenyan_phone(phone: str) -> str:
    """
    Normalize Kenyan phone number to +254 format.
    Accepts: 0705708643, 254705708643, +254705708643
    Returns: +254705708643
    """
    # Remove spaces, dashes, and other characters
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)

    # Handle different formats
    if cleaned.startswith('+254'):
        return cleaned
    elif cleaned.startswith('254'):
        return f'+{cleaned}'
    elif cleaned.startswith('0') and len(cleaned) == 10:
        return f'+254{cleaned[1:]}'
    else:
        raise ValueError('Invalid Kenyan phone number format')


class PlayerCreate(BaseModel):
    """Schema for player registration"""
    chess_com_username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    phone: str = Field(..., description="Kenyan phone number (0705708643 or +254705708643)")
    age: int = Field(..., ge=5, le=120)
    gender: str = Field(..., description="male, female, or other")
    county: Optional[str] = None
    club: Optional[str] = None

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        return normalize_kenyan_phone(v)

    @field_validator('gender')
    @classmethod
    def validate_gender(cls, v):
        allowed = ['male', 'female', 'other']
        if v.lower() not in allowed:
            raise ValueError(f'Gender must be one of: {", ".join(allowed)}')
        return v.lower()


class PlayerLogin(BaseModel):
    """Schema for player login"""
    chess_com_username: str
    password: str


class PlayerLoginWithFingerprint(BaseModel):
    """Schema for player login with device fingerprint for security tracking"""
    chess_com_username: str
    password: str
    # Device fingerprint data (optional but recommended)
    fingerprint: Optional[dict] = None  # DeviceFingerprintData as dict


class PlayerUpdate(BaseModel):
    """Schema for updating player profile"""
    phone: Optional[str] = None
    age: Optional[int] = Field(None, ge=5, le=120)
    gender: Optional[str] = None
    county: Optional[str] = None
    club: Optional[str] = None

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        if v is None:
            return v
        return normalize_kenyan_phone(v)

    @field_validator('gender')
    @classmethod
    def validate_gender(cls, v):
        if v is None:
            return v
        allowed = ['male', 'female', 'other']
        if v.lower() not in allowed:
            raise ValueError(f'Gender must be one of: {", ".join(allowed)}')
        return v.lower()


class PlayerResponse(BaseModel):
    """Schema for player response (what we return to clients)"""
    id: str
    chess_com_username: str
    chess_com_avatar: Optional[str]
    chess_com_joined: Optional[int] = None  # Unix timestamp
    chess_com_status: Optional[str] = None  # premium, basic, etc.
    chess_com_country: Optional[str] = None  # ISO country code (KE, US, etc.)
    phone: str
    age: int
    gender: str
    county: Optional[str]
    club: Optional[str]
    is_active: bool
    is_admin: bool
    created_at: datetime
    # Chess.com ratings
    rating_rapid: Optional[int] = None
    rating_blitz: Optional[int] = None
    rating_bullet: Optional[int] = None
    ratings_updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Token(BaseModel):
    """JWT Token response"""
    access_token: str
    token_type: str = "bearer"
    player: PlayerResponse


class TokenData(BaseModel):
    """Data extracted from JWT token"""
    player_id: Optional[str] = None


class PasswordResetRequest(BaseModel):
    """Request password reset OTP"""
    phone: str

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        return normalize_kenyan_phone(v)


class PasswordResetConfirm(BaseModel):
    """Confirm password reset with OTP"""
    phone: str
    otp: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=6)

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        return normalize_kenyan_phone(v)

    @field_validator('otp')
    @classmethod
    def validate_otp(cls, v):
        if not v.isdigit():
            raise ValueError('OTP must be numeric')
        return v
