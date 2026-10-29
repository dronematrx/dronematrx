"""Development-only simulated drone fleet (the "drone side" of the system).

Sprint 4: a 10 Hz motion loop. Every 100 ms each drone's preferred velocity
(patrol target / scenario waypoint / home / hold) is passed through the
`velocity_filter` (the live ORCA runtime) and the returned safe velocity is
integrated. Telemetry is emitted at 1 Hz per drone and a state hook fires
at 5 Hz; the runtime publishes both to NATS through the active comms link.

This is intentionally the *only* place in the backend that knows how
simulated positions are computed -- everything else consumes
TelemetryUpdate objects via the TelemetryProvider interface.
"""
import asyncio
import logging
import math
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.logging import log_event
from app.models.drone import Drone
from app.schemas.telemetry import TelemetryUpdate
from app.services.telemetry.base import TelemetryCallback, TelemetryProvider

logger = logging.getLogger("dronematrx.simulator")

FLIGHT_MODES = ["LOITER", "MISSION", "HOLD", "RTL", "STABILIZE"]
M_PER_DEG_LAT = 110_574.0
CRUISE_MPS = 6.5  # ~ the Sprint 2 patrol speed
CLIMB_MPS = 2.0
FLIGHT_MODE_BY_SIM_MODE = {"patrol": "LOITER", "scenario": "MISSION", "hold": "HOLD", "rth": "RTL", "landed": "STANDBY"}


def m_per_deg_lon(lat: float) -> float:
    return 111_320.0 * math.cos(math.radians(lat))


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
    # Patrol anchor: targets are picked around home, not around the current
    # position, so a long-running simulation never random-walks drones away.
    home_lat: float | None = None
    home_lon: float | None = None
    # Sprint 4 kinematics / supervision
    uin: str = ""
    vx: float = 0.0  # m/s east
    vy: float = 0.0  # m/s north
    cruise_speed: float = CRUISE_MPS
    max_speed: float | None = None
    mode: str = "patrol"  # patrol | scenario | hold | rth | landed
    resume_mode: str = "patrol"
    band: int | None = None
    target_altitude: float | None = None
    waypoints: list[tuple[float, float]] = field(default_factory=list)
    plan_horizon_s: float | None = None

    def pick_new_target(self, radius_deg: float = 0.004) -> None:
        if self.home_lat is None or self.home_lon is None:
            self.home_lat, self.home_lon = self.lat, self.lon
        angle = random.uniform(0, 2 * math.pi)
        r = random.uniform(radius_deg * 0.3, radius_deg)
        self.target_lat = self.home_lat + r * math.cos(angle)
        self.target_lon = self.home_lon + r * math.sin(angle)


VelocityFilter = Callable[[list[_SimDroneState], float, dict[str, tuple[float, float]]], dict[str, tuple[float, float]]]
StateListener = Callable[[list[_SimDroneState]], Awaitable[None]]


class SimulationTelemetryProvider(TelemetryProvider):
    """Default local/offline telemetry source. Swap for MavlinkTelemetryProvider
    or NatsTelemetryProvider later without touching any caller of TelemetryProvider.
    """

    def __init__(self, session_factory: sessionmaker, tick_seconds: float = 1.0, motion_hz: float = 10.0, state_hz: float = 5.0):
        self._session_factory = session_factory
        self._tick_seconds = tick_seconds
        self.motion_hz = motion_hz
        self.state_hz = state_hz
        self._task: asyncio.Task | None = None
        self._running = False
        self._states: dict[str, _SimDroneState] = {}
        self.velocity_filter: VelocityFilter | None = None
        self.state_listener: StateListener | None = None
        self.on_tick_committed: Callable[[float], None] | None = None
        self.loop_overruns = 0

    @property
    def states(self) -> list[_SimDroneState]:
        return list(self._states.values())

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

    def _state_from_drone(self, drone: Drone) -> _SimDroneState:
        from geoalchemy2.shape import to_shape

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
            uin=drone.uin or str(drone.id),
            lat=lat,
            lon=lon,
            altitude=drone.altitude or random.uniform(30, 60),
            battery=drone.battery or random.uniform(60, 100),
            flight_mode=drone.flight_mode or "LOITER",
            status=drone.status if drone.status in ("active", "online") else "active",
            max_speed=drone.max_speed,
        )
        state.pick_new_target()
        return state

    def initial_states(self) -> dict[str, "_SimDroneState"]:
        """Fresh states for every flyable drone in the DB (safe from any thread)."""
        previous = self._states
        self._states = {}
        try:
            self._load_initial_state()
            return self._states
        finally:
            self._states = previous

    def _load_initial_state(self) -> None:
        db = self._session_factory()
        try:
            # Offline / in-maintenance airframes stay parked: they must not
            # start reporting flight telemetry (and must not look flyable to
            # mission planning).
            drones = db.execute(
                select(Drone).where(
                    Drone.active.is_(True),
                    Drone.status.notin_(("offline", "maintenance")),
                    # Sprint 5: SITL drones move only from Gazebo/PX4 telemetry.
                    Drone.simulated.is_(False),
                    # Real aircraft report only through the hardware bridge.
                    Drone.hardware.is_(False),
                )
            ).scalars().all()
            for drone in drones:
                state = self._state_from_drone(drone)
                self._states[state.drone_id] = state
        finally:
            db.close()

    def load_states(self, drone_ids: list[Any]) -> list[_SimDroneState]:
        """Build (but do not attach) states for drones -- safe from any thread."""
        db = self._session_factory()
        try:
            return [self._state_from_drone(d) for d in db.execute(select(Drone).where(Drone.id.in_(list(drone_ids)), Drone.simulated.is_(False), Drone.hardware.is_(False))).scalars()]
        finally:
            db.close()

    def attach(self, states: list[_SimDroneState]) -> None:
        """Attach prepared states. Must run on the event loop thread."""
        for state in states:
            self._states.setdefault(state.drone_id, state)

    def ensure_drones(self, drone_ids: list[uuid.UUID]) -> list[_SimDroneState]:
        """Bring specific drones into the simulation (e.g. the demo scenario)."""
        missing = [d for d in drone_ids if str(d) not in self._states]
        if missing:
            db = self._session_factory()
            try:
                for drone in db.execute(select(Drone).where(Drone.id.in_(missing), Drone.simulated.is_(False), Drone.hardware.is_(False))).scalars():
                    state = self._state_from_drone(drone)
                    self._states[state.drone_id] = state
            finally:
                db.close()
        return [self._states[str(d)] for d in drone_ids if str(d) in self._states]

    # ------------------------------------------------------------ kinematics

    def preferred_velocity(self, s: _SimDroneState) -> tuple[float, float]:
        if s.mode in ("hold", "landed"):
            s.plan_horizon_s = 0.0
            return (0.0, 0.0)
        if s.mode == "rth" and s.home_lat is not None and s.home_lon is not None:
            target = (s.home_lat, s.home_lon)
        elif s.mode == "scenario":
            if not s.waypoints:
                s.plan_horizon_s = 0.0
                return (0.0, 0.0)
            target = s.waypoints[0]
        else:
            target = (s.target_lat, s.target_lon)
        dy = (target[0] - s.lat) * M_PER_DEG_LAT
        dx = (target[1] - s.lon) * m_per_deg_lon(s.lat)
        dist = math.hypot(dx, dy)
        if dist < 1.0:
            if s.mode == "scenario":
                s.waypoints.pop(0)
            elif s.mode == "patrol":
                s.pick_new_target()
            s.plan_horizon_s = 0.0
            return (0.0, 0.0)
        speed = min(s.cruise_speed, dist / (1.0 / self.motion_hz))
        remaining = dist + sum(
            math.hypot((b[0] - a[0]) * M_PER_DEG_LAT, (b[1] - a[1]) * m_per_deg_lon(s.lat))
            for a, b in zip(s.waypoints, s.waypoints[1:])
        ) if s.mode == "scenario" else dist
        s.plan_horizon_s = remaining / max(s.cruise_speed, 0.1)
        return (dx / dist * speed, dy / dist * speed)

    def step_motion(self, dt: float) -> None:
        states = list(self._states.values())
        preferred = {s.drone_id: self.preferred_velocity(s) for s in states}
        safe = self.velocity_filter(states, dt, preferred) if self.velocity_filter else preferred
        for s in states:
            vx, vy = safe.get(s.drone_id, preferred[s.drone_id])
            s.vx, s.vy = vx, vy
            s.lat += vy * dt / M_PER_DEG_LAT
            s.lon += vx * dt / m_per_deg_lon(s.lat)
            s.speed = round(math.hypot(vx, vy), 2)
            if s.speed > 0.3:
                s.heading = (math.degrees(math.atan2(vx, vy)) + 360) % 360
            if s.target_altitude is not None:
                delta = s.target_altitude - s.altitude
                s.altitude += max(-CLIMB_MPS * dt, min(CLIMB_MPS * dt, delta))
            s.flight_mode = FLIGHT_MODE_BY_SIM_MODE.get(s.mode, s.flight_mode)

    def _per_second(self, s: _SimDroneState) -> None:
        if s.mode == "landed":
            s.battery = min(100.0, s.battery + 2.0)  # simulated battery swap / charge
        else:
            s.battery -= random.uniform(0.02, 0.08)
        s.battery = round(max(0.0, min(100.0, s.battery)), 1)
        s.link_quality = round(max(40.0, min(100.0, s.link_quality + random.uniform(-2, 2))), 1)
        s.gps_status = "3D_FIX" if random.random() > 0.02 else "2D_FIX"

    def _telemetry(self, s: _SimDroneState) -> TelemetryUpdate:
        return TelemetryUpdate(
            drone_id=s.drone_id,
            name=s.name,
            uin=s.uin,
            latitude=round(s.lat, 7),
            longitude=round(s.lon, 7),
            altitude=round(s.altitude, 1),
            battery=s.battery,
            heading=round(s.heading % 360, 1),
            speed=s.speed,
            flight_mode=s.flight_mode,
            gps_status=s.gps_status,
            armed=s.armed,
            link_quality=s.link_quality,
            status=s.status,
            timestamp=datetime.now(timezone.utc),
        )

    async def _run(self, on_update: TelemetryCallback) -> None:
        period = 1.0 / self.motion_hz
        telemetry_every = max(1, round(self._tick_seconds * self.motion_hz))
        state_every = max(1, round(self.motion_hz / self.state_hz))
        tick = 0
        next_tick = time.monotonic()
        while self._running:
            started = time.perf_counter()
            self.step_motion(period)
            if self.on_tick_committed is not None:
                self.on_tick_committed((time.perf_counter() - started) * 1000)
            tick += 1
            if tick % state_every == 0 and self.state_listener is not None:
                await self.state_listener(self.states)
            if tick % telemetry_every == 0:
                for state in list(self._states.values()):
                    self._per_second(state)
                    await on_update(self._telemetry(state))
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay < -period:  # fell more than a cycle behind: resync rather than burst
                self.loop_overruns += 1
                log_event(logger, "simulator.overrun", logging.DEBUG, behind_ms=round(-delay * 1000, 1))
                next_tick = time.monotonic()
                delay = 0.0
            await asyncio.sleep(max(0.0, delay))

    def _advance(self, s: _SimDroneState) -> TelemetryUpdate:
        """One second of unfiltered motion for a single drone (used by tests)."""
        substeps = max(1, round(self.motion_hz * self._tick_seconds))
        dt = self._tick_seconds / substeps
        for _ in range(substeps):
            vx, vy = self.preferred_velocity(s)
            s.vx, s.vy = vx, vy
            s.lat += vy * dt / M_PER_DEG_LAT
            s.lon += vx * dt / m_per_deg_lon(s.lat)
            s.speed = round(math.hypot(vx, vy), 2)
            if s.speed > 0.3:
                s.heading = (math.degrees(math.atan2(vx, vy)) + 360) % 360
        self._per_second(s)
        return self._telemetry(s)

    def register_drone(self, drone_id: uuid.UUID, name: str, lat: float, lon: float, altitude: float) -> None:
        """Allow newly-created drones to join the running simulation without a restart."""
        state = _SimDroneState(drone_id=str(drone_id), name=name, lat=lat, lon=lon, altitude=altitude)
        state.pick_new_target()
        self._states[state.drone_id] = state

    def unregister_drone(self, drone_id: uuid.UUID) -> None:
        self._states.pop(str(drone_id), None)
