"""Live ORCA runtime: called by the simulator's 10 Hz motion loop.

Each cycle (`filter`):
  1. supervision -- RTH requests (low battery / fault / comms), priority-queue
     grants, altitude-band assignment, landing and return to service;
  2. build AgentState for every flying drone in local metres (GeoFrame);
  3. OrcaEngine.step -> safe velocity per drone;
  4. timing instrumentation (achieved rate, compute time, missed cycles).

The simulator integrates the returned velocities and reports the pipeline
time back through `commit_tick`, which is what "resolution latency" measures
live (prediction -> ORCA -> safe velocity -> updated trajectory).
"""
from __future__ import annotations

import logging
import math
import statistics
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from app.core.logging import log_event
from app.services.orca.deconfliction import AltitudeBandManager, RTHPolicy, RTHPriorityQueue
from app.services.orca.engine import OrcaEngine
from app.services.orca.frames import GeoFrame
from app.services.orca.models import AgentState, OrcaEvent, OrcaParams

if TYPE_CHECKING:  # pragma: no cover
    from app.services.comms.manager import CommunicationManager

logger = logging.getLogger("dronematrx.orca.runtime")

RTH_BATTERY_PCT = 25.0
RECHARGED_PCT = 95.0
ARRIVE_M = 3.0
PRIORITY = {"rth": 3, "critical": 4}


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(pct / 100 * len(ordered)) - 1))]


class OrcaRuntime:
    def __init__(self, params: OrcaParams | None = None, frame: GeoFrame | None = None, comms: "CommunicationManager | None" = None,
                 rate_hz: float = 10.0):
        self.params = params or OrcaParams()
        self.engine = OrcaEngine(self.params)
        self.frame = frame or GeoFrame()
        self.comms = comms
        self.rate_hz = rate_hz
        self.bands = AltitudeBandManager()
        self.rth = RTHPriorityQueue(RTHPolicy())
        self.sim_time = 0.0
        self.decisions: dict[str, Any] = {}
        self.predictions: list[Any] = []
        self.pending_events: list[OrcaEvent] = []
        self.call_times: deque[float] = deque(maxlen=600)
        self.compute_ms: deque[float] = deque(maxlen=600)
        self.pipeline_ms: deque[float] = deque(maxlen=600)
        self.resolution_ms: deque[float] = deque(maxlen=200)
        self.missed_cycles = 0
        self.cycles = 0
        self._last_had_prediction_event = False

    # ------------------------------------------------------------ supervision

    def _comm_degraded(self, drone_id: str) -> bool:
        if self.comms is None or drone_id not in self.comms.drones:
            return False
        state = self.comms.drones[drone_id]
        return state.all_lost or state.active.value == "lora"

    def _emit(self, kind: str, drones: list[str], **details: Any) -> None:
        self.engine._emit(kind, self.sim_time, drones, self.pending_events, **details)

    def assign_band(self, state: Any, preferred: int | None = None) -> None:
        band = self.bands.assign_cruise(state.drone_id, preferred)
        if band is None:
            state.mode = "hold"
            self._emit("band_full_hold", [state.drone_id])
            return
        state.band = band
        state.target_altitude = self.bands.altitude_for(band)

    def supervise(self, states: list[Any]) -> None:
        for s in states:
            if s.band is None and s.mode in ("patrol", "scenario"):
                self.assign_band(s)
            flying = s.mode in ("patrol", "scenario")
            if flying and (s.battery <= RTH_BATTERY_PCT or s.status == "critical" or (self.comms and self.comms.drones.get(s.drone_id) and self.comms.drones[s.drone_id].all_lost)):
                reason = self.rth.classify(s.battery, emergency=s.status == "critical", comm_degraded=self._comm_degraded(s.drone_id))
                self.request_rth(s, reason)
        for drone_id in self.rth.grant():
            s = next((x for x in states if x.drone_id == drone_id), None)
            if s is None:
                continue
            s.mode = "rth"
            s.band = self.bands.assign_rth(drone_id)
            s.target_altitude = self.bands.altitude_for(0)
            self._emit("rth_granted", [s.drone_id], reason=self.rth.active[drone_id].reason, band=0)
        for s in states:
            if s.mode == "rth" and s.home_lat is not None and self._distance(s, s.home_lat, s.home_lon) <= ARRIVE_M:
                s.mode = "landed"
                s.target_altitude = 0.0
                self._emit("rth_landed", [s.drone_id])
            elif s.mode == "landed" and s.battery >= RECHARGED_PCT and s.drone_id in self.rth.active:
                self.rth.complete(s.drone_id)
                self.bands.release(s.drone_id)
                s.mode = "patrol"
                s.band = None
                self.assign_band(s)
                self._emit("rth_complete", [s.drone_id])

    def request_rth(self, s: Any, reason: str) -> None:
        if s.drone_id in self.rth.active or s.mode in ("rth", "landed"):
            return
        if self.rth.request(s.drone_id, reason, s.battery, self.sim_time) and s.mode != "hold":
            s.resume_mode = s.mode
            s.mode = "hold"  # wait in place until an RTH slot frees up
            self._emit("rth_queued", [s.drone_id], reason=reason, battery_pct=round(s.battery, 1))

    def _distance(self, s: Any, lat: float, lon: float) -> float:
        ax, ay = self.frame.to_local(s.lat, s.lon)
        bx, by = self.frame.to_local(lat, lon)
        return math.hypot(bx - ax, by - ay)

    # ------------------------------------------------------------- 10 Hz cycle

    def filter(self, states: list[Any], dt: float, preferred: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
        started = time.perf_counter()
        now = time.monotonic()
        if self.call_times and now - self.call_times[-1] > 1.5 / self.rate_hz:
            self.missed_cycles += 1
        self.call_times.append(now)
        self.cycles += 1
        self.sim_time += dt

        self.supervise(states)
        agents = []
        for s in states:
            x, y = self.frame.to_local(s.lat, s.lon)
            mode_priority = PRIORITY["critical"] if s.status == "critical" else PRIORITY["rth"] if s.mode == "rth" else 0
            agents.append(
                AgentState(
                    id=s.drone_id,
                    position=(x, y),
                    velocity=(s.vx, s.vy),
                    preferred_velocity=preferred.get(s.drone_id, (0.0, 0.0)),
                    altitude=s.altitude,
                    max_speed=s.max_speed,
                    heading=s.heading,
                    status="landed" if s.mode == "landed" else "active",
                    priority=mode_priority,
                    comm_degraded=self._comm_degraded(s.drone_id),
                    uin=s.uin,
                    plan_horizon_s=s.plan_horizon_s,
                )
            )
        result = self.engine.step(agents, self.sim_time)
        self.decisions = result.decisions
        self.predictions = result.predictions
        self.pending_events.extend(result.events)
        self._last_had_prediction_event = any(e.kind == "conflict_predicted" for e in result.events)
        self.compute_ms.append((time.perf_counter() - started) * 1000)
        return {drone_id: d.safe_velocity for drone_id, d in result.decisions.items()}

    def commit_tick(self, pipeline_ms: float) -> None:
        self.pipeline_ms.append(pipeline_ms)
        if self._last_had_prediction_event:
            self.resolution_ms.append(pipeline_ms)
            log_event(logger, "orca.resolution", logging.DEBUG, ms=round(pipeline_ms, 3))

    def drain_events(self) -> list[OrcaEvent]:
        events, self.pending_events = self.pending_events, []
        return events

    # ------------------------------------------------------------- reporting

    def timing(self) -> dict[str, Any]:
        calls = list(self.call_times)
        intervals = [(b - a) * 1000 for a, b in zip(calls, calls[1:])]
        compute = list(self.compute_ms)
        return {
            "target_hz": self.rate_hz,
            "measured_hz": round(1000.0 / statistics.fmean(intervals), 2) if intervals else None,
            "cycles": self.cycles,
            "missed_cycles": self.missed_cycles,
            "interval_ms_avg": round(statistics.fmean(intervals), 2) if intervals else None,
            "interval_ms_max": round(max(intervals), 2) if intervals else None,
            "compute_ms_avg": round(statistics.fmean(compute), 3) if compute else None,
            "compute_ms_p95": round(_percentile(compute, 95), 3),
            "compute_ms_max": round(max(compute), 3) if compute else None,
            "resolution_ms_avg": round(statistics.fmean(self.resolution_ms), 3) if self.resolution_ms else None,
            "resolution_ms_max": round(max(self.resolution_ms), 3) if self.resolution_ms else None,
        }

    def event_dict(self, event: OrcaEvent) -> dict[str, Any]:
        """Event as JSON, with the engine's local-metre point also given as lat/lon."""
        data = event.as_dict()
        point = data["details"].get("point")
        if isinstance(point, (list, tuple)) and len(point) == 2:
            data["details"] = {**data["details"], "point_geo": list(self.frame.to_geo(point[0], point[1]))}
        return data

    def drone_state(self, s: Any) -> dict[str, Any]:
        d = self.decisions.get(s.drone_id)
        preds = []
        for p in self.predictions:
            if s.drone_id in (p.a, p.b):
                lat, lon = self.frame.to_geo(*p.point)
                preds.append({"other": p.b if p.a == s.drone_id else p.a, "ttc_s": round(p.time_to_collision_s, 2), "point": [lat, lon]})
        return {
            "drone_id": s.drone_id,
            "uin": s.uin,
            "name": s.name,
            "lat": round(s.lat, 7),
            "lon": round(s.lon, 7),
            "altitude": round(s.altitude, 1),
            "band": s.band,
            "mode": s.mode,
            "velocity": [round(s.vx, 3), round(s.vy, 3)],
            "preferred_velocity": [round(v, 3) for v in d.preferred_velocity] if d else [0.0, 0.0],
            "safe_velocity": [round(v, 3) for v in d.safe_velocity] if d else [0.0, 0.0],
            "avoidance_vector": [round(v, 3) for v in d.avoidance_vector] if d else [0.0, 0.0],
            "neighbors": d.neighbors if d else [],
            "constraints": len(d.constraints) if d else 0,
            "collision_predicted": bool(preds),
            "predictions": preds,
            "battery": round(s.battery, 1),
            "sim_time_s": round(self.sim_time, 2),
        }

    def snapshot(self, states: list[Any]) -> dict[str, Any]:
        names = {s.drone_id: s.name for s in states}
        return {
            "drones": [self.drone_state(s) for s in sorted(states, key=lambda x: x.name)],
            "predictions": [
                {
                    "a": names.get(p.a, p.a), "b": names.get(p.b, p.b), "a_id": p.a, "b_id": p.b,
                    "ttc_s": round(p.time_to_collision_s, 2), "point": list(self.frame.to_geo(*p.point)),
                }
                for p in self.predictions
            ],
            "rth_queue": self.rth.snapshot(),
            "bands": {str(k): v for k, v in sorted(self.bands.occupancy().items())},
            "band_altitudes": {str(k): self.bands.altitude_for(k) for k in range(self.bands.cruise_bands + 1)},
            "timing": self.timing(),
            "params": self.params.__dict__,
            "events": [self.event_dict(e) for e in self.engine.recent_events(limit=60)],
        }
