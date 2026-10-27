"""MAVLink bridge between one PX4 SITL instance and Drone MatrX.

    PX4 (udp 127.0.0.1:14540+i, onboard/API link)
        <-> DroneLink (pymavlink; GCS heartbeat, commands, mission protocol)
        <-> LinkEmulator (latency / loss / link-down, deterministic per seed)
        <-> NATS JetStream  drone.{UIN}.telemetry | drone.{UIN}.alert

Telemetry comes from PX4 (GLOBAL_POSITION_INT, VFR_HUD, BATTERY_STATUS,
GPS_RAW_INT, HEARTBEAT, EXTENDED_SYS_STATE). The Gazebo ground-truth pose of
the same model is attached as `truth_*` so the true position and the
reported (estimated) position stay distinguishable -- that is how GPS drift
and the map-consistency tests are checked.
"""
from __future__ import annotations

import logging
import math
import queue
import random
import struct
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("simhost.bridge")

# PX4 custom modes: (main, sub) -> name
PX4_MODES = {
    (1, 0): "MANUAL", (2, 0): "ALTCTL", (3, 0): "POSCTL", (4, 1): "READY", (4, 2): "TAKEOFF", (4, 3): "HOLD",
    (4, 4): "MISSION", (4, 5): "RTL", (4, 6): "LAND", (4, 8): "FOLLOW", (4, 9): "PRECLAND", (5, 0): "ACRO",
    (6, 0): "OFFBOARD", (7, 0): "STABILIZED",
}
MODE_TO_PX4 = {"MISSION": (4, 4), "HOLD": (4, 3), "RTL": (4, 5), "LAND": (4, 6), "TAKEOFF": (4, 2), "POSCTL": (3, 0)}
INT_PARAMS = {"NAV_DLL_ACT", "COM_RC_IN_MODE", "COM_DL_LOSS_T", "COM_LOW_BAT_ACT", "SIM_BAT_ENABLE", "COM_DLL_EXCEPT",
              "COM_ARM_WO_GPS", "NAV_RCL_ACT", "MAV_SYS_ID", "COM_RCL_EXCEPT", "SIM_GPS_USED", "COM_OBL_RC_ACT", "COM_DISARM_LAND_DUMMY",
              "GF_ACTION"}


class BridgeError(Exception):
    pass


@dataclass
class LinkConfig:
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    loss_pct: float = 0.0
    down: bool = False

    def quality_pct(self) -> float:
        if self.down:
            return 0.0
        return round(max(0.0, 100.0 - self.loss_pct - min(40.0, self.latency_ms / 25.0)), 1)


class LinkEmulator:
    """Deterministic link model at the bridge <-> Drone MatrX boundary.

    The same seed produces the same drop pattern, so failure tests are
    repeatable. While `down`, nothing crosses in either direction and the
    GCS heartbeat to PX4 stops -- PX4 sees a real data-link loss.
    """

    def __init__(self, seed: int):
        self.config = LinkConfig()
        self.rng = random.Random(seed)
        self.stats = {"sent": 0, "dropped": 0, "delayed": 0}
        self._timers: list[threading.Timer] = []

    def configure(self, **kw) -> LinkConfig:
        for k, v in kw.items():
            if v is not None and hasattr(self.config, k):
                setattr(self.config, k, v)
        return self.config

    def admit(self) -> bool:
        if self.config.down:
            self.stats["dropped"] += 1
            return False
        if self.config.loss_pct > 0 and self.rng.random() * 100 < self.config.loss_pct:
            self.stats["dropped"] += 1
            return False
        return True

    def deliver(self, fn: Callable[[], None]) -> bool:
        if not self.admit():
            return False
        delay = (self.config.latency_ms + (self.rng.uniform(-1, 1) * self.config.jitter_ms if self.config.jitter_ms else 0)) / 1000.0
        self.stats["sent"] += 1
        if delay > 0.001:
            self.stats["delayed"] += 1
            t = threading.Timer(delay, fn)
            t.daemon = True
            t.start()
        else:
            fn()
        return True


@dataclass
class DroneState:
    armed: bool = False
    mode: str = "UNKNOWN"
    lat: float | None = None
    lon: float | None = None
    alt_msl: float | None = None
    rel_alt: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0
    heading: float = 0.0
    groundspeed: float = 0.0
    battery_pct: float | None = None
    gps_fix: int = 0
    satellites: int = 0
    landed_state: int = 0  # MAV_LANDED_STATE
    home: tuple[float, float, float] | None = None
    mission_current: int = -1
    mission_count: int = 0
    mission_gen: int = 0  # bumped on every accepted upload; stamps progress
    reached: set[int] = field(default_factory=set)
    last_rx: float = 0.0
    last_heartbeat: float = 0.0
    statustext: deque = field(default_factory=lambda: deque(maxlen=30))
    # Sprint 6: SYS_STATUS sensor bitmasks (MAV_SYS_STATUS_SENSOR) and firmware.
    sensors_present: int | None = None
    sensors_enabled: int | None = None
    sensors_health: int | None = None
    firmware: str | None = None


def _px4_mode(custom_mode: int) -> str:
    main = (custom_mode >> 16) & 0xFF
    sub = (custom_mode >> 24) & 0xFF
    return PX4_MODES.get((main, sub), PX4_MODES.get((main, 0), f"MODE_{main}_{sub}"))


class DroneLink:
    def __init__(
        self,
        session_id: str,
        index: int,
        uin: str,
        drone_id: str,
        name: str,
        port: int,
        emulator: LinkEmulator,
        on_event: Callable[[str, dict], None],
        truth: Callable[[], dict | None] | None = None,
    ):
        self.session_id = session_id
        self.index = index
        self.uin = uin
        self.drone_id = drone_id
        self.name = name
        self.port = port
        self.sysid = index + 1
        self.emulator = emulator
        self.on_event = on_event
        self.truth = truth
        self.state = DroneState()
        self.conn = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._acks: queue.Queue = queue.Queue()
        self._mission_q: queue.Queue = queue.Queue()
        self._params: dict[str, float] = {}
        self._param_evt = threading.Event()
        self.closed = False
        self.gps_drift: dict | None = None
        # Sprint 6 bench: an unbound flight controller publishes nothing; binding
        # routes its telemetry to a Drone MatrX drone record (pub_uin/drone_id).
        self.publish_enabled = True
        self.pub_uin = uin
        self.last_command_timing: dict = {}

    # ------------------------------------------------------------ lifecycle

    def open(self) -> None:
        from pymavlink import mavutil

        self.conn = mavutil.mavlink_connection(f"udpin:127.0.0.1:{self.port}", source_system=255, source_component=190, autoreconnect=True)
        for target, name in ((self._rx_loop, "rx"), (self._heartbeat_loop, "hb")):
            t = threading.Thread(target=target, name=f"mav-{self.uin}-{name}", daemon=True)
            t.start()
            self._threads.append(t)

    def close(self) -> None:
        self._stop.set()
        self.closed = True
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass

    def _heartbeat_loop(self) -> None:
        from pymavlink import mavutil

        while not self._stop.is_set():
            # GCS heartbeat; suppressed while the emulated link is down so PX4
            # detects the data-link loss and runs its failsafe (NAV_DLL_ACT).
            # PX4 times link loss in *simulated* time: at 10x a 1 Hz wall-clock
            # heartbeat is one every 10 sim-seconds, so send at 10 Hz wall.
            if self.conn is not None and not self.emulator.config.down:
                try:
                    self.conn.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
                except Exception:
                    pass
            self._stop.wait(0.1)

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                msg = self.conn.recv_match(blocking=True, timeout=0.5)
            except Exception:
                if self._stop.is_set():
                    return
                time.sleep(0.1)
                continue
            if msg is None:
                continue
            if msg.get_srcSystem() != self.sysid:
                continue
            self._handle(msg)

    # -------------------------------------------------------------- inbound

    def _handle(self, msg) -> None:
        s = self.state
        s.last_rx = time.time()
        t = msg.get_type()
        if t == "HEARTBEAT":
            if msg.autopilot == 12:  # MAV_AUTOPILOT_PX4
                armed = bool(msg.base_mode & 0x80)
                mode = _px4_mode(msg.custom_mode)
                if armed != s.armed:
                    self.on_event("armed" if armed else "disarmed", {})
                if mode != s.mode:
                    self.on_event("mode_changed", {"previous": s.mode, "mode": mode})
                s.armed, s.mode = armed, mode
                s.last_heartbeat = time.time()
        elif t == "GLOBAL_POSITION_INT":
            s.lat, s.lon = msg.lat / 1e7, msg.lon / 1e7
            s.alt_msl, s.rel_alt = msg.alt / 1000.0, msg.relative_alt / 1000.0
            s.vx, s.vy, s.vz = msg.vx / 100.0, msg.vy / 100.0, msg.vz / 100.0
            if msg.hdg != 65535:
                s.heading = msg.hdg / 100.0
        elif t == "VFR_HUD":
            s.groundspeed = msg.groundspeed
        elif t == "BATTERY_STATUS":
            if msg.battery_remaining >= 0:
                s.battery_pct = float(msg.battery_remaining)
        elif t == "SYS_STATUS":
            # PX4's onboard link streams SYS_STATUS (BATTERY_STATUS only on
            # request); both carry the battery simulator's state of charge.
            if msg.battery_remaining >= 0:
                s.battery_pct = float(msg.battery_remaining)
            s.sensors_present = msg.onboard_control_sensors_present
            s.sensors_enabled = msg.onboard_control_sensors_enabled
            s.sensors_health = msg.onboard_control_sensors_health
        elif t == "AUTOPILOT_VERSION":
            v = msg.flight_sw_version
            s.firmware = f"PX4 v{(v >> 24) & 0xFF}.{(v >> 16) & 0xFF}.{(v >> 8) & 0xFF}"
        elif t == "GPS_RAW_INT":
            s.gps_fix, s.satellites = msg.fix_type, msg.satellites_visible
        elif t == "EXTENDED_SYS_STATE":
            if msg.landed_state != s.landed_state:
                self.on_event("landed_state", {"previous": s.landed_state, "landed_state": msg.landed_state})
            s.landed_state = msg.landed_state
        elif t == "HOME_POSITION":
            s.home = (msg.latitude / 1e7, msg.longitude / 1e7, msg.altitude / 1000.0)
        elif t == "MISSION_CURRENT":
            if msg.seq != s.mission_current:
                s.mission_current = msg.seq
                self.on_event("mission_current", {"seq": msg.seq, "total": getattr(msg, "total", None)})
        elif t == "MISSION_ITEM_REACHED":
            if msg.seq not in s.reached:
                s.reached.add(msg.seq)
                self.on_event("waypoint_reached", {"seq": msg.seq, "mission_gen": s.mission_gen})
        elif t in ("MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"):
            self._mission_q.put(msg)
        elif t == "COMMAND_ACK":
            self._acks.put(msg)
        elif t == "PARAM_VALUE":
            self._params[msg.param_id] = msg.param_value
            self._param_evt.set()
        elif t == "STATUSTEXT":
            s.statustext.append((time.time(), msg.severity, msg.text))
            if msg.severity <= 4:  # warning or worse: failsafes, preflight failures
                self.on_event("statustext", {"severity": msg.severity, "text": msg.text})

    # -------------------------------------------------------------- outbound

    def _link_ok(self) -> None:
        if not self.emulator.admit():
            raise BridgeError("LINK_DOWN: command dropped by the emulated link")
        if self.emulator.config.latency_ms:
            time.sleep(self.emulator.config.latency_ms / 1000.0)

    def command(self, command: int, params: list[float], timeout_s: float = 5.0) -> int:
        self._link_ok()
        p = (params + [0.0] * 7)[:7]
        while not self._acks.empty():
            self._acks.get_nowait()
        sent = time.perf_counter()
        self.conn.mav.command_long_send(self.sysid, 1, command, 0, *p)
        return self._await_ack(command, sent, timeout_s)

    def command_int(self, command: int, frame: int, params: list[float], x: int, y: int, z: float, timeout_s: float = 5.0) -> int:
        self._link_ok()
        p = (params + [0.0] * 4)[:4]
        while not self._acks.empty():
            self._acks.get_nowait()
        sent = time.perf_counter()
        self.conn.mav.command_int_send(self.sysid, 1, frame, command, 0, 0, *p, x, y, z)
        return self._await_ack(command, sent, timeout_s)

    def _await_ack(self, command: int, sent: float, timeout_s: float) -> int:
        """Wait for PX4's COMMAND_ACK; record send/ack times (perf_counter, s)."""
        self.last_command_timing = {"command": command, "sent": sent, "ack": None}
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                ack = self._acks.get(timeout=0.05)
            except queue.Empty:
                continue
            if ack.command == command:
                self.last_command_timing["ack"] = time.perf_counter()
                return ack.result
        raise BridgeError(f"No COMMAND_ACK for command {command}")

    def set_mode(self, mode: str) -> int:
        main, sub = MODE_TO_PX4[mode]
        return self.command(176, [1, main, sub])  # MAV_CMD_DO_SET_MODE, custom mode enabled

    def arm(self, force: bool = False) -> int:
        return self.command(400, [1, 21196 if force else 0])

    def disarm(self) -> int:
        return self.command(400, [0, 0])

    def takeoff(self, rel_alt: float) -> int:
        home_alt = self.state.home[2] if self.state.home else (self.state.alt_msl or 0.0)
        return self.command(22, [0, 0, 0, float("nan"), float("nan"), float("nan"), home_alt + rel_alt])

    def goto(self, lat: float, lon: float, rel_alt: float) -> int:
        home_alt = self.state.home[2] if self.state.home else (self.state.alt_msl or 0.0)
        # MAV_CMD_DO_REPOSITION (192), GLOBAL frame; PX4 acknowledges it.
        return self.command_int(192, 0, [-1, 1, 0, float("nan")], int(lat * 1e7), int(lon * 1e7), home_alt + rel_alt)

    def request_firmware(self) -> None:
        try:  # MAV_CMD_REQUEST_MESSAGE(AUTOPILOT_VERSION); answered asynchronously
            self.conn.mav.command_long_send(self.sysid, 1, 512, 0, 148, 0, 0, 0, 0, 0, 0)
        except Exception:
            pass

    def get_param(self, name: str, timeout_s: float = 3.0) -> float | None:
        self._params.pop(name, None)
        self._param_evt.clear()
        self.conn.mav.param_request_read_send(self.sysid, 1, name.encode(), -1)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if name in self._params:
                got = self._params[name]
                return struct.unpack("<i", struct.pack("<f", got))[0] if name in INT_PARAMS else got
            self._param_evt.wait(0.1)
            self._param_evt.clear()
        return None

    def set_param(self, name: str, value: float, timeout_s: float = 5.0) -> float:
        from pymavlink import mavutil

        if name in INT_PARAMS:
            ptype = mavutil.mavlink.MAV_PARAM_TYPE_INT32
            raw = struct.unpack("<f", struct.pack("<i", int(value)))[0]  # PX4 bytewise encoding
        else:
            ptype, raw = mavutil.mavlink.MAV_PARAM_TYPE_REAL32, float(value)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self._params.pop(name, None)
            self._param_evt.clear()
            self.conn.mav.param_set_send(self.sysid, 1, name.encode(), raw, ptype)
            if self._param_evt.wait(1.0) and name in self._params:
                got = self._params[name]
                if name in INT_PARAMS:
                    got = struct.unpack("<i", struct.pack("<f", got))[0]
                return got
        raise BridgeError(f"PX4 did not acknowledge parameter {name}")

    def request_streams(self) -> None:
        # GLOBAL_POSITION_INT 10 Hz, BATTERY_STATUS 1 Hz, EXTENDED_SYS_STATE 2 Hz, HOME_POSITION 1 Hz, MISSION_CURRENT 2 Hz
        for msg_id, hz in ((33, 10), (147, 1), (245, 2), (242, 1), (42, 2), (24, 2), (74, 4)):
            try:
                self.conn.mav.command_long_send(self.sysid, 1, 511, 0, msg_id, 1e6 / hz, 0, 0, 0, 0, 0)
            except Exception:
                pass

    def upload_mission(self, items: list[dict], timeout_s: float = 20.0) -> int:
        """MAVLink mission protocol (MISSION_COUNT -> REQUEST_INT* -> ITEM_INT* -> ACK).

        items: {"command": int, "lat": deg, "lon": deg, "alt": m (relative to home),
                "param1".."param4": float}. Frame MAV_FRAME_GLOBAL_RELATIVE_ALT_INT.
        """
        from pymavlink import mavutil

        self._link_ok()
        while not self._mission_q.empty():
            self._mission_q.get_nowait()
        self.conn.mav.mission_count_send(self.sysid, 1, len(items), 0)
        deadline = time.time() + timeout_s
        sent = set()
        while time.time() < deadline:
            try:
                msg = self._mission_q.get(timeout=0.5)
            except queue.Empty:
                if not sent:
                    self.conn.mav.mission_count_send(self.sysid, 1, len(items), 0)
                continue
            if msg.get_type() == "MISSION_ACK":
                if msg.type == 0:
                    self.state.mission_count = len(items)
                    self.state.reached.clear()
                    self.state.mission_current = -1
                    self.state.mission_gen += 1
                    return len(items)
                raise BridgeError(f"Mission rejected by PX4 (MAV_MISSION_RESULT {msg.type})")
            seq = msg.seq
            it = items[seq]
            self.conn.mav.mission_item_int_send(
                self.sysid, 1, seq, it.get("frame", mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT), it["command"],
                1 if seq == 0 else 0, 1, it.get("param1", 0), it.get("param2", 0), it.get("param3", 0), it.get("param4", float("nan")),
                int(it.get("lat", 0) * 1e7), int(it.get("lon", 0) * 1e7), float(it.get("alt", 0)), 0,
            )
            sent.add(seq)
        raise BridgeError("Mission upload timed out")

    def arm_when_ready(self, timeout_s: float = 120.0) -> int:
        """Arm, retrying while PX4's preflight checks still fail (EKF
        converging, yaw estimate...). Returns MAV_RESULT_ACCEPTED or raises."""
        deadline = time.time() + timeout_s
        last = None
        while time.time() < deadline:
            if self.state.armed:
                return 0
            try:
                last = self.arm()
            except BridgeError as exc:
                last = str(exc)
            if last == 0:
                return 0
            time.sleep(2.0)
        raise BridgeError(f"{self.uin}: arming denied by PX4 preflight checks (last result {last})")

    def start_mission(self, timeout_s: float = 120.0) -> int:
        """Arm (when PX4 allows it) and switch to AUTO.MISSION; PX4 then flies
        the uploaded mission from item 0 (takeoff)."""
        self.arm_when_ready(timeout_s)
        # Always command the mode change (a stale heartbeat may still say
        # MISSION right after a HOLD) and accept only a heartbeat newer than it.
        deadline = time.time() + 20
        while time.time() < deadline:
            sent = time.time()
            try:
                self.set_mode("MISSION")
            except BridgeError:
                pass
            while time.time() - sent < 2.5:
                if self.state.mode == "MISSION" and self.state.last_heartbeat > sent:
                    return 0
                time.sleep(0.1)
        raise BridgeError(f"{self.uin}: PX4 did not enter MISSION mode (mode {self.state.mode})")

    # ------------------------------------------------------------ readiness

    def ready(self) -> bool:
        s = self.state
        return bool(s.last_heartbeat and s.gps_fix >= 3 and s.lat and s.home is not None)

    def wait_ready(self, timeout_s: float = 120.0) -> None:
        deadline = time.time() + timeout_s
        last_req = 0.0
        while time.time() < deadline and not self._stop.is_set():
            if time.time() - last_req > 3 and self.state.last_heartbeat:
                self.request_streams()
                last_req = time.time()
            if self.ready():
                self.request_streams()
                self.request_firmware()
                return
            time.sleep(0.3)
        raise BridgeError(f"{self.uin}: PX4 not ready (heartbeat={bool(self.state.last_heartbeat)}, fix={self.state.gps_fix}, home={self.state.home is not None})")

    # ------------------------------------------------------------ telemetry

    def telemetry(self, sim_time_s: float | None) -> dict:
        s = self.state
        truth = self.truth() if self.truth else None
        battery = s.battery_pct if s.battery_pct is not None else 100.0
        link = self.emulator.config
        status = "active" if s.armed else "online"
        if battery < 15:
            status = "critical"
        elif battery < 30 or link.loss_pct >= 20 or link.latency_ms >= 500:
            status = "warning"
        payload = {
            "drone_id": self.drone_id,
            "name": self.name,
            "uin": self.pub_uin,
            "latitude": s.lat,
            "longitude": s.lon,
            "altitude": round(s.rel_alt, 2),
            "battery": round(battery, 1),
            "heading": round(s.heading, 1),
            "speed": round(s.groundspeed or math.hypot(s.vx, s.vy), 2),
            "flight_mode": s.mode,
            "fc_id": self.uin,
            "gps_status": {3: "3D_FIX", 4: "3D_FIX", 5: "3D_FIX", 6: "3D_FIX", 2: "2D_FIX"}.get(s.gps_fix, "NO_FIX"),
            "armed": s.armed,
            "link_quality": link.quality_pct(),
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            # No radio link type: SITL telemetry is not Wi-Fi/LTE/LoRa traffic.
            "link_type": None,
            # Sprint 5 simulation extras (ignored by the generic telemetry path)
            "simulation": True,
            "session_id": self.session_id,
            "sim_time_s": sim_time_s,
            "alt_msl": s.alt_msl,
            "landed_state": s.landed_state,
            "mission_current": s.mission_current,
            "mission_count": s.mission_count,
            "mission_gen": s.mission_gen,
            "reached_max": max(s.reached) if s.reached else -1,
            "vx": s.vx, "vy": s.vy, "vz": s.vz,
            "satellites": s.satellites,
        }
        # Aliases used by the NATS telemetry consumers (TimescaleDB writer).
        payload.update(lat=payload["latitude"], lon=payload["longitude"], alt=payload["altitude"], soc=payload["battery"])
        if truth:
            payload.update({f"truth_{k}": v for k, v in truth.items()})
        return payload
