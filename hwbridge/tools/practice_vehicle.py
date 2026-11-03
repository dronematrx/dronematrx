"""PRACTICE VEHICLE: a minimal ArduCopter-like MAVLink responder for rehearsing
the hardware workflow without aircraft. It is NOT ArduPilot and NOT a flight
simulator: simple kinematics, no physics. Everything it reports is simulated.

    backend\\venv\\Scripts\\python -m hwbridge.tools.practice_vehicle --count 2

Vehicle N (system id N) listens on TCP 127.0.0.1:5760 + 10*(N-1), like
ArduPilot SITL, so the bridge config uses  {"endpoint": "tcp:127.0.0.1:5760"}.

A small control server on http://127.0.0.1:5799 plays the safety pilot:
    POST /pilot?sysid=1&mode=LOITER     RC mode switch (outside Drone MatrX)
    POST /rc?sysid=1&on=0               RC transmitter off / on
    POST /sticks?sysid=1&mode=sweep     move the sticks through their full range (RC calibration);
                                        mode=center returns them to the middle (throttle low)

--fresh starts every vehicle like a new flight controller (all with system id 1,
failsafes off, sensors and RC uncalibrated, no frame type), to rehearse the
guided Drone Setup. It answers parameter writes, reboot, accelerometer /
level / compass calibration and motor tests the way ArduCopter does.
"""
from __future__ import annotations

import argparse
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pymavlink import mavutil

MODES = {"STABILIZE": 0, "ALT_HOLD": 2, "AUTO": 3, "GUIDED": 4, "LOITER": 5, "RTL": 6, "LAND": 9, "BRAKE": 17}
MODE_NAME = {v: k for k, v in MODES.items()}
# ArduCopter defaults of a new flight controller (subset the setup uses)
FACTORY = {"SYSID_THISMAV": 1, "FRAME_CLASS": 0, "FRAME_TYPE": 1, "FS_GCS_ENABLE": 0, "FS_GCS_TIMEOUT": 5, "FS_THR_ENABLE": 1,
           "FS_THR_VALUE": 975, "BATT_MONITOR": 4, "BATT_FS_LOW_ACT": 0, "BATT_FS_CRT_ACT": 0, "BATT_LOW_VOLT": 10.5, "BATT_CRT_VOLT": 0,
           "FENCE_ENABLE": 0, "FENCE_TYPE": 7, "FENCE_ACTION": 1, "FENCE_ALT_MAX": 100, "FENCE_RADIUS": 300, "RTL_ALT": 1500,
           "FLTMODE_CH": 5, **{f"FLTMODE{i}": 0 for i in range(1, 7)},
           **{f"RC{i}_{k}": v for i in range(1, 5) for k, v in (("MIN", 1100), ("MAX", 1900), ("TRIM", 1500))},
           "RCMAP_ROLL": 1, "RCMAP_PITCH": 2, "RCMAP_THROTTLE": 3, "RCMAP_YAW": 4,
           **{f"INS_ACCOFFS_{a}": 0.0 for a in "XYZ"}, **{f"INS_ACCSCAL_{a}": 1.0 for a in "XYZ"},
           "COMPASS_USE": 1, **{f"COMPASS_OFS_{a}": 0.0 for a in "XYZ"}}
# A vehicle someone already set up (the default, used by the hardware walkthrough)
CONFIGURED = {**FACTORY, "FRAME_CLASS": 1, "FS_GCS_ENABLE": 1, "BATT_FS_LOW_ACT": 2, "BATT_FS_CRT_ACT": 1, "BATT_LOW_VOLT": 14.0,
              "BATT_CRT_VOLT": 13.2, "FENCE_ENABLE": 1, "FENCE_ALT_MAX": 40, "FENCE_RADIUS": 150,
              "FLTMODE1": 2, "FLTMODE2": 2, "FLTMODE3": 5, "FLTMODE4": 5, "FLTMODE5": 6, "FLTMODE6": 6,
              **{f"RC{i}_{k}": v for i in range(1, 5) for k, v in (("MIN", 1012), ("MAX", 1988), ("TRIM", 1500))}, "RC3_TRIM": 1012,
              "INS_ACCOFFS_X": 0.04, "INS_ACCSCAL_X": 1.002, "COMPASS_OFS_X": 41.0, "COMPASS_OFS_Y": -12.0, "COMPASS_OFS_Z": 30.0}
AHRS, GPS, RC, PREARM = 0x200000, 0x20, 0x10000, 0x10000000
M_PER_DEG = 111_320.0


class PracticeCopter:
    def __init__(self, sysid: int, lat: float, lon: float, fresh: bool = False):
        self.sysid = 1 if fresh else sysid
        self.port = 5760 + 10 * (sysid - 1)
        self.home = (lat, lon, 50.0)
        self.lat, self.lon, self.alt = lat, lon, 0.0
        self.heading = 0.0
        self.mode = "STABILIZE"
        self.armed = False
        self.battery = 98.0
        self.rc_on = True
        self.target: tuple[float, float, float] | None = None
        self.mission: list = []
        self.mission_seq = 0
        self.params = dict(FACTORY) if fresh else {**CONFIGURED, "SYSID_THISMAV": sysid, "RTL_ALT": 1500 + 500 * (sysid - 1)}
        self.sticks = "center"
        self.accel_pos = 0          # accelerometer calibration: position the vehicle waits for (0 = idle)
        self.magcal_pct: float | None = None
        self.rebooting_until = 0.0
        self.speed = 5.0
        self.conn = mavutil.mavlink_connection(f"tcpin:127.0.0.1:{self.port}", source_system=self.sysid, source_component=1)
        self._mission_rx: list = []
        self.lock = threading.Lock()

    # ------------------------------------------------------------ physics

    def landed(self) -> bool:
        return self.alt < 0.2

    def _move_to(self, lat: float, lon: float, alt: float, dt: float) -> bool:
        dn = (lat - self.lat) * M_PER_DEG
        de = (lon - self.lon) * M_PER_DEG * math.cos(math.radians(self.lat))
        d = math.hypot(dn, de)
        step = self.speed * dt
        if d > 0.3:
            k = min(1.0, step / d)
            self.lat += dn * k / M_PER_DEG
            self.lon += de * k / (M_PER_DEG * math.cos(math.radians(self.lat)))
            self.heading = (math.degrees(math.atan2(de, dn)) + 360) % 360
        dz = alt - self.alt
        self.alt += max(-2.0 * dt, min(2.0 * dt, dz))
        return d <= 1.0 and abs(alt - self.alt) < 0.5

    def step(self, dt: float) -> None:
        with self.lock:
            if self.armed:
                self.battery = max(0.0, self.battery - 0.02 * dt)
            if not self.armed:
                return
            if self.mode == "GUIDED" and self.target:
                self._move_to(*self.target, dt)
            elif self.mode == "RTL":
                rtl_alt = self.params["RTL_ALT"] / 100.0
                far = math.hypot((self.home[0] - self.lat) * M_PER_DEG, (self.home[1] - self.lon) * M_PER_DEG) > 1.0
                if far:
                    self._move_to(self.home[0], self.home[1], max(self.alt, rtl_alt), dt)
                else:
                    self._descend(dt)
            elif self.mode == "LAND":
                self._descend(dt)
            elif self.mode == "AUTO":
                self._auto(dt)

    def _descend(self, dt: float) -> None:
        self.alt = max(0.0, self.alt - 1.0 * dt)
        if self.alt <= 0.0:
            self.armed = False
            self.statustext("Disarming motors")

    def _auto(self, dt: float) -> None:
        if self.mission_seq >= len(self.mission):
            return
        it = self.mission[self.mission_seq]
        if it.command == 22:  # takeoff
            self.alt = min(it.z, self.alt + 2.0 * dt)
            done = self.alt >= it.z - 0.3
        elif it.command == 16:
            done = self._move_to(it.x / 1e7, it.y / 1e7, it.z, dt)
        elif it.command == 178:
            self.speed = it.param2 or self.speed
            done = True
        elif it.command == 20:
            self.mode = "RTL"
            done = True
        else:
            done = True
        if done:
            self.conn.mav.mission_item_reached_send(self.mission_seq)
            self.mission_seq += 1
            self.conn.mav.mission_current_send(self.mission_seq)

    # ------------------------------------------------------------- MAVLink

    def statustext(self, text: str, severity: int = 6) -> None:
        self.conn.mav.statustext_send(severity, text.encode()[:50])

    def ack(self, cmd: int, result: int) -> None:
        self.conn.mav.command_ack_send(cmd, result)

    def handle(self, m) -> None:
        t = m.get_type()
        if t == "COMMAND_LONG":
            self.command(m.command, [m.param1, m.param2, m.param3, m.param4, m.param5, m.param6, m.param7])
        elif t == "COMMAND_INT":
            if m.command == 192:  # DO_REPOSITION
                self.mode, self.target = "GUIDED", (m.x / 1e7, m.y / 1e7, m.z)
                self.ack(192, 0)
            else:
                self.ack(m.command, 3)
        elif t == "PARAM_REQUEST_READ":
            name = m.param_id
            if name in self.params:
                self.conn.mav.param_value_send(name.encode(), float(self.params[name]), 9, len(self.params), 0)
        elif t == "PARAM_SET":
            name = m.param_id
            if name in self.params and not self.armed:
                v = float(m.param_value)
                self.params[name] = int(v) if float(v).is_integer() and not name.startswith(("INS_", "COMPASS_OFS", "BATT_")) else v
                self.conn.mav.param_value_send(name.encode(), float(self.params[name]), 9, len(self.params), 0)
        elif t == "MISSION_COUNT":
            self._mission_rx = [None] * m.count
            self.conn.mav.mission_request_int_send(255, 190, 0)
        elif t == "MISSION_ITEM_INT":
            if 0 <= m.seq < len(self._mission_rx):
                self._mission_rx[m.seq] = m
                if m.seq + 1 < len(self._mission_rx):
                    self.conn.mav.mission_request_int_send(255, 190, m.seq + 1)
                else:
                    self.mission = self._mission_rx
                    self.mission_seq = 1
                    self.conn.mav.mission_ack_send(255, 190, 0)

    def command(self, cmd: int, p: list[float]) -> None:
        with self.lock:
            if cmd == 176:  # DO_SET_MODE
                name = MODE_NAME.get(int(p[1]))
                if name is None:
                    return self.ack(cmd, 4)
                self.mode = name
                if name == "LOITER":
                    self.target = (self.lat, self.lon, self.alt)
                return self.ack(cmd, 0)
            if cmd == 400:  # ARM / DISARM
                if p[0] == 1:
                    if not self.rc_on:
                        self.statustext("PreArm: RC not found", 3)
                        return self.ack(cmd, 4)
                    if self.prearm_failures():
                        self.statustext(self.prearm_failures()[0], 3)
                        return self.ack(cmd, 4)
                    self.armed = True
                    self.statustext("Arming motors")
                    return self.ack(cmd, 0)
                if not self.landed():
                    return self.ack(cmd, 4)
                self.armed = False
                return self.ack(cmd, 0)
            if cmd == 22:  # TAKEOFF (altitude above home)
                if not self.armed or self.mode != "GUIDED":
                    return self.ack(cmd, 4)
                self.target = (self.lat, self.lon, p[6])
                return self.ack(cmd, 0)
            if cmd == 300:  # MISSION_START
                if not self.mission:
                    return self.ack(cmd, 4)
                self.mode, self.mission_seq = "AUTO", 1
                return self.ack(cmd, 0)
            if cmd in (246, 241, 42424, 42426, 42429, 209):
                return self.setup_command(cmd, p)
            if cmd in (511, 512, 520):
                if cmd == 512 and int(p[0]) == 148:
                    self.conn.mav.autopilot_version_send(0, (4 << 24) | (7 << 16) | (2 << 8) | 255, 0, 0, 0, b"\0" * 8, b"\0" * 8, b"\0" * 8, 0, 0, 0)
                if cmd == 512 and int(p[0]) == 242:
                    self.send_home()
                return self.ack(cmd, 0)
            self.ack(cmd, 3)

    # ---------------------------------------------------------- guided setup

    def prearm_failures(self) -> list[str]:
        p = self.params
        out = []
        if not p["FRAME_CLASS"]:
            out.append("PreArm: Check frame class and type")
        if all(p[f"INS_ACCOFFS_{a}"] == 0 for a in "XYZ") and all(p[f"INS_ACCSCAL_{a}"] == 1 for a in "XYZ"):
            out.append("PreArm: 3D Accel calibration needed")
        if p["COMPASS_USE"] and all(p[f"COMPASS_OFS_{a}"] == 0 for a in "XYZ"):
            out.append("PreArm: Compass not calibrated")
        if p["RC3_MIN"] == 1100 and p["RC3_MAX"] == 1900:
            out.append("PreArm: RC not calibrated")
        return out

    def setup_command(self, cmd: int, p: list[float]) -> None:
        if self.armed and cmd != 42429:
            return self.ack(cmd, 4)
        if cmd == 246:  # reboot: the new SYSID_THISMAV takes effect
            self.ack(cmd, 0)
            self.rebooting_until = time.time() + 3.0
            self.sysid = int(self.params["SYSID_THISMAV"])
            self.conn.mav.srcSystem = self.sysid
            self.mode, self.accel_pos, self.magcal_pct = "STABILIZE", 0, None
            return None
        if cmd == 241:
            if int(p[4]) == 1:  # full accelerometer calibration: six positions
                self.ack(cmd, 0)
                self.accel_pos = 1
                self.statustext("Place vehicle level and press any key.")
                self.conn.mav.command_long_send(255, 190, 42429, 0, 1, 0, 0, 0, 0, 0, 0)
                return None
            if int(p[4]) == 2:  # level
                self.ack(cmd, 0)
                self.statustext("Trim OK: roll=0.01 pitch=-0.02")
                return None
            return self.ack(cmd, 3)
        if cmd == 42429:  # ground station confirms the requested position
            if self.accel_pos and int(p[0]) == self.accel_pos:
                self.accel_pos += 1
                if self.accel_pos > 6:
                    self.accel_pos = 0
                    self.params.update(INS_ACCOFFS_X=0.031, INS_ACCOFFS_Y=-0.012, INS_ACCSCAL_X=1.001)
                    self.statustext("Calibration successful")
                    self.conn.mav.command_long_send(255, 190, 42429, 0, 16777215, 0, 0, 0, 0, 0, 0)
                else:
                    self.conn.mav.command_long_send(255, 190, 42429, 0, self.accel_pos, 0, 0, 0, 0, 0, 0)
            return None
        if cmd == 42424:  # compass calibration (autosave)
            self.ack(cmd, 0)
            self.magcal_pct = 0.0
            return None
        if cmd == 42426:
            self.magcal_pct = None
            return self.ack(cmd, 0)
        if cmd == 209:  # motor test
            self.ack(cmd, 0)
            self.statustext(f"Motor test: motor {int(p[0])} at {p[2]:.0f}% for {p[3]:.0f}s")
            return None
        return None

    def magcal_tick(self) -> None:
        if self.magcal_pct is None:
            return
        self.magcal_pct = min(100.0, self.magcal_pct + 12.5)
        if self.magcal_pct < 100:
            self.conn.mav.mag_cal_progress_send(0, 1, 3, 1, int(self.magcal_pct), [0] * 10, 0, 0, 1)
        else:
            self.params.update(COMPASS_OFS_X=37.0, COMPASS_OFS_Y=-15.0, COMPASS_OFS_Z=22.0)
            self.conn.mav.mag_cal_report_send(0, 1, 4, 1, 8.4, 37.0, -15.0, 22.0, 1, 1, 1, 0, 0, 0)
            self.magcal_pct = None

    def rc_values(self) -> list[int]:
        if self.sticks == "sweep":  # all four sticks through their full travel
            ph = time.time() * 1.6
            return [int(1500 + 490 * math.sin(ph + i)) for i in range(4)]
        return [1500, 1500, 1010, 1500]

    def send_home(self) -> None:
        self.conn.mav.home_position_send(int(self.home[0] * 1e7), int(self.home[1] * 1e7), int(self.home[2] * 1000), 0, 0, 0, [1, 0, 0, 0], 0, 0, 0)

    def telemetry(self) -> None:
        if time.time() < self.rebooting_until:
            return
        mav = self.conn.mav
        base = mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED | (mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED if self.armed else 0)
        mav.heartbeat_send(2, 3, base, MODES[self.mode], 4 if self.armed else 3)
        mav.global_position_int_send(int(time.time() * 1000) & 0xFFFFFFFF, int(self.lat * 1e7), int(self.lon * 1e7),
                                     int((self.home[2] + self.alt) * 1000), int(self.alt * 1000), 0, 0, 0, int(self.heading * 100))
        present = AHRS | GPS | RC | PREARM
        health = AHRS | GPS | (0 if self.prearm_failures() else PREARM) | (RC if self.rc_on else 0)
        mav.sys_status_send(present, present, health, 300, int((14.8 + self.battery / 100 * 2.0) * 1000), -1, int(self.battery), 0, 0, 0, 0, 0, 0)
        mav.gps_raw_int_send(0, 3, int(self.lat * 1e7), int(self.lon * 1e7), 0, 80, 120, 0, 0, 14)
        mav.extended_sys_state_send(0, 1 if self.landed() else 2)
        mav.vfr_hud_send(self.speed if self.armed and not self.landed() else 0.0, 0.0, int(self.heading), 0, self.alt, 0.0)
        flt = {0: 1100, 1: 1500, 2: 1900}[int(time.time() / 4) % 3] if self.sticks == "sweep" else 1100
        mav.rc_channels_send(0, 8, *self.rc_values(), flt, 1500, 1500, 1500, *([0] * 10), 200 if self.rc_on else 0)

    def run(self) -> None:
        last_tel = last_home = last_slow = 0.0
        last = time.time()
        while True:
            m = self.conn.recv_match(blocking=True, timeout=0.05)
            if m is not None and m.get_type() != "BAD_DATA":
                try:
                    self.handle(m)
                except Exception:
                    pass
            now = time.time()
            self.step(now - last)
            last = now
            try:
                if now - last_tel >= 0.25:
                    self.telemetry()
                    last_tel = now
                if now - last_home >= 5 and now >= self.rebooting_until:
                    self.send_home()
                    last_home = now
                if now - last_slow >= 0.5 and now >= self.rebooting_until:
                    self.magcal_tick()
                    if int(now) % 10 == 0 and not self.armed:
                        for text in self.prearm_failures()[:2]:
                            self.statustext(text, 4)
                    last_slow = now
            except Exception:
                pass  # no bridge connected yet


def control_server(vehicles: dict[int, PracticeCopter]) -> None:
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            n = int(q.get("sysid", 1))
            v = vehicles.get(n) or next((x for x in vehicles.values() if x.sysid == n), None)
            if v is None:
                self.send_response(404)
                self.end_headers()
                return
            with v.lock:
                if self.path.startswith("/pilot"):
                    v.mode = q["mode"].upper()
                    if v.mode == "LOITER":
                        v.target = (v.lat, v.lon, v.alt)
                elif self.path.startswith("/rc"):
                    v.rc_on = q.get("on", "1") == "1"
                elif self.path.startswith("/sticks"):
                    v.sticks = q.get("mode", "center")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")

        def log_message(self, *a):
            pass
    ThreadingHTTPServer(("127.0.0.1", 5799), H).serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description="PRACTICE vehicle (not ArduPilot): rehearse the hardware workflow without aircraft")
    ap.add_argument("--count", type=int, default=2)
    ap.add_argument("--lat", type=float, default=23.2400)
    ap.add_argument("--lon", type=float, default=72.6500)
    ap.add_argument("--fresh", action="store_true", help="start like new, unconfigured flight controllers")
    args = ap.parse_args()
    vehicles = {}
    for n in range(1, args.count + 1):
        v = PracticeCopter(n, args.lat + 0.00005 * (n - 1), args.lon, fresh=args.fresh)
        vehicles[n] = v
        threading.Thread(target=v.run, daemon=True, name=f"practice-{n}").start()
        print(f"practice vehicle {n} (system id {v.sysid}) on tcp:127.0.0.1:{v.port}{'  [fresh]' if args.fresh else ''}")
    threading.Thread(target=control_server, args=(vehicles,), daemon=True).start()
    print("pilot control on http://127.0.0.1:5799  (POST /pilot?sysid=1&mode=LOITER, /rc?sysid=1&on=0)")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
