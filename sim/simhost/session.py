"""One simulation session inside the simhost: Gazebo world + N PX4 SITL
instances + N MAVLink bridges, with telemetry/events pushed to NATS.

FPS guardrail (documented in docs/sprint5/SIMULATION.md):
    sim_fps = 30 * achieved_real_time_factor / requested_speed
i.e. a nominal 30 frames of simulated motion per second of wall time at the
requested speed. If sim_fps < 10 (the world advances at less than a third
of the requested speed) for 3 consecutive 1 s samples, the requested speed
steps down one level (10x -> 5x -> 2x -> 1x) and a `guardrail` alert is
published. At 1x the guardrail only warns.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from simhost.bridge import BridgeError, DroneLink, LinkEmulator
from enu import EnuFrame
from simhost.gazebo import GazeboError, GazeboWorld
from simhost.sitl_manager import SitlProcessManager

logger = logging.getLogger("simhost.session")

SPEED_LEVELS = (1.0, 2.0, 5.0, 10.0)
GUARDRAIL_FPS = 10.0
NOMINAL_FPS = 30.0
GUARDRAIL_SAMPLES = 3
# A model this far from the world origin has left the generated terrain for
# good; it is removed before the physics engine overflows (ODE AABB assert).
RUNAWAY_LIMIT_M = 20_000.0
LINK_LOSS_TO_NAV_DLL_ACT = {"CONTINUE": 0, "HOLD": 1, "RTH": 2, "LAND": 3}


class SessionError(Exception):
    pass


@dataclass
class DroneSpec:
    uin: str
    drone_id: str
    name: str
    pose: str  # Gazebo "x,y,z,0,0,yaw"


@dataclass
class SessionSpec:
    session_id: str
    world_sdf: str
    world_name: str
    origin: dict  # {"latitude", "longitude", "height_m"}
    drones: list[DroneSpec]
    speed: float = 1.0
    link_loss_policy: str = "RTH"
    seed: int = 1
    cruise_speed_mps: float = 8.0
    battery_drain_s: float = 1800.0
    params: dict = field(default_factory=dict)


class SimSession:
    def __init__(self, spec: SessionSpec, manager: SitlProcessManager, workdir: Path, publish: Callable[[str, str, dict], None]):
        self.spec = spec
        self.manager = manager
        self.workdir = Path(workdir)
        self.publish = publish  # (uin, kind, payload) -> None ; thread-safe
        self.world = GazeboWorld(Path(spec.world_sdf), spec.world_name, self.workdir / "gz_server.log")
        self.frame = EnuFrame(spec.origin["latitude"], spec.origin["longitude"], spec.origin.get("height_m", 0.0))
        self.links: dict[str, DroneLink] = {}
        self.index_of: dict[str, int] = {}
        self.state = "created"
        self.error: str | None = None
        self.requested_speed = spec.speed
        self.guardrail = {"active": False, "low_samples": 0, "events": []}
        self.obstacles: list[dict] = []
        self.failures: list[dict] = []
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.started_wall: float | None = None

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Start world + fleet. If no PX4 instance produced a single heartbeat
        (a start-up hang, not a flight failure), retry the whole start once;
        the attempt count is reported in status()."""
        self.startup_attempts = getattr(self, "startup_attempts", 0) + 1
        try:
            self._start_once()
        except SessionError as exc:
            no_heartbeat = all(not l.state.last_heartbeat for l in self.links.values()) if self.links else False
            if self.startup_attempts >= 2 or not no_heartbeat:
                raise
            logger.warning("session %s: no PX4 heartbeat on attempt 1 (%s); retrying start once", self.spec.session_id, exc)
            self.startup_errors = [str(exc)]
            self.manager.forget_session(self.spec.session_id)
            self.links, self.index_of = {}, {}
            self._stop.clear()
            self.world = GazeboWorld(Path(self.spec.world_sdf), self.spec.world_name, self.workdir / "gz_server_retry.log")
            self.state, self.error = "created", None
            self.start()

    def _start_once(self) -> None:
        self.state = "starting"
        try:
            self.workdir.mkdir(parents=True, exist_ok=True)
            self.world.start()
            self.world.set_real_time_factor(1.0)  # warm-up at 1x; the requested speed applies once ready
            indices = self.manager.allocate_indices(self.spec.session_id, len(self.spec.drones))
            for idx, d in zip(indices, self.spec.drones):
                inst = self.manager.start(self.spec.session_id, idx, d.uin, d.pose, self.spec.world_name, self.workdir, extra_env=self.spec.params.get("env"))
                emulator = LinkEmulator(seed=self.spec.seed * 1000 + idx)
                link = DroneLink(self.spec.session_id, idx, d.uin, d.drone_id, d.name, inst.api_port, emulator,
                                 on_event=self._event_cb(d.uin), truth=self._truth_cb(inst.gz_model_name))
                link.open()
                self.links[d.uin] = link
                self.index_of[d.uin] = idx
                time.sleep(1.0)  # stagger PX4 model spawns in Gazebo
            errors: list[str] = []

            def ready(uin: str, link: DroneLink) -> None:
                try:
                    link.wait_ready(timeout_s=180.0)
                    self._configure(link)
                    self.manager.mark_running(self.spec.session_id, self.index_of[uin])
                except Exception as exc:  # noqa: BLE001 - reported per drone
                    errors.append(f"{uin}: {exc}")

            threads = [threading.Thread(target=ready, args=(u, l)) for u, l in self.links.items()]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            if errors:
                raise SessionError("; ".join(errors))
            for target, name in ((self._telemetry_loop, "telemetry"), (self._monitor_loop, "monitor")):
                th = threading.Thread(target=target, name=f"sess-{name}", daemon=True)
                th.start()
                self._threads.append(th)
            if self.requested_speed != 1.0:
                self.world.set_real_time_factor(self.requested_speed)
            self.state = "ready"
            self.started_wall = time.time()
        except (GazeboError, SessionError, BridgeError, Exception) as exc:
            self.state = "failed"
            self.error = str(exc)
            logger.exception("session start failed")
            self.stop()
            raise SessionError(str(exc)) from exc

    def _configure(self, link: DroneLink) -> None:
        sp = self.spec
        params = {
            "NAV_DLL_ACT": LINK_LOSS_TO_NAV_DLL_ACT.get(sp.link_loss_policy, 2),
            "COM_DL_LOSS_T": 10,
            "COM_RC_IN_MODE": 4,  # no RC in simulation; MAVLink control only
            "NAV_RCL_ACT": 0,
            "MPC_XY_CRUISE": sp.cruise_speed_mps,
            "SIM_BAT_DRAIN": sp.battery_drain_s,
            "SIM_BAT_MIN_PCT": 5.0,
            # The simulated battery has no voltage sag; PX4's throttle-based sag
            # compensation would otherwise over-read the charge in flight (~+17 %).
            "BAT1_V_LOAD_DROP": 0.0,
            "COM_LOW_BAT_ACT": 3,  # warning -> RTL -> land, by battery thresholds
            **{k: v for k, v in sp.params.items() if k != "env"},
        }
        for name, value in params.items():
            if value is None:
                continue
            try:
                link.set_param(name, value)
            except BridgeError as exc:
                logger.warning("param %s on %s not set: %s", name, link.uin, exc)

    def stop(self) -> dict:
        self._stop.set()
        for link in self.links.values():
            link.close()
        stopped = self.manager.stop_session(self.spec.session_id)
        self.world.stop()
        if self.state not in ("failed",):
            self.state = "stopped"
        return {"sitl_stopped": stopped}

    # -------------------------------------------------------------- helpers

    def _truth_cb(self, model: str):
        def truth() -> dict | None:
            pose = self.world.model_pose(model)
            if pose is None:
                return None
            lat, lon, h = self.frame.to_geodetic(*pose)
            return {"lat": lat, "lon": lon, "alt_msl": h, "x": round(pose[0], 3), "y": round(pose[1], 3), "z": round(pose[2], 3)}

        return truth

    def _event_cb(self, uin: str):
        def cb(kind: str, data: dict) -> None:
            link = self.links.get(uin)
            payload = {"type": "sim", "kind": kind, "session_id": self.spec.session_id, "uin": uin,
                       "drone_id": link.drone_id if link else None, "sim_time_s": self.sim_time(), "wall": time.time(), **data}
            if link is None:
                self.publish(uin, "alert", payload)
            else:
                link.emulator.deliver(lambda: self.publish(uin, "alert", payload))

        return cb

    def process_exited(self, uin: str, returncode: int, expected: bool) -> None:
        """Called from the SITL monitor thread. Not subject to the radio link:
        the simulator itself knows the process died."""
        link = self.links.get(uin)
        if link and not expected:
            link.close()
        if not expected and uin in self.index_of and self.world.server_exit_code() is None:
            # Dead flight controller -> motors stop and the drone falls, as a
            # real one would; Gazebo would otherwise hold its last thrust.
            threading.Thread(target=self._cut_motors, args=(f"x500_{self.index_of[uin]}",), daemon=True).start()
        payload = {"type": "sim", "kind": "process_exited", "session_id": self.spec.session_id, "uin": uin,
                   "drone_id": link.drone_id if link else None, "returncode": returncode, "expected": expected,
                   "sim_time_s": self.sim_time(), "wall": time.time()}
        self.publish(uin, "alert", payload)

    def _cut_motors(self, model: str) -> None:
        try:
            self.world.stop_motors(model)
        except Exception as exc:  # pragma: no cover - logged, never fatal
            logger.warning("could not stop motors of %s: %s", model, exc)

    def sim_time(self) -> float | None:
        return self.world.stats.get("sim_time_s")

    def _check_world(self) -> None:
        """Gazebo server death and runaway models (called once a second)."""
        rc = self.world.server_exit_code()
        if rc is not None and self.state in ("ready", "running", "paused"):
            self.state = "failed"
            self.error = f"Gazebo server exited (code {rc}); see {self.world.log_path}"
            logger.error(self.error)
            first = next(iter(self.links), None)
            if first:
                self.publish(first, "alert", {"type": "sim", "kind": "simulator_failed", "session_id": self.spec.session_id,
                                              "uin": first, "error": self.error, "sim_time_s": self.sim_time(), "wall": time.time()})
            return
        for uin, index in list(self.index_of.items()):
            model = f"x500_{index}"
            pose = self.world.model_pose(model)
            if pose is None or all(abs(c) < RUNAWAY_LIMIT_M for c in pose):
                continue
            try:
                self.world.remove_entity(model)
            except GazeboError as exc:
                logger.warning("could not remove runaway %s: %s", model, exc)
                continue
            logger.warning("removed runaway model %s at %s", model, pose)
            self.publish(uin, "alert", {"type": "sim", "kind": "model_removed", "session_id": self.spec.session_id, "uin": uin,
                                        "reason": f"left the world ({RUNAWAY_LIMIT_M / 1000:g} km limit)", "sim_time_s": self.sim_time(), "wall": time.time()})

    def _telemetry_loop(self) -> None:
        while not self._stop.is_set():
            st = self.sim_time()
            for uin, link in list(self.links.items()):
                if link.closed or not link.state.last_rx:
                    continue
                payload = link.telemetry(st)
                if payload["latitude"] is None:
                    continue
                link.emulator.deliver(lambda u=uin, p=payload: self.publish(u, "telemetry", p))
            self._stop.wait(0.5)

    def sim_fps(self) -> float | None:
        rtf = self.world.achieved_rtf()
        if rtf is None or self.world.stats.get("paused"):
            return None
        return min(NOMINAL_FPS, NOMINAL_FPS * rtf / max(self.requested_speed, 1e-6))

    def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(1.0)
            self._check_world()
            if self.state == "failed":
                return
            fps = self.sim_fps()
            if fps is None:
                continue
            g = self.guardrail
            g["fps"] = round(fps, 1)
            if fps < GUARDRAIL_FPS:
                g["low_samples"] += 1
            else:
                g["low_samples"] = 0
                g["active"] = False
            if g["low_samples"] >= GUARDRAIL_SAMPLES:
                g["low_samples"] = 0
                g["active"] = True
                previous = self.requested_speed
                lower = max([s for s in SPEED_LEVELS if s < previous], default=None)
                action = "warn_only"
                if lower is not None:
                    self.set_speed(lower)
                    action = f"speed {previous:g}x -> {lower:g}x"
                event = {"fps": round(fps, 1), "requested_speed": previous, "achieved_rtf": round(self.world.achieved_rtf() or 0, 3), "action": action, "wall": time.time()}
                g["events"].append(event)
                first = next(iter(self.links), None)
                if first:
                    self.publish(first, "alert", {"type": "sim", "kind": "guardrail", "session_id": self.spec.session_id, "uin": first,
                                                  "sim_time_s": self.sim_time(), **event})

    # ------------------------------------------------------------- controls

    def set_speed(self, factor: float) -> dict:
        if factor not in SPEED_LEVELS:
            raise SessionError(f"Speed must be one of {SPEED_LEVELS}")
        ok = self.world.set_real_time_factor(factor)
        if not ok:
            raise SessionError("Gazebo rejected the physics update")
        self.requested_speed = factor
        return {"speed": factor}

    def pause(self, paused: bool) -> dict:
        if not self.world.pause(paused):
            raise SessionError("Gazebo rejected the world control request")
        self.state = "paused" if paused else "running"
        return {"paused": paused, "sim_time_s": self.sim_time()}

    def link(self, uin: str) -> DroneLink:
        link = self.links.get(uin)
        if link is None:
            raise SessionError(f"Unknown drone {uin}")
        return link

    def status(self) -> dict:
        return {
            "session_id": self.spec.session_id,
            "state": self.state,
            "error": self.error,
            "world_name": self.spec.world_name,
            "stats": self.world.stats,
            "achieved_rtf": self.world.achieved_rtf(),
            "requested_speed": self.requested_speed,
            "sim_fps": self.sim_fps(),
            "guardrail": self.guardrail,
            "instances": self.manager.health(self.spec.session_id),
            "drones": {
                uin: {
                    "ready": l.ready(),
                    "closed": l.closed,
                    "mode": l.state.mode,
                    "armed": l.state.armed,
                    "battery_pct": l.state.battery_pct,
                    "mission_current": l.state.mission_current,
                    "mission_count": l.state.mission_count,
                    "reached": sorted(l.state.reached),
                    "link": vars(l.emulator.config),
                    "link_stats": l.emulator.stats,
                    "gps_drift": l.gps_drift,
                    "truth": l.truth() if l.truth else None,
                    "reported": {"lat": l.state.lat, "lon": l.state.lon, "alt_msl": l.state.alt_msl},
                }
                for uin, l in self.links.items()
            },
            "obstacles": self.obstacles,
            "failures": self.failures,
            "startup_attempts": getattr(self, "startup_attempts", 1),
            "startup_errors": getattr(self, "startup_errors", []),
        }
