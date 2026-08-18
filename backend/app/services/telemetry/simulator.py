"""Development-only simulated telemetry generator.

Moves each active drone gradually toward a randomly chosen nearby waypoint
(rather than teleporting), drains/recharges battery slowly, and occasionally
varies flight mode / link quality so the UI has something realistic to
react to. This is intentionally the *only* place in the backend that knows
how simulated positions are computed -- everything else just consumes
TelemetryUpdate objects via the TelemetryProvider interface.
"""
import asyncio
import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models.drone import Drone
from app.schemas.telemetry import TelemetryUpdate
from app.services.telemetry.base import TelemetryCallback, TelemetryProvider

FLIGHT_MODES = ["LOITER", "MISSION", "HOLD", "RTL", "STABILIZE"]


@dataclass
class _SimDroneState:
    drone_id: str
    name: str
    lat: float
    lon: float
    altitude: float
    heading: float = 0.0
    speed: float = 0.0
    battery: float = 95.0
    battery_direction: int = -1
    flight_mode: str = "LOITER"
    gps_status: str = "3D_FIX"
    armed: bool = True
    link_quality: float = 95.0
    status: str = "active"
    target_lat: float = field(default=0.0)
    target_lon: float = field(default=0.0)

    def pick_new_target(self, radius_deg: float = 0.004) -> None:
        angle = random.uniform(0, 2 * math.pi)
        r = random.uniform(radius_deg * 0.3, radius_deg)
        self.target_lat = self.lat + r * math.cos(angle)
        self.target_lon = self.lon + r * math.sin(angle)


class SimulationTelemetryProvider(TelemetryProvider):
    """Default local/offline telemetry source. Swap for MavlinkTelemetryProvider
    or NatsTelemetryProvider later without touching any caller of TelemetryProvider.
    """

    def __init__(self, session_factory: sessionmaker, tick_seconds: float = 1.0):
        self._session_factory = session_factory
        self._tick_seconds = tick_seconds
        self._task: asyncio.Task | None = None
        self._running = False
        self._states: dict[str, _SimDroneState] = {}

    async def start(self, on_update: TelemetryCallback) -> None:
        self._load_initial_state()
        self._running = True
        self._task = asyncio.create_task(self._run(on_update))

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _load_initial_state(self) -> None:
        db = self._session_factory()
        try:
            from geoalchemy2.shape import to_shape

            drones = db.execute(select(Drone).where(Drone.active.is_(True))).scalars().all()
            for drone in drones:
                if drone.location is not None:
                    point = to_shape(drone.location)
                    lat, lon = point.y, point.x
                else:
                    # Default to the Gandhinagar, Gujarat area (project HQ) when a
                    # drone has never reported a position yet.
                    lat, lon = 23.2156 + random.uniform(-0.01, 0.01), 72.6369 + random.uniform(-0.01, 0.01)

                state = _SimDroneState(
                    drone_id=str(drone.id),
                    name=drone.name,
                    lat=lat,
                    lon=lon,
                    altitude=drone.altitude or random.uniform(30, 60),
                    battery=drone.battery or random.uniform(60, 100),
                    flight_mode=drone.flight_mode or "LOITER",
                    status=drone.status if drone.status in ("active", "online") else "active",
                )
                state.pick_new_target()
                self._states[state.drone_id] = state
        finally:
            db.close()

    async def _run(self, on_update: TelemetryCallback) -> None:
        while self._running:
            for state in self._states.values():
                update = self._advance(state)
                await on_update(update)
            await asyncio.sleep(self._tick_seconds)

    def _advance(self, s: _SimDroneState) -> TelemetryUpdate:
        dx = s.target_lon - s.lon
        dy = s.target_lat - s.lat
        dist = math.hypot(dx, dy)

        if dist < 0.0003:
            s.pick_new_target()
            dx, dy = s.target_lon - s.lon, s.target_lat - s.lat
            dist = math.hypot(dx, dy) or 1e-9

        step = min(dist, 0.00006)  # gradual movement, not a teleport
        s.lon += (dx / dist) * step
        s.lat += (dy / dist) * step
        s.heading = (math.degrees(math.atan2(dx, dy)) + 360) % 360
        s.speed = round(step / self._tick_seconds * 111_000, 2)  # deg/s -> approx m/s

        s.battery += s.battery_direction * random.uniform(0.02, 0.08)
        if s.battery <= 20:
            s.battery_direction = 1
            s.flight_mode = "RTL"
        elif s.battery >= 95:
            s.battery_direction = -1
            if s.flight_mode == "RTL":
                s.flight_mode = "LOITER"  # simulated battery swap complete, back in service
        s.battery = round(max(0.0, min(100.0, s.battery)), 1)

        if s.flight_mode != "RTL" and random.random() < 0.01:
            s.flight_mode = random.choice(FLIGHT_MODES)

        s.link_quality = round(max(40.0, min(100.0, s.link_quality + random.uniform(-2, 2))), 1)
        s.gps_status = "3D_FIX" if random.random() > 0.02 else "2D_FIX"
        s.altitude = round(max(0.0, s.altitude + random.uniform(-0.3, 0.3)), 1)

        return TelemetryUpdate(
            drone_id=s.drone_id,
            name=s.name,
            latitude=round(s.lat, 6),
            longitude=round(s.lon, 6),
            altitude=s.altitude,
            battery=s.battery,
            heading=round(s.heading, 1),
            speed=s.speed,
            flight_mode=s.flight_mode,
            gps_status=s.gps_status,
            armed=s.armed,
            link_quality=s.link_quality,
            status=s.status,
            timestamp=datetime.now(timezone.utc),
        )

    def register_drone(self, drone_id: uuid.UUID, name: str, lat: float, lon: float, altitude: float) -> None:
        """Allow newly-created drones to join the running simulation without a restart."""
        state = _SimDroneState(drone_id=str(drone_id), name=name, lat=lat, lon=lon, altitude=altitude)
        state.pick_new_target()
        self._states[state.drone_id] = state

    def unregister_drone(self, drone_id: uuid.UUID) -> None:
        self._states.pop(str(drone_id), None)
