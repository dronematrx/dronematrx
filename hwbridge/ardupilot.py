"""One ArduPilot (ArduCopter) flight controller seen over MAVLink.

State comes only from what the autopilot reports. A value never received
stays None so the backend's pre-arm checklist shows it as UNKNOWN.

Safety rules implemented here (the backend enforces them too):

* Never sends RC_CHANNELS_OVERRIDE. The pilot's RC transmitter always has
  authority: a mode change on the transmitter takes effect immediately.
* Pilot / autopilot override: any mode change this bridge did not command
  (RC mode switch, a failsafe RTL/LAND, another ground station) and any arming
  it did not command (stick arming) sets `override`. While set, every
  automated command is refused until an operator explicitly releases it, so
  Drone MatrX never fights the pilot or a failsafe for control.
* Flight commands (ARM, TAKEOFF, GOTO, MISSION_START) need a time-limited
  flight authorization granted by an operator through the backend.
* Hard limits: maximum altitude and maximum distance from home for TAKEOFF,
  GOTO and every mission waypoint.
"""
from __future__ import annotations

import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

# ArduCopter custom_mode numbers
COPTER_MODES = {
    0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO", 4: "GUIDED", 5: "LOITER", 6: "RTL", 7: "CIRCLE", 9: "LAND",
    11: "DRIFT", 13: "SPORT", 14: "FLIP", 15: "AUTOTUNE", 16: "POSHOLD", 17: "BRAKE", 18: "THROW", 19: "AVOID_ADSB",
    20: "GUIDED_NOGPS", 21: "SMART_RTL", 22: "FLOWHOLD", 23: "FOLLOW", 24: "ZIGZAG", 25: "SYSTEMID", 26: "AUTOROTATE", 27: "AUTO_RTL",
}
MODE_NUMBER = {v: k for k, v in COPTER_MODES.items()}
COPTER_TYPES = {2, 3, 4, 13, 14, 15}  # quad, coaxial, heli, hexa, octo, tri
MAV_AUTOPILOT_ARDUPILOTMEGA = 3
MAV_TYPE_GCS = 6

# MAV_SYS_STATUS_SENSOR bits
RC_RECEIVER = 0x10000
PREARM_CHECK = 0x10000000

FAILSAFE_PARAMS = ("FS_GCS_ENABLE", "FS_THR_ENABLE", "BATT_FS_LOW_ACT", "FENCE_ENABLE", "FENCE_ACTION", "FENCE_ALT_MAX",
                   "FENCE_RADIUS", "RTL_ALT", "SYSID_THISMAV")

MAV_RESULT = {0: "ACCEPTED", 1: "TEMPORARILY_REJECTED", 2: "DENIED", 3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS", 6: "CANCELLED"}


class VehicleError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Limits:
    max_alt_m: float = 30.0
    max_distance_m: float = 150.0

    @classmethod
    def from_dict(cls, d: dict | None) -> "Limits":
        d = d or {}
        return cls(float(d.get("max_alt_m", 30.0)), float(d.get("max_distance_m", 150.0)))


@dataclass
class VehicleState:
    armed: bool = False
    mode: str = "UNKNOWN"
    custom_mode: int | None = None
    vehicle_type: int | None = None
    system_status: int | None = None
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
    battery_v: float | None = None
    gps_fix: int | None = None
    satellites: int | None = None
    landed_state: int | None = None  # MAV_LANDED_STATE (EXTENDED_SYS_STATE)
    home: tuple[float, float, float] | None = None
    sensors_present: int | None = None
    sensors_enabled: int | None = None
    sensors_health: int | None = None
    rc_rssi: int | None = None
    rc_last: float = 0.0
    firmware: str | None = None
    mission_current: int = -1
    mission_count: int = 0
    reached: set[int] = field(default_factory=set)
    last_rx: float = 0.0
    last_heartbeat: float = 0.0
    radio_rssi: int | None = None
    radio_remrssi: int | None = None
    statustext: deque = field(default_factory=lambda: deque(maxlen=30))


class Vehicle:
    """One autopilot (system id) on one link. `send` is the link's MAVLink
    sender (pymavlink `mav` object); `on_event` publishes alerts."""

    def __init__(self, link_name: str, endpoint: str, sysid: int, compid: int, mav, limits: Limits,
                 on_event: Callable[[str, dict], None]):
        self.link_name = link_name
        self.endpoint = endpoint
        self.sysid = sysid
        self.compid = compid
        self.fc_id = f"{link_name}-{sysid}"
        self.identity = f"{endpoint}/{sysid}"
        self.mav = mav
        self.limits = limits
        self.on_event = on_event
        self.state = VehicleState()
        self.params: dict[str, float] = {}
        self._param_evt = threading.Event()
        self._acks: queue.Queue = queue.Queue()
        self._mission_q: queue.Queue = queue.Queue()
        self._cmd_lock = threading.Lock()
        # binding (set by the backend through the bridge API)
        self.drone_id: str | None = None
        self.uin: str | None = None
        self.name: str | None = None
        # safety state
        self.authorized_until: float = 0.0
        self.authorized_by: str | None = None
        self.override: dict | None = None
        self._commanded_modes: list[tuple[str, float]] = []  # every mode this bridge asked for, last 10 s
        self._commanded_arm: float = 0.0
        self.last_command_timing: dict = {}
        self.streams_requested = 0.0
        self._params_cache: dict[str, float | None] = {}
        self._params_read_at = 0.0

    # ----------------------------------------------------------- inbound

    @property
    def bound(self) -> bool:
        return bool(self.drone_id)

    def handle(self, msg) -> None:
        s = self.state
        now = time.time()
        s.last_rx = now
        t = msg.get_type()
        if t == "HEARTBEAT":
            s.last_heartbeat = now
            s.vehicle_type, s.system_status, s.custom_mode = msg.type, msg.system_status, msg.custom_mode
            armed = bool(msg.base_mode & 0x80)
            mode = COPTER_MODES.get(msg.custom_mode, f"MODE_{msg.custom_mode}") if msg.type in COPTER_TYPES else f"UNSUPPORTED_TYPE_{msg.type}"
            if armed != s.armed:
                ours = now - self._commanded_arm < 10.0
                self.on_event("armed" if armed else "disarmed", {"by_drone_matrx": ours})
                if armed and not ours and s.mode != "UNKNOWN":
                    self._set_override(f"armed outside Drone MatrX (RC sticks or another ground station) in {mode}")
            if mode != s.mode and s.mode != "UNKNOWN":
                ours = any(m == mode and now - t < 10.0 for m, t in self._commanded_modes)
                self.on_event("mode_changed", {"previous": s.mode, "mode": mode, "by_drone_matrx": ours})
                if not ours:
                    self._set_override(f"mode changed {s.mode} -> {mode} outside Drone MatrX (RC mode switch, failsafe or another ground station)")
            s.armed, s.mode = armed, mode
        elif t == "GLOBAL_POSITION_INT":
            s.lat, s.lon = msg.lat / 1e7, msg.lon / 1e7
            s.alt_msl, s.rel_alt = msg.alt / 1000.0, msg.relative_alt / 1000.0
            s.vx, s.vy, s.vz = msg.vx / 100.0, msg.vy / 100.0, msg.vz / 100.0
            if msg.hdg != 65535:
                s.heading = msg.hdg / 100.0
        elif t == "VFR_HUD":
            s.groundspeed = msg.groundspeed
        elif t == "SYS_STATUS":
            if msg.battery_remaining >= 0:
                s.battery_pct = float(msg.battery_remaining)
            if msg.voltage_battery not in (0, 65535):
                s.battery_v = msg.voltage_battery / 1000.0
            s.sensors_present = msg.onboard_control_sensors_present
            s.sensors_enabled = msg.onboard_control_sensors_enabled
            s.sensors_health = msg.onboard_control_sensors_health
        elif t == "BATTERY_STATUS":
            if msg.battery_remaining >= 0:
                s.battery_pct = float(msg.battery_remaining)
        elif t == "AUTOPILOT_VERSION":
            v = msg.flight_sw_version
            s.firmware = f"ArduPilot v{(v >> 24) & 0xFF}.{(v >> 16) & 0xFF}.{(v >> 8) & 0xFF}"
        elif t == "GPS_RAW_INT":
            s.gps_fix, s.satellites = msg.fix_type, msg.satellites_visible
        elif t == "EXTENDED_SYS_STATE":
            if msg.landed_state != s.landed_state and s.landed_state is not None:
                self.on_event("landed_state", {"previous": s.landed_state, "landed_state": msg.landed_state})
            s.landed_state = msg.landed_state
        elif t == "HOME_POSITION":
            s.home = (msg.latitude / 1e7, msg.longitude / 1e7, msg.altitude / 1000.0)
        elif t == "RC_CHANNELS":
            s.rc_rssi = msg.rssi
            s.rc_last = now
        elif t == "RADIO_STATUS":
            s.radio_rssi, s.radio_remrssi = msg.rssi, msg.remrssi
        elif t == "MISSION_CURRENT":
            if msg.seq != s.mission_current:
                s.mission_current = msg.seq
                self.on_event("mission_current", {"seq": msg.seq})
        elif t == "MISSION_ITEM_REACHED":
            if msg.seq not in s.reached:
                s.reached.add(msg.seq)
                self.on_event("waypoint_reached", {"seq": msg.seq})
        elif t in ("MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"):
            self._mission_q.put(msg)
        elif t == "COMMAND_ACK":
            self._acks.put(msg)
        elif t == "PARAM_VALUE":
            self.params[msg.param_id] = float(msg.param_value)
            self._param_evt.set()
        elif t == "STATUSTEXT":
            s.statustext.append((now, msg.severity, msg.text))
            if msg.severity <= 4:  # warning or worse: failsafes, pre-arm failures
                self.on_event("statustext", {"severity": msg.severity, "text": msg.text})

    def _set_override(self, reason: str) -> None:
        if self.override is None:
            self.override = {"reason": reason, "at": time.time(), "mode": self.state.mode}
            self.on_event("pilot_override", {"reason": reason})

    # ---------------------------------------------------------- outbound

    def request_streams(self) -> None:
        """Message rates kept modest: several aircraft may share one radio."""
        rates = ((33, 4), (1, 2), (24, 1), (74, 2), (245, 1), (147, 1), (42, 1), (65, 1), (242, 0.2))
        for msg_id, hz in rates:
            try:  # MAV_CMD_SET_MESSAGE_INTERVAL
                self.mav.command_long_send(self.sysid, self.compid, 511, 0, msg_id, 1e6 / hz, 0, 0, 0, 0, 0)
            except Exception:
                pass
        try:  # AUTOPILOT_VERSION, HOME_POSITION once
            self.mav.command_long_send(self.sysid, self.compid, 512, 0, 148, 0, 0, 0, 0, 0, 0)
            self.mav.command_long_send(self.sysid, self.compid, 512, 0, 242, 0, 0, 0, 0, 0, 0)
        except Exception:
            pass
        self.streams_requested = time.time()

    def read_params(self, names=FAILSAFE_PARAMS, timeout_s: float = 2.0) -> dict:
        out = {}
        for name in names:
            self.params.pop(name, None)
            self._param_evt.clear()
            try:
                self.mav.param_request_read_send(self.sysid, self.compid, name.encode(), -1)
            except Exception:
                out[name] = None
                continue
            deadline = time.time() + timeout_s
            while time.time() < deadline and name not in self.params:
                self._param_evt.wait(0.1)
                self._param_evt.clear()
            out[name] = self.params.get(name)
        return out

    def _command(self, command: int, params: list[float], timeout_s: float = 5.0) -> int:
        p = (params + [0.0] * 7)[:7]
        while not self._acks.empty():
            self._acks.get_nowait()
        sent = time.perf_counter()
        self.mav.command_long_send(self.sysid, self.compid, command, 0, *p)
        return self._await_ack(command, sent, timeout_s)

    def _command_int(self, command: int, frame: int, params: list[float], x: int, y: int, z: float, timeout_s: float = 5.0) -> int:
        p = (params + [0.0] * 4)[:4]
        while not self._acks.empty():
            self._acks.get_nowait()
        sent = time.perf_counter()
        self.mav.command_int_send(self.sysid, self.compid, frame, command, 0, 0, *p, x, y, z)
        return self._await_ack(command, sent, timeout_s)

    def _await_ack(self, command: int, sent: float, timeout_s: float) -> int:
        self.last_command_timing = {"command": command, "sent": sent, "ack": None}
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                ack = self._acks.get(timeout=0.05)
            except queue.Empty:
                continue
            if ack.command == command and ack.result != 5:  # skip IN_PROGRESS
                self.last_command_timing["ack"] = time.perf_counter()
                return ack.result
        raise VehicleError("NO_ACK", f"No COMMAND_ACK for command {command} within {timeout_s:g} s (radio link?)")

    # ------------------------------------------------------------ safety

    def _require_controllable(self) -> None:
        if self.state.vehicle_type is not None and self.state.vehicle_type not in COPTER_TYPES:
            raise VehicleError("UNSUPPORTED_VEHICLE", f"Only ArduCopter is supported (MAV_TYPE {self.state.vehicle_type})")
        if self.override is not None:
            raise VehicleError("PILOT_OVERRIDE", f"Pilot/autopilot override active ({self.override['reason']}); "
                                                 "an operator must release it in Drone MatrX before automated commands")
        if not self.state.last_heartbeat or time.time() - self.state.last_heartbeat > 5:
            raise VehicleError("NO_HEARTBEAT", "No heartbeat from the flight controller in the last 5 s")

    def _require_authorized(self) -> None:
        if time.time() >= self.authorized_until:
            raise VehicleError("FLIGHT_NOT_AUTHORIZED", "No valid flight permission for this aircraft; an operator must grant it")

    def _check_point(self, lat: float, lon: float, rel_alt: float) -> None:
        if rel_alt > self.limits.max_alt_m:
            raise VehicleError("ALTITUDE_LIMIT", f"{rel_alt:.0f} m exceeds the hardware altitude limit {self.limits.max_alt_m:.0f} m")
        home = self.state.home
        if home is None:
            raise VehicleError("NO_HOME", "Home position not known; cannot check the distance limit")
        d = distance_m(home[0], home[1], lat, lon)
        if d > self.limits.max_distance_m:
            raise VehicleError("DISTANCE_LIMIT", f"{d:.0f} m from home exceeds the hardware distance limit {self.limits.max_distance_m:.0f} m")

    # ---------------------------------------------------------- commands

    def _command_mode(self, mode: str) -> None:
        now = time.time()
        self._commanded_modes = [(m, t) for m, t in self._commanded_modes if now - t < 10.0] + [(mode, now)]

    def set_mode(self, mode: str) -> int:
        number = MODE_NUMBER[mode]
        self._command_mode(mode)
        return self._command(176, [1, number])  # MAV_CMD_DO_SET_MODE, custom mode enabled

    def execute(self, command: str, params: dict) -> int:
        """One Drone MatrX command. Returns MAV_RESULT. Raises VehicleError."""
        with self._cmd_lock:
            self._require_controllable()
            if command == "arm":
                self._require_authorized()
                # Always arm in GUIDED: autonomous take-off needs it, and the
                # pilot regains manual control with the transmitter's mode switch.
                if self.state.mode != "GUIDED":
                    r = self.set_mode("GUIDED")
                    if r != 0:
                        return r
                self._commanded_arm = time.time()
                return self._command(400, [1, 0])  # ArduPilot's own pre-arm checks decide; never forced
            if command == "disarm":
                if self.state.landed_state not in (1,):
                    raise VehicleError("NOT_ON_GROUND", "Disarm only on the ground (landed state reported by the autopilot)")
                return self._command(400, [0, 0])
            if command == "takeoff":
                self._require_authorized()
                alt = float(params.get("altitude_m", 10))
                if self.state.home:
                    self._check_point(self.state.home[0], self.state.home[1], alt)
                elif alt > self.limits.max_alt_m:
                    raise VehicleError("ALTITUDE_LIMIT", f"{alt:.0f} m exceeds {self.limits.max_alt_m:.0f} m")
                if not self.state.armed:
                    raise VehicleError("NOT_ARMED", "Arm before take-off")
                if self.state.mode != "GUIDED":
                    r = self.set_mode("GUIDED")
                    if r != 0:
                        return r
                return self._command(22, [0, 0, 0, 0, 0, 0, alt])  # ArduCopter: param7 = altitude above home
            if command == "hold":
                return self.set_mode("LOITER")
            if command == "rtl":
                return self.set_mode("RTL")
            if command == "land":
                return self.set_mode("LAND")
            if command == "goto":
                self._require_authorized()
                lat, lon, alt = float(params["latitude"]), float(params["longitude"]), float(params.get("altitude_m", 20))
                self._check_point(lat, lon, alt)
                self._command_mode("GUIDED")
                # MAV_CMD_DO_REPOSITION, frame GLOBAL_RELATIVE_ALT_INT, flag "change mode" -> GUIDED (ArduCopter 4.1+)
                r = self._command_int(192, 6, [-1, 1, 0, float("nan")], int(lat * 1e7), int(lon * 1e7), alt)
                if r == 3:  # older firmware: GUIDED + position target (no acknowledgement)
                    r = self.set_mode("GUIDED")
                    if r == 0:
                        self.mav.set_position_target_global_int_send(0, self.sysid, self.compid, 6, 0b110111111000,
                                                                     int(lat * 1e7), int(lon * 1e7), alt, 0, 0, 0, 0, 0, 0, 0, 0)
                return r
            if command == "mission_start":
                self._require_authorized()
                if self.state.mission_count < 2:
                    raise VehicleError("NO_MISSION", "No mission uploaded to this aircraft")
                if not self.state.armed:
                    if self.state.mode != "GUIDED":
                        r = self.set_mode("GUIDED")
                        if r != 0:
                            return r
                    self._commanded_arm = time.time()
                    r = self._command(400, [1, 0])
                    if r != 0:
                        return r
                self._command_mode("AUTO")
                return self._command(300, [0, 0])  # MAV_CMD_MISSION_START: AUTO from the first item (takeoff)
            raise VehicleError("UNSUPPORTED_COMMAND", f"Unsupported command {command}")

    def upload_mission(self, items: list[dict], timeout_s: float = 30.0) -> int:
        """Upload a mission (items without home). ArduPilot's item 0 is home:
        a placeholder is sent there and ArduPilot keeps its own home.
        Every positional item is checked against the hardware limits."""
        from pymavlink import mavutil

        with self._cmd_lock:
            self._require_controllable()
            if self.state.armed:
                raise VehicleError("ARMED", "Upload missions on the ground (disarmed) only")
            for it in items:
                if it["command"] in (16, 22) and (it.get("lat") or it.get("lon")):
                    self._check_point(it["lat"], it["lon"], float(it.get("alt", 0)))
                elif it["command"] == 22 and float(it.get("alt", 0)) > self.limits.max_alt_m:
                    raise VehicleError("ALTITUDE_LIMIT", f"take-off altitude exceeds {self.limits.max_alt_m:.0f} m")
            home = self.state.home or (0.0, 0.0, 0.0)
            full = [{"command": 16, "lat": home[0], "lon": home[1], "alt": 0, "frame": 0}] + items
            while not self._mission_q.empty():
                self._mission_q.get_nowait()
            self.mav.mission_count_send(self.sysid, self.compid, len(full), 0)
            deadline = time.time() + timeout_s
            last_activity = time.time()
            while time.time() < deadline:
                try:
                    msg = self._mission_q.get(timeout=0.5)
                except queue.Empty:
                    if time.time() - last_activity > 3:  # radio packet lost: restart the handshake
                        self.mav.mission_count_send(self.sysid, self.compid, len(full), 0)
                        last_activity = time.time()
                    continue
                last_activity = time.time()
                if msg.get_type() == "MISSION_ACK":
                    if msg.type == 0:
                        self.state.mission_count = len(full)
                        self.state.reached.clear()
                        self.state.mission_current = -1
                        return len(items)
                    raise VehicleError("MISSION_REJECTED", f"Mission rejected by ArduPilot (MAV_MISSION_RESULT {msg.type})")
                it = full[msg.seq]
                frame = it.get("frame", mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT)
                if frame == 2:  # PX4-style MISSION frame for DO_* items: ArduPilot uses the relative-alt frame
                    frame = mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
                self.mav.mission_item_int_send(
                    self.sysid, self.compid, msg.seq, frame, it["command"], 0, 1,
                    it.get("param1", 0), it.get("param2", 0), it.get("param3", 0), it.get("param4", 0),
                    int(it.get("lat", 0) * 1e7), int(it.get("lon", 0) * 1e7), float(it.get("alt", 0)), 0)
            raise VehicleError("MISSION_TIMEOUT", "Mission upload timed out (radio link?)")

    # ------------------------------------------------------------- views

    def info(self) -> dict:
        s = self.state
        now = time.time()
        return {
            "fc_id": self.fc_id, "identity": self.identity, "endpoint": self.endpoint, "system_id": self.sysid, "component_id": self.compid,
            "firmware": s.firmware or "ArduPilot", "vehicle_type": s.vehicle_type,
            "heartbeat_age_s": round(now - s.last_heartbeat, 2) if s.last_heartbeat else None,
            "bound": self.bound, "drone_id": self.drone_id, "uin": self.uin, "mode": s.mode, "armed": s.armed, "closed": False,
            "authorized_until": self.authorized_until if self.authorized_until > now else None, "authorized_by": self.authorized_by,
            "override": self.override, "radio_rssi": s.radio_rssi, "radio_remrssi": s.radio_remrssi,
        }

    def prearm(self) -> dict:
        s = self.state
        now = time.time()
        # Parameters change rarely and the radio is slow: re-read at most every 30 s.
        if now - self._params_read_at > 30 or any(v is None for v in self._params_cache.values()):
            self._params_cache = self.read_params()
            self._params_read_at = time.time()
        params = dict(self._params_cache)
        rc_ok = None
        if s.sensors_present is not None and s.sensors_present & RC_RECEIVER:
            rc_ok = bool(s.sensors_health & RC_RECEIVER) if s.sensors_health is not None else None
        return {
            **self.info(),
            "autopilot": "ardupilot",
            "sensors_present": s.sensors_present, "sensors_enabled": s.sensors_enabled, "sensors_health": s.sensors_health,
            "gps_fix": s.gps_fix, "satellites": s.satellites, "home_set": s.home is not None, "home": s.home,
            "battery_pct": s.battery_pct, "battery_v": s.battery_v, "landed_state": s.landed_state,
            "rc_receiver_ok": rc_ok, "rc_rssi": s.rc_rssi, "rc_age_s": round(now - s.rc_last, 2) if s.rc_last else None,
            "last_rx_age_s": round(now - s.last_rx, 2) if s.last_rx else None,
            "statustext": [{"t": t, "severity": sev, "text": txt} for t, sev, txt in list(s.statustext)[-10:]],
            "failsafe_params": params,
            "limits": {"max_alt_m": self.limits.max_alt_m, "max_distance_m": self.limits.max_distance_m},
        }

    def telemetry(self) -> dict | None:
        s = self.state
        if s.lat is None or not self.bound:
            return None
        battery = s.battery_pct if s.battery_pct is not None else 0.0
        status = "active" if s.armed else "online"
        if battery < 15:
            status = "critical"
        elif battery < 30 or self.override is not None:
            status = "warning"
        payload = {
            "drone_id": self.drone_id, "name": self.name, "uin": self.uin,
            "latitude": s.lat, "longitude": s.lon, "altitude": round(s.rel_alt, 2), "battery": round(battery, 1),
            "heading": round(s.heading, 1), "speed": round(s.groundspeed or math.hypot(s.vx, s.vy), 2), "flight_mode": s.mode,
            "fc_id": self.fc_id, "gps_status": {2: "2D_FIX"}.get(s.gps_fix, "3D_FIX" if (s.gps_fix or 0) >= 3 else "NO_FIX"),
            "armed": s.armed, "link_quality": radio_quality(s.radio_rssi, s.radio_remrssi), "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            # Not one of the Sprint 4 link types (wifi/lte/lora): the telemetry store rejects
            # unknown values, so the radio is reported as `ground_link` instead.
            "link_type": None, "ground_link": "telemetry_radio",
            "simulation": False, "hardware": True, "alt_msl": s.alt_msl, "landed_state": s.landed_state,
            "mission_current": s.mission_current, "mission_count": s.mission_count,
            "reached_max": max(s.reached) if s.reached else -1, "vx": s.vx, "vy": s.vy, "vz": s.vz, "satellites": s.satellites,
            "pilot_override": self.override is not None,
        }
        payload.update(lat=payload["latitude"], lon=payload["longitude"], alt=payload["altitude"], soc=payload["battery"])
        return payload


def radio_quality(rssi: int | None, remrssi: int | None) -> float:
    """SiK RADIO_STATUS rssi/remrssi (0..254) -> 0..100 %; 100 when unknown (USB / Wi-Fi links)."""
    vals = [v for v in (rssi, remrssi) if v is not None and v != 255]
    if not vals:
        return 100.0
    return round(min(vals) / 254 * 100, 1)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
