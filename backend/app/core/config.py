"""Application configuration loaded from environment variables (.env)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Drone MatrX API"
    ENVIRONMENT: str = "development"
    API_V1_PREFIX: str = ""

    DATABASE_URL: str = (
        "postgresql+psycopg2://dronematrx:dronematrx@localhost:5433/dronematrx"
    )

    JWT_SECRET: str = "dev-secret-change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # When true, the backend runs an in-process simulated telemetry generator
    # and broadcasts updates over the /ws/telemetry websocket. This is the
    # local/offline-first development mode. Disable once a real telemetry
    # provider (MAVLink/MAVSDK/NATS) is wired up via TELEMETRY_PROVIDER.
    TELEMETRY_SIMULATOR_ENABLED: bool = True
    TELEMETRY_PROVIDER: str = "simulator"  # simulator | mavlink | nats (future)
    TELEMETRY_TICK_SECONDS: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
