"""Deterministic Sprint 4 demonstration scenario (8 drones).

Local frame = metres east/north of HQ; all drones cruise at 8 m/s.

    T+0   8 drones online, holding at their start points (telemetry flowing)
    T+5   drones start their planned legs
          DMX-001 <-> DMX-002  head-on in band 1 (planned contact ~T+19.6)
          DMX-003  x  DMX-004  crossing at (60, 100) in band 1 at the same time
          DMX-005..008         parallel / same-direction traffic in band 2
          -> ORCA predicts both conflicts at TTC ~10 s (alert >= 3 s ahead,
             ~T+9.6), applies avoidance inside its 5 s horizon, drones separate
    T+20  DMX-003 Wi-Fi degraded (-84 dBm)    -> Wi-Fi -> LTE
    T+30  DMX-003 LTE degraded   (-114 dBm)   -> LTE -> LoRa
    T+40  DMX-003 Wi-Fi recovered             -> LoRa -> Wi-Fi after the 3 s hold
Telemetry keeps flowing at 1 Hz through NATS into TimescaleDB throughout.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.core.logging import log_event
from app.models.drone import Drone
from app.services.comms.links import LinkType

if TYPE_CHECKING:  # pragma: no cover
    from app.services.runtime import FleetRuntime

logger = logging.getLogger("dronematrx.demo")

SPEED_MPS = 8.0
LAUNCH_AT_S = 5.0

# name -> (start (x, y), goal (x, y), cruise band)
LEGS: dict[str, tuple[tuple[float, float], tuple[float, float], int]] = {
    "DMX-001": ((-120.0, 0.0), (180.0, 0.0), 1),
    "DMX-002": ((120.0, 1.5), (-180.0, 1.5), 1),
    "DMX-003": ((60.0, -16.8), (60.0, 180.0), 1),
    "DMX-004": ((-56.8, 100.0), (180.0, 100.0), 1),
    "DMX-005": ((-150.0, -150.0), (150.0, -150.0), 2),
    "DMX-006": ((150.0, -175.0), (-150.0, -175.0), 2),
    "DMX-007": ((-150.0, 230.0), (150.0, 230.0), 2),
    "DMX-008": ((-150.0, 250.0), (150.0, 250.0), 2),
}

COMMS_SCRIPT = (
    (20.0, "DMX-003", "degrade", LinkType.WIFI, -84.0),
    (30.0, "DMX-003", "degrade", LinkType.LTE, -114.0),
    (40.0, "DMX-003", "restore", LinkType.WIFI, None),
)


@dataclass
class DemoStatus:
    running: bool = False
    started_at: float | None = None
    timeline: list[dict[str, Any]] | None = None

    def as_dict(self) -> dict[str, Any]:
        elapsed = time.time() - self.started_at if self.running and self.started_at else None
        return {"running": self.running, "elapsed_s": round(elapsed, 1) if elapsed is not None else None, "timeline": self.timeline or []}


class DemoController:
    def __init__(self, runtime: "FleetRuntime"):
        self.runtime = runtime
        self.status = DemoStatus()
        self._task: asyncio.Task | None = None
        self._drone_ids: dict[str, str] = {}

    def _log(self, step: str, **details: Any) -> None:
        entry = {"t": round(time.time() - (self.status.started_at or time.time()), 2), "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "step": step, **details}
        self.status.timeline.append(entry)  # type: ignore[union-attr]
        log_event(logger, "demo.step", step=step, **{k: v for k, v in details.items() if not isinstance(v, (list, dict))})

    async def start(self) -> dict[str, Any]:
        if self.status.running:
            await self.stop()
        rt = self.runtime
        sim, orca, comms = rt.simulator, rt.orca, rt.comms
        def lookup() -> list:
            with rt.session_factory() as db:
                return [d.id for d in db.execute(select(Drone).where(Drone.name.in_(list(LEGS)))).scalars()]

        drone_ids = await asyncio.to_thread(lookup)  # never block the 10 Hz loop on SQL
        if len(drone_ids) < 2:
            raise RuntimeError("Demo needs the seeded DMX-001..DMX-008 drones (run scripts/seed.py)")
        missing = [d for d in drone_ids if str(d) not in {s.drone_id for s in sim.states}]
        if missing:
            sim.attach(await asyncio.to_thread(sim.load_states, missing))
        states = [s for s in sim.states if s.drone_id in {str(d) for d in drone_ids}]
        by_name = {s.name: s for s in states}
        frame = orca.frame
        for name, (start, goal, band) in LEGS.items():
            s = by_name.get(name)
            if s is None:
                continue
            s.lat, s.lon = frame.to_geo(*start)
            s.home_lat, s.home_lon = s.lat, s.lon
            s.waypoints = [frame.to_geo(*goal)]
            s.vx = s.vy = 0.0
            s.cruise_speed = SPEED_MPS
            s.mode = "hold"
            s.battery = max(s.battery, 80.0)
            s.status = "active"
            orca.rth.complete(s.drone_id)
            orca.bands.release(s.drone_id)
            orca.bands.assignments[s.drone_id] = band
            s.band = band
            s.target_altitude = s.altitude = orca.bands.altitude_for(band)
            comms.register(s.drone_id, s.uin, s.name)
            comms.environment.restore(s.drone_id, now=time.time())
            self._drone_ids[name] = s.drone_id
        self.status = DemoStatus(running=True, started_at=time.time(), timeline=[])
        self._log("drones_online", drones=sorted(by_name))
        self._task = asyncio.create_task(self._script(by_name))
        return self.status.as_dict()

    async def _script(self, by_name: dict[str, Any]) -> None:
        try:
            await asyncio.sleep(LAUNCH_AT_S)
            for name in LEGS:
                if name in by_name:
                    by_name[name].mode = "scenario"
            self._log("legs_started")
            started = self.status.started_at or time.time()
            for at, name, action, link, rssi in COMMS_SCRIPT:
                await asyncio.sleep(max(0.0, started + at - time.time()))
                drone_id = self._drone_ids.get(name)
                if drone_id is None:
                    continue
                env = self.runtime.comms.environment
                if action == "degrade":
                    env.degrade(drone_id, link, now=time.time(), rssi_dbm=rssi)
                    self._log("link_degraded", drone=name, link=link.value, rssi_dbm=rssi)
                else:
                    env.restore(drone_id, None, now=time.time())
                    self._log("link_restored", drone=name, link=link.value)
            await asyncio.sleep(10)
            self._log("scenario_complete")
        except asyncio.CancelledError:
            pass

    async def stop(self) -> dict[str, Any]:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        for s in self.runtime.simulator.states:
            if s.drone_id in self._drone_ids.values():
                s.mode = "patrol"
                s.waypoints = []
                s.cruise_speed = 6.5
                s.home_lat, s.home_lon = s.lat, s.lon
                s.pick_new_target()
                self.runtime.comms.environment.restore(s.drone_id, now=time.time())
        self.status.running = False
        return self.status.as_dict()
