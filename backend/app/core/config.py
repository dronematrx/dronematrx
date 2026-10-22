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

    # Sprint 4: NATS JetStream message bus (drone telemetry/state/alert/command).
    # Credentials, if any, belong in the URL via .env -- never in code.
    NATS_ENABLED: bool = True
    NATS_URL: str = "nats://localhost:4222"
    NATS_STREAM: str = "DRONES"
    NATS_SUBJECT_PREFIX: str = ""  # tests use an isolated prefix + stream
    NATS_MAX_AGE_HOURS: float = 24.0  # JetStream retention = replay window
    NATS_CONNECT_TIMEOUT_S: float = 2.0
    NATS_MONITOR_URL: str = "http://localhost:8222"

    # Sprint 4: ORCA collision avoidance (10 Hz) + communication manager (2 Hz).
    ORCA_ENABLED: bool = True
    ORCA_RATE_HZ: float = 10.0
    COMMS_POLL_HZ: float = 2.0

    # Mission planning (Sprint 3)
    CBBA_TIME_LIMIT_SECONDS: float = 5.0  # hard cap; acceptance target is < 2 s for 8 drones
    MISSION_TELEMETRY_MAX_AGE_SECONDS: float = 120.0  # ARMED -> ACTIVE requires fresher telemetry

    # Sprint 5: Gazebo + PX4 SITL simulation (docker-compose.sim.yml).
    SIMHOST_URL: str = "http://localhost:8100"
    SIM_WORKSPACE_DIR: str = "sim_workspace"  # host path (relative to backend/), bind-mounted into simhost
    SIM_WORKSPACE_URI: str = "/workspace"  # the same directory as the simulator sees it
    SIM_DEM_CACHE_DIR: str = "sim_cache/dem"
    SIM_DEM_SOURCE: str = "terrarium"  # terrarium | flat
    SIM_ALLOW_FLAT_FALLBACK: bool = True  # labelled fallback when no DEM data can be fetched
    SIM_COMM_LOSS_S: float = 3.0  # telemetry silence that marks a simulated drone comm_lost


@lru_cache
def get_settings() -> Settings:
    return Settings()
