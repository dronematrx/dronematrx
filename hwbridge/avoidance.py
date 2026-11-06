"""ORCA collision avoidance for REAL aircraft, with the same engine, parameters
and priorities as the Drone MatrX simulator (backend/app/services/orca: the code
is imported, not copied).

Every cycle (10 Hz):
  1. every aircraft the bridge hears is an agent: position and velocity from
     GLOBAL_POSITION_INT, in the engine's local metric frame (GeoFrame);
  2. preferred velocity = towards where Drone MatrX is sending it (current
     mission waypoint, or the go-to target) at mission speed. Aircraft that
     Drone MatrX is not flying (pilot in control, RTL, LAND, failsafes, other
     ground stations) keep their own velocity and get the simulator's RTH /
     critical priority: the others yield to them;
  3. OrcaEngine.step -> safe velocity per aircraft (as in the simulator);
     The simulator's guidance layer is applied too: a sub-degree heading
     bias per aircraft and DeadlockPolicy (nearly stopped while constrained
     and far from the goal for 2 s -> preferred direction turned 60 deg to the
     right for 3 s), ORCA's known fix for symmetric deadlocks such as an
     aircraft hovering exactly on the path;
  4. an aircraft Drone MatrX is flying whose safe velocity departs from its
     preferred one is switched to GUIDED and fed the safe velocity at 10 Hz;
     after CLEAR_HOLD_S without conflict it resumes AUTO (ArduPilot continues
     the same waypoint) or its go-to.

Safety: the RC pilot still wins (a mode change outside Drone MatrX sets the
override lock and ORCA never steers that aircraft again until it is
released); only aircraft with a valid flight permission are steered; nothing
below 3 m is steered (take-off, landing); if this process stops, ArduCopter
stops on its own when the velocity setpoints stop and the GCS failsafe
returns it home.
"""
from __future__ import annotations

import logging
import math
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.orca.engine import AVOIDANCE_EPSILON_MPS, OrcaEngine  # noqa: E402
from app.services.orca.frames import GeoFrame  # noqa: E402
from app.services.orca.models import AgentState, OrcaParams  # noqa: E402
from app.services.orca.runtime import PRIORITY  # noqa: E402
from app.services.orca.simulation import DeadlockPolicy  # noqa: E402

from hwbridge.ardupilot import Vehicle  # noqa: E402

logger = logging.getLogger("hwbridge.avoidance")

ENGAGE_MPS = 0.5          # take over when the safe velocity departs this much from the preferred one
CLEAR_HOLD_S = 2.0        # ...and give back after this long without deviation
DEFAULT_SPEED_MPS = 5.0   # when the mission did not set one
NOT_FLOWN_BY_US = ("RTL", "LAND", "SMART_RTL", "BRAKE", "LOITER", "ALT_HOLD", "STABILIZE", "POSHOLD")


class HardwareOrca:
    def __init__(self, vehicles: Callable[[], dict[str, Vehicle]], params: OrcaParams | None = None, rate_hz: float = 10.0,
                 on_event: Callable[[Vehicle | None, str, dict], None] | None = None, enabled: bool = True):
        self.vehicles = vehicles
        self.params = params or OrcaParams()
        self.engine = OrcaEngine(self.params)
        self.rate_hz = rate_hz
        self.on_event = on_event or (lambda v, k, d: None)
        self.enabled = enabled
        self.deadlock = DeadlockPolicy()
        self.deadlock_detours = 0
        self._stuck_since: dict[str, float] = {}
        self._detour_until: dict[str, float] = {}
        self._index: dict[str, int] = {}
        self.frame: GeoFrame | None = None
        self.decisions: dict = {}
        self.predictions: list = []
        self.cycle_ms: deque[float] = deque(maxlen=300)
        self.cycles = 0
        self.errors = 0
        self.last_error: str | None = None
        self._t0 = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="orca", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:  # hand every aircraft back to what it was doing
            for v in self.vehicles().values():
                if v.orca_engaged:
                    v.orca_release()

    def _loop(self) -> None:
        period = 1.0 / self.rate_hz
        while not self._stop.is_set():
            started = time.monotonic()
            if self.enabled:
                try:
                    self.cycle()
                except Exception as exc:  # noqa: BLE001 - never let one bad cycle stop avoidance
                    self.errors += 1
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    logger.exception("ORCA cycle failed")
            self._stop.wait(max(0.0, period - (time.monotonic() - started)))

    # --------------------------------------------------------------- cycle

    def _agent(self, v: Vehicle, frame: GeoFrame) -> tuple[AgentState, bool]:
        s = v.state
        x, y = frame.to_local(s.lat, s.lon)
        velocity = (s.vy, s.vx)  # MAVLink vx = north, vy = east -> engine (east, north)
        ours = v.orca_controllable()
        preferred = velocity
        plan_horizon = None
        if ours:
            target = v.orca_target()
            if target is None:
                preferred = (0.0, 0.0)
            else:
                tx, ty = frame.to_local(*target)
                dx, dy = tx - x, ty - y
                dist = math.hypot(dx, dy)
                speed = v.mission_speed or max(DEFAULT_SPEED_MPS, s.groundspeed)
                if v.orca_resume == "GOTO" or (s.mode == "GUIDED" and not v.orca_engaged):
                    speed = max(DEFAULT_SPEED_MPS, s.groundspeed)
                speed = min(speed, dist)  # slow down onto the target instead of overshooting it
                # The simulator's guidance: sub-degree bias per aircraft (breaks perfect
                # symmetry), plus a 60 deg turn to the right while a deadlock detour runs.
                bias = math.radians(0.5) * ((self._index.get(v.fc_id, 0) % 3) - 1)
                if time.monotonic() < self._detour_until.get(v.fc_id, -1.0):
                    bias -= math.radians(self.deadlock.detour_deg)
                ux, uy = (dx / dist, dy / dist) if dist > 0 else (0.0, 0.0)
                preferred = ((speed * (ux * math.cos(bias) - uy * math.sin(bias)), speed * (ux * math.sin(bias) + uy * math.cos(bias)))
                             if dist > 0.5 else (0.0, 0.0))
                plan_horizon = dist / speed if speed > 0.1 else None
        airborne = s.armed and s.rel_alt >= 1.0
        priority = 0
        if not ours and airborne:
            # Not flown by Drone MatrX (pilot, failsafe, RTL/LAND): like the simulator's
            # RTH / critical drones, the others yield to it.
            priority = PRIORITY["critical"] if v.override is not None else PRIORITY["rth"]
        agent = AgentState(
            id=v.fc_id, position=(x, y), velocity=velocity, preferred_velocity=preferred, altitude=s.rel_alt,
            status="active" if airborne else "landed", priority=priority, uin=v.uin, plan_horizon_s=plan_horizon,
            max_speed=max(self.params.max_speed_mps if not ours else (v.mission_speed or DEFAULT_SPEED_MPS) * 1.5, 1.0),
        )
        return agent, ours

    def cycle(self) -> None:
        started = time.perf_counter()
        fleet = {fc: v for fc, v in self.vehicles().items()
                 if v.state.lat is not None and v.state.last_heartbeat and time.time() - v.state.last_heartbeat < 5}
        for v in self.vehicles().values():  # someone else took the aircraft: stop considering it ours
            if v.orca_engaged and (v.state.mode != "GUIDED" or v.override is not None or not v.state.armed):
                v.orca_engaged, v.orca_resume = False, None
        if not fleet:
            return
        if self.frame is None:
            first = next(iter(fleet.values()))
            origin = first.state.home or (first.state.lat, first.state.lon, 0.0)
            self.frame = GeoFrame(origin_lat=origin[0], origin_lon=origin[1])
        self._index = {fc: n for n, fc in enumerate(sorted(fleet))}
        agents, ours = [], {}
        for fc, v in fleet.items():
            a, mine = self._agent(v, self.frame)
            agents.append(a)
            ours[fc] = mine
        result = self.engine.step(agents, time.monotonic() - self._t0)
        self.decisions, self.predictions = result.decisions, result.predictions
        for event in result.events:
            for fc in event.drones:
                if fc in fleet:
                    self.on_event(fleet[fc], "orca", event.as_dict())

        now = time.monotonic()
        for fc, v in fleet.items():
            if not ours[fc]:
                self._stuck_since.pop(fc, None)
                continue
            d = result.decisions[fc]
            self._update_deadlock(v, d, now)
            deviation = math.hypot(*d.avoidance_vector)
            if not v.orca_engaged:
                if deviation > ENGAGE_MPS:
                    resume = "AUTO" if v.state.mode == "AUTO" else "GOTO"
                    if v.orca_engage(resume):
                        v.orca_velocity(d.safe_velocity[1], d.safe_velocity[0])
                continue
            v.orca_velocity(d.safe_velocity[1], d.safe_velocity[0])  # engine (east, north) -> (north, east)
            if deviation <= max(AVOIDANCE_EPSILON_MPS * 4, 0.2):
                v.orca_clear_since = v.orca_clear_since or now
                if now - v.orca_clear_since >= CLEAR_HOLD_S:
                    v.orca_release()
            else:
                v.orca_clear_since = None
        self.cycles += 1
        self.cycle_ms.append((time.perf_counter() - started) * 1000)

    def _update_deadlock(self, v: Vehicle, d, now: float) -> None:
        """DeadlockPolicy, exactly as the simulator applies it."""
        p = self.deadlock
        target = v.orca_target()
        if target is None or self.frame is None:
            self._stuck_since.pop(v.fc_id, None)
            return
        x, y = self.frame.to_local(v.state.lat, v.state.lon)
        tx, ty = self.frame.to_local(*target)
        cruise = v.mission_speed or DEFAULT_SPEED_MPS
        far = math.hypot(tx - x, ty - y) > p.min_goal_distance_m
        slow = math.hypot(v.state.vx, v.state.vy) < p.speed_fraction * cruise
        constrained = bool(d.neighbors) and math.hypot(*d.avoidance_vector) > AVOIDANCE_EPSILON_MPS
        if not far or not slow or not constrained or now < self._detour_until.get(v.fc_id, -1.0):
            self._stuck_since.pop(v.fc_id, None)
            return
        since = self._stuck_since.setdefault(v.fc_id, now)
        if now - since >= p.detect_s:
            self._detour_until[v.fc_id] = now + p.detour_s
            self._stuck_since.pop(v.fc_id, None)
            self.deadlock_detours += 1
            self.on_event(v, "orca", {"kind": "deadlock_detour", "drones": [v.fc_id], "details": {"detour_deg": p.detour_deg}})

    # ------------------------------------------------------------ reporting

    def status(self) -> dict:
        vs = self.vehicles()
        ms = list(self.cycle_ms)
        out = []
        for fc, v in vs.items():
            d = self.decisions.get(fc)
            out.append({"fc_id": fc, "uin": v.uin, "steered_by_orca": v.orca_engaged, "eligible": v.orca_controllable(),
                        "mode": v.state.mode, "resume": v.orca_resume,
                        "preferred_velocity": [round(x, 2) for x in d.preferred_velocity] if d else None,
                        "safe_velocity": [round(x, 2) for x in d.safe_velocity] if d else None,
                        "neighbors": d.neighbors if d else [], "collision_predicted": bool(d and d.collision_predicted)})
        return {"enabled": self.enabled, "rate_hz": self.rate_hz, "cycles": self.cycles, "deadlock_detours": self.deadlock_detours, "errors": self.errors, "last_error": self.last_error,
                "cycle_ms_avg": round(sum(ms) / len(ms), 3) if ms else None, "cycle_ms_max": round(max(ms), 3) if ms else None,
                "params": self.params.__dict__, "aircraft": out,
                "predictions": [{"a": p.a, "b": p.b, "ttc_s": round(p.time_to_collision_s, 2), "distance_m": round(p.distance_m, 1)}
                                for p in self.predictions],
                "events": [e.as_dict() for e in self.engine.recent_events(limit=40)]}
