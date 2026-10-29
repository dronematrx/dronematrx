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

    # Sprint 6: mission control.
    MC_TELEMETRY_STALE_S: float = 5.0  # no telemetry for this long -> STALE (not connected)
    MC_TELEMETRY_OFFLINE_S: float = 60.0  # ... and for this long -> OFFLINE
    MC_PREARM_MIN_BATTERY_PCT: float = 30.0
    MC_COMMAND_CONFIRM_TIMEOUT_S: float = 20.0  # telemetry must show the commanded state within this time
    MC_BENCH_LAT: float = 23.2400  # SITL bench site (open ground, Gandhinagar; same area as the demo fleet)
    MC_BENCH_LON: float = 72.6500
    # Flight-controller discovery scope. SITL: bench sessions on SIMHOST_URL.
    # Hardware: explicit allow-list only ("udp:192.168.1.10:14550,serial:/dev/ttyACM0"); never scanned.
    FC_HARDWARE_ENDPOINTS: str = ""
    # Media relay (MediaMTX, docker-compose.sim.yml). Backend-only credentials.
    MEDIAMTX_WHEP_URL: str = "http://127.0.0.1:8889"
    MEDIAMTX_API_URL: str = "http://127.0.0.1:9997"
    MEDIAMTX_PLAYBACK_URL: str = "http://127.0.0.1:9996"
    MEDIAMTX_USER: str = "dmx-backend"
    MEDIAMTX_PASS: str = "dmx-backend-dev"
    # Admin-only deterministic detection fixtures (labelled TEST FIXTURE). Off in production.
    DETECTION_TEST_FIXTURES: bool = True
    # Real aircraft: the hardware bridge (hwbridge/, ArduPilot over MAVLink) on the ops laptop.
    HWBRIDGE_URL: str = "http://127.0.0.1:8110"
    HWBRIDGE_TOKEN: str = ""  # must match "token" in hwbridge/config.json
    HW_PREARM_MIN_BATTERY_PCT: float = 50.0  # real batteries: stricter than the simulator
    HW_FLIGHT_PERMISSION_MAX_MIN: int = 30  # a human flight permission lasts at most this long


@lru_cache
def get_settings() -> Settings:
    return Settings()
