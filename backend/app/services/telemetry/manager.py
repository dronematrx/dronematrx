"""Wires a TelemetryProvider to the WebSocket broadcast layer and to
persistence, without either side knowing about the other.

    Telemetry Simulator (or Mavlink/NATS provider later)
        -> TelemetryManager
            -> ConnectionManager.broadcast_json  (-> Zustand -> Fleet UI + Map)
            -> DroneService.apply_telemetry      (-> PostgreSQL)
"""
import logging
import uuid

from sqlalchemy.orm import sessionmaker

from app.schemas.telemetry import TelemetryUpdate
from app.services.telemetry.base import TelemetryProvider
from app.websocket.connection_manager import ConnectionManager

logger = logging.getLogger("dronematrx.telemetry")


class TelemetryManager:
    def __init__(
        self,
        provider: TelemetryProvider,
        connection_manager: ConnectionManager,
        session_factory: sessionmaker,
    ):
        self.provider = provider
        self.connection_manager = connection_manager
        self._session_factory = session_factory
        self.latest: dict[str, TelemetryUpdate] = {}

    async def start(self) -> None:
        await self.provider.start(self._on_update)

    async def stop(self) -> None:
        await self.provider.stop()

    async def _on_update(self, update: TelemetryUpdate) -> None:
        self.latest[update.drone_id] = update

        await self.connection_manager.broadcast_json({"type": "telemetry", "data": update.model_dump(mode="json")})

        try:
            self._persist(update)
        except Exception:  # pragma: no cover - persistence must never kill the sim loop
            logger.exception("Failed to persist telemetry for drone %s", update.drone_id)

    def _persist(self, update: TelemetryUpdate) -> None:
        from app.models.drone import Drone
        from app.services.drone_service import DroneService

        db = self._session_factory()
        try:
            try:
                drone_id = uuid.UUID(update.drone_id)
            except ValueError:
                return
            drone = db.get(Drone, drone_id)
            if drone is None:
                return
            DroneService(db).apply_telemetry(
                drone,
                latitude=update.latitude,
                longitude=update.longitude,
                altitude=update.altitude,
                heading=update.heading,
                speed=update.speed,
                battery=update.battery,
                flight_mode=update.flight_mode,
                gps_status=update.gps_status,
                armed=update.armed,
                link_quality=update.link_quality,
                status=update.status,
                timestamp=update.timestamp,
            )
        finally:
            db.close()
