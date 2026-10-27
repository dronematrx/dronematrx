"""Wires a TelemetryProvider to the WebSocket broadcast layer and to
persistence, without either side knowing about the other.

    Telemetry Simulator (or Mavlink/NATS provider later)
        -> TelemetryManager
            -> ConnectionManager.broadcast_json  (-> Zustand -> Fleet UI + Map)
            -> DroneService.apply_telemetry      (-> PostgreSQL)
"""
import asyncio
from datetime import datetime, timezone
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
        # When each drone's latest frame *arrived here* (backend clock). Link freshness
        # is judged on this, not on the producer's timestamp: a simulator or vehicle
        # clock that drifts must not make a live drone look stale (or a dead one fresh).
        self.received_at: dict[str, datetime] = {}
        self.dropped_out_of_order = 0
        # Drone-row writes are coalesced (newest frame per drone) and done by one
        # background writer, so a slow database never holds up the telemetry stream.
        self._pending: dict[str, TelemetryUpdate] = {}
        self._writer: asyncio.Task | None = None

    async def start(self) -> None:
        await self.provider.start(self._on_update)

    async def stop(self) -> None:
        await self.provider.stop()

    async def _on_update(self, update: TelemetryUpdate) -> None:
        previous = self.latest.get(update.drone_id)
        try:
            late = previous is not None and update.timestamp < previous.timestamp
        except TypeError:  # naive vs aware timestamps from different producers
            late = False
        if late:
            # Late / re-delivered frame: never move a drone back in time.
            self.dropped_out_of_order += 1
            return
        self.latest[update.drone_id] = update
        received = datetime.now(timezone.utc)
        self.received_at[update.drone_id] = received

        await self.connection_manager.broadcast_json({"type": "telemetry", "data": {**update.model_dump(mode="json"), "received_at": received.isoformat()}})

        self._pending[update.drone_id] = update
        if self._writer is None or self._writer.done():
            self._writer = asyncio.get_running_loop().create_task(self._drain())

    async def _drain(self) -> None:
        # Blocking SQLAlchemy writes run off the event loop; only the newest
        # frame per drone is written when the database falls behind.
        while self._pending:
            _, update = self._pending.popitem()
            try:
                await asyncio.to_thread(self._persist, update)
            except Exception:  # pragma: no cover - persistence must never kill the stream
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
