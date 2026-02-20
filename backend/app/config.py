"""Application settings — loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    # Database — MUST be set via env/docker-compose (no default password)
    database_url: str
    database_url_sync: str

    # Redis
    redis_url: str = "redis://redis:6379/0"
    cache_ttl: int = 300  # 5 minutes

    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8080", "http://frontend:3000"]

    # Exchange defaults
    default_exchange: str = "binance"
    default_symbol: str = "BTC/USDT"
    default_timeframe: str = "1h"

    # Analysis defaults
    default_sensitivity: str = "Medium"
    default_signal_mode: str = "Confirmed Only"
    default_limit: int = 500

    # Scheduler
    auto_refresh_enabled: bool = True
    auto_refresh_interval_minutes: int = 5
    agent_cycle_interval_minutes: int = 5

    # Hyperliquid (API keys via .env)
    hyperliquid_wallet_address: str = ""
    hyperliquid_api_secret: str = ""

    # Telegram Bot
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
