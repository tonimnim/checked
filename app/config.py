from pydantic_settings import BaseSettings
from functools import lru_cache
import secrets
import os


class Settings(BaseSettings):
    # App
    app_name: str = "ChessKenya"
    debug: bool = False  # Set to False in production
    environment: str = "development"  # development, staging, production, testing

    # Database - Use /data for Railway volume, local path for development
    # Can be overridden with DATABASE_URL env var
    # Note: SQLite absolute paths need 4 slashes (sqlite:////path)
    database_url: str = (
        "sqlite+aiosqlite:///:memory:"
        if os.environ.get("TESTING") == "1"
        else (
            "sqlite+aiosqlite:////data/chesskenya.db"
            if os.path.exists("/data")
            else "sqlite+aiosqlite:///./chesskenya.db"
        )
    )

    # JWT Auth - IMPORTANT: Set SECRET_KEY env var in production!
    secret_key: str = secrets.token_urlsafe(32)
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days

    # Chess.com API
    chess_com_api_base: str = "https://api.chess.com/pub"

    # Rate limiting (for future use)
    rate_limit_per_minute: int = 60

    # Africa's Talking SMS
    at_username: str = "sandbox"  # "sandbox" for testing, your username for production
    at_api_key: str = ""  # Your API key
    at_sender_id: str = ""  # Optional: Your registered sender ID (only for production)

    # Web Push Notifications (VAPID)
    # Generate keys: npx web-push generate-vapid-keys
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_contact_email: str = "admin@chesskenya.com"

    # M-Pesa (for future use)
    mpesa_consumer_key: str = ""
    mpesa_consumer_secret: str = ""
    mpesa_shortcode: str = ""
    mpesa_passkey: str = ""
    mpesa_callback_url: str = ""
    mpesa_environment: str = "sandbox"  # sandbox or production

    class Config:
        env_file = ".env"

    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
