import uuid
from datetime import datetime

from pydantic import BaseModel


class TelemetryUpdate(BaseModel):
    """The single standardized telemetry shape the whole system speaks.

    Every provider (simulator today; MAVLink/MAVSDK/NATS later) must produce
    exactly this shape before it reaches the WebSocket/UI layer, so the
    frontend and REST responses never need to know which provider is live.
    """

    drone_id: str
    name: str | None = None
    latitude: float
    longitude: float
    altitude: float
    battery: float
    heading: float
    speed: float
    flight_mode: str
    gps_status: str
    armed: bool
    link_quality: float
    status: str
    timestamp: datetime


class TelemetryHistoryPoint(BaseModel):
    latitude: float
    longitude: float
    altitude: float
    battery: float
    heading: float
    speed: float
    flight_mode: str
    timestamp: datetime


class TelemetrySnapshot(BaseModel):
    drone_id: uuid.UUID
    latest: TelemetryUpdate | None
