"""Guided real-drone setup (the Drone Setup page): everything Mission Planner
was needed for, done through the hardware bridge.

* Starts / stops the bridge process on this laptop (it needs the COM ports,
  so it runs next to the backend, never in Docker).
* Connects radios / USB autopilots, assigns unique MAVLink ids, pairs SiK
  radios, writes the safety parameters, runs the calibrations and motor test,
  stores camera URLs.
* Only parameters on an allow-list can be written, only while disarmed (the
  bridge refuses otherwise), and every change is written to the audit log.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.user import User
from app.services.mission_control.fc import hw_client
from app.services.mission_control.hwbridge import REPO_ROOT, HwBridgeError

FC_ID_RE = re.compile(r"^[A-Za-z0-9]{1,24}-\d{1,3}$")
PORT_RE = re.compile(r"^(COM\d{1,3}|/dev/tty[A-Za-z0-9]{1,20})$")
LINK_NAME_RE = re.compile(r"^[A-Za-z0-9]{1,24}$")
# What the guided setup may change. Anything else needs a deliberate code change.
WRITABLE_PARAM_RE = re.compile(
    r"^(FRAME_(CLASS|TYPE)|FS_(GCS_ENABLE|GCS_TIMEOUT|THR_ENABLE|THR_VALUE)|BATT_(MONITOR|FS_LOW_ACT|FS_CRT_ACT|LOW_VOLT|CRT_VOLT)"
    r"|FENCE_(ENABLE|TYPE|ACTION|ALT_MAX|RADIUS)|RTL_ALT|FLTMODE(_CH|[1-6])|RC([1-9]|1[0-6])_(MIN|MAX|TRIM|REVERSED)"
    r"|RCMAP_(ROLL|PITCH|THROTTLE|YAW))$")
CALIBRATIONS = ("accel-start", "accel-next", "level", "compass-start", "compass-cancel")
BRIDGE_LOG = REPO_ROOT / "hwbridge" / "bridge.log"
BRIDGE_PID = REPO_ROOT / "hwbridge" / "bridge.pid"
BRIDGE_START_TIMEOUT_S = 90.0  # imports can take ~35 s on a laptop with on-access antivirus


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def bridge_pid() -> int | None:
    """PID of a bridge process that is alive (running or still starting), from hwbridge/bridge.pid."""
    try:
        pid = int(BRIDGE_PID.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


class SetupError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _bridge(fn, *args):
    try:
        return fn(*args)
    except HwBridgeError as exc:
        status = exc.status if exc.status in (404, 409, 422) else 503
        raise SetupError(exc.code or ("BRIDGE_UNAVAILABLE" if exc.status is None else "BRIDGE_ERROR"), str(exc), status) from exc


def _audit(db: Session, user: User, action: str, target: str, meta: dict) -> None:
    db.add(AuditLog(user_id=user.id, action=f"hardware_setup.{action}", resource="flight_controller", resource_id=target[:100],
                    event_metadata=meta))
    db.commit()


def _fc(fc_id: str) -> str:
    if not FC_ID_RE.match(fc_id):
        raise SetupError("BAD_FC_ID", "Unknown flight controller id", 422)
    return fc_id


# --------------------------------------------------------------- bridge process

class BridgeProcess:
    """The bridge started from the Drone Setup page (one per backend)."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.started_at: float | None = None
        self._lock = threading.Lock()

    def _local(self) -> bool:
        return urlparse(get_settings().HWBRIDGE_URL).hostname in ("127.0.0.1", "localhost")

    def status(self) -> dict:
        try:
            health = hw_client().health()
            running, error = True, None
        except HwBridgeError as exc:
            health, running, error = None, False, str(exc)
        ours = self.proc is not None and self.proc.poll() is None
        pid = bridge_pid()
        return {"running": running, "starting": not running and pid is not None, "started_here": ours or pid is not None,
                "pid": pid or (self.proc.pid if ours else None), "error": error,
                "can_start": self._local() and not os.path.exists("/.dockerenv"), "health": health, "log_tail": self.log_tail()}

    def log_tail(self, lines: int = 25) -> list[str]:
        try:
            with BRIDGE_LOG.open(encoding="utf-8", errors="replace") as f:
                return [ln.rstrip() for ln in deque(f, maxlen=lines)]
        except OSError:
            return []

    def start(self) -> dict:
        with self._lock:
            st = self.status()
            if st["running"] or st["starting"]:
                return st  # never a second copy: two bridges fight over the COM ports
            if not st["can_start"]:
                raise SetupError("CANNOT_START", "The bridge must run on the laptop the radios are plugged into. Start it there with "
                                 "'python -m hwbridge.server'.", 409)
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
            log = BRIDGE_LOG.open("a", encoding="utf-8")
            log.write(f"\n--- started from Drone MatrX {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            log.flush()
            # The bridge keeps running if the backend restarts, so drone links survive a reload.
            self.proc = subprocess.Popen([sys.executable, "-m", "hwbridge.server"], cwd=str(REPO_ROOT), stdout=log,
                                         stderr=subprocess.STDOUT, creationflags=flags)
            self.started_at = time.time()
            deadline = time.time() + BRIDGE_START_TIMEOUT_S
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    raise SetupError("BRIDGE_EXITED", "The bridge stopped right after starting: " + " | ".join(self.log_tail(6)), 409)
                st = self.status()
                if st["running"]:
                    return st
                time.sleep(0.5)
            # Still alive but slow: report "starting" (the page keeps polling) instead of
            # an error that invites a second start.
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            pid = bridge_pid()
            if (self.proc is None or self.proc.poll() is not None) and pid is None:
                raise SetupError("NOT_STARTED_HERE", "This bridge was not started from Drone MatrX; close its window to stop it", 409)
            try:
                c = hw_client().controllers()
                if any(x.get("armed") for x in c):
                    raise SetupError("ARMED", "A drone is armed; land and disarm before stopping the bridge", 409)
            except HwBridgeError:
                pass
            if self.proc is not None and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            if pid is not None and _pid_alive(pid):  # started by an earlier backend run
                os.kill(pid, signal.SIGTERM)
                deadline = time.time() + 10
                while time.time() < deadline and _pid_alive(pid):
                    time.sleep(0.2)
            try:
                BRIDGE_PID.unlink()
            except OSError:
                pass
            self.proc = None
            return self.status()


bridge_process = BridgeProcess()


# --------------------------------------------------------------- setup actions

def connect(db: Session, user: User, endpoint: str, baud: int) -> dict:
    out = _bridge(hw_client().add_link, endpoint, baud)
    _audit(db, user, "connect", endpoint, {"baud": baud})
    return out


def disconnect(db: Session, user: User, name: str) -> dict:
    if not LINK_NAME_RE.match(name):
        raise SetupError("BAD_LINK", "Unknown connection", 422)
    out = _bridge(hw_client().remove_link, name)
    _audit(db, user, "disconnect", name, {})
    return out


def write_params(db: Session, user: User, fc_id: str, values: dict[str, float]) -> dict:
    _fc(fc_id)
    if not values:
        raise SetupError("NO_VALUES", "Nothing to write", 422)
    refused = [k for k in values if not WRITABLE_PARAM_RE.match(k)]
    if refused:
        raise SetupError("PARAM_NOT_ALLOWED", f"The guided setup does not change {', '.join(refused)}", 422)
    out = _bridge(hw_client().set_params, fc_id, values)
    _audit(db, user, "params", fc_id, {"values": values})
    return out


def set_sysid(db: Session, user: User, fc_id: str, system_id: int) -> dict:
    out = _bridge(hw_client().set_sysid, _fc(fc_id), system_id)
    _audit(db, user, "system_id", fc_id, {"previous": out.get("previous"), "system_id": system_id})
    return out


def reboot(db: Session, user: User, fc_id: str) -> dict:
    out = _bridge(hw_client().reboot, _fc(fc_id))
    _audit(db, user, "reboot", fc_id, {})
    return out


def calibration(db: Session, user: User, fc_id: str, action: str) -> dict:
    if action not in CALIBRATIONS:
        raise SetupError("BAD_CALIBRATION", "Unknown calibration step", 422)
    out = _bridge(hw_client().calibration, _fc(fc_id), action)
    if action != "accel-next":
        _audit(db, user, f"calibration.{action}", fc_id, {})
    return out


def motor_test(db: Session, user: User, fc_id: str, motor: int, throttle_pct: float, seconds: float) -> dict:
    out = _bridge(hw_client().motor_test, _fc(fc_id), motor, throttle_pct, seconds)
    _audit(db, user, "motor_test", fc_id, {"motor": motor, "throttle_pct": throttle_pct, "seconds": seconds, "propellers_removed": True})
    return out


def radio_info(port: str) -> dict:
    if not PORT_RE.match(port):
        raise SetupError("BAD_PORT", "Port must look like COM5", 422)
    return _bridge(hw_client().radio_info, port)


def radio_netid(db: Session, user: User, port: str, netid: int) -> dict:
    if not PORT_RE.match(port):
        raise SetupError("BAD_PORT", "Port must look like COM5", 422)
    out = _bridge(hw_client().radio_netid, port, netid)
    _audit(db, user, "radio_netid", port, {"netid": netid, "previous": out.get("remote_was")})
    return out


def set_camera(db: Session, user: User, sysid: int, url: str | None) -> dict:
    out = _bridge(hw_client().set_camera, sysid, url)
    # The URL can contain the camera's password: audit only the host.
    _audit(db, user, "camera", str(sysid), {"host": urlparse(url).hostname if url else None})
    return out


def setup_view(fc_id: str, refresh: bool) -> dict:
    return _bridge(hw_client().setup, _fc(fc_id), refresh)


def ports() -> dict:
    return _bridge(hw_client().ports)


def controllers() -> list[dict]:
    return _bridge(hw_client().controllers)


# One-click fixes from the Mission Control pre-arm checklist (same values as
# Drone Setup step 8, minus the per-drone RTL height and battery voltages).
SAFETY_FIXES: dict[str, dict[str, float]] = {
    "failsafes": {"FS_GCS_ENABLE": 1, "FS_GCS_TIMEOUT": 5, "FS_THR_ENABLE": 1, "BATT_FS_LOW_ACT": 2, "BATT_FS_CRT_ACT": 1},
    "fence": {"FENCE_ENABLE": 1, "FENCE_TYPE": 7, "FENCE_ACTION": 1, "FENCE_ALT_MAX": 40, "FENCE_RADIUS": 150},
}


def fix_drone_safety(db: Session, user: User, drone_id, kind: str) -> dict:
    from app.models.drone import Drone
    from app.services.mission_control.hwbridge import parse_identity

    if kind not in SAFETY_FIXES:
        raise SetupError("BAD_FIX", "Unknown fix", 422)
    drone = db.get(Drone, drone_id)
    if drone is None or drone.organization_id != user.organization_id or not drone.active:
        raise SetupError("DRONE_NOT_FOUND", "Drone not found", 404)
    if not drone.hardware:
        raise SetupError("NOT_HARDWARE", f"{drone.name} is not a real aircraft")
    from app.services.mission_control import fc as fcsvc_mod

    b = fcsvc_mod.active_binding(db, drone.id)
    hw = parse_identity(b.endpoint) if b is not None and b.state == "bound" else None
    if hw is None:
        raise SetupError("NOT_BOUND", f"{drone.name} has no bound flight controller")
    return {"drone_id": str(drone.id), "kind": kind, **write_params(db, user, hw["fc_id"], SAFETY_FIXES[kind])}
