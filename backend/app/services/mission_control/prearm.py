"""Pre-arm checklist, computed by the backend from what the flight controller
and the telemetry pipeline actually report. A value that was never received
is UNKNOWN, never assumed good. Arming is allowed only when every required
check is PASS: a required FAIL or UNKNOWN blocks it, and the arm command
re-runs this checklist immediately before dispatch.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.drone import Drone
from app.models.mission_control import FlightControllerBinding
from app.services.mission_control.fleet import connection_state, now_utc
from app.services.spatial_validation import SpatialValidator

PASS, FAIL, UNKNOWN, WARNING = "PASS", "FAIL", "UNKNOWN", "WARNING"
# MAV_SYS_STATUS_SENSOR bits
AHRS = 0x200000
GPS = 0x20
PREARM_CHECK = 0x10000000
NOT_ARMABLE_MODES = ("RTL", "LAND", "OFFBOARD", "UNKNOWN", "PRECLAND")
# ArduCopter: the bridge arms in GUIDED; these modes cannot (or must not) be armed in.
ARDUPILOT_NOT_ARMABLE = ("RTL", "LAND", "SMART_RTL", "AUTO_RTL", "BRAKE", "AUTO", "UNKNOWN")
RC_RECEIVER = 0x10000


def zone_check(db: Session, org_id: uuid.UUID, lon: float, lat: float, path_from: tuple[float, float] | None = None) -> dict:
    """Geofence / NFZ status of a point (and optionally the straight path to it)."""
    v = SpatialValidator(db, org_id)
    point = {"type": "Point", "coordinates": [lon, lat]}
    blocked = [z.name for z in v.covering(point, ["nfz", "exclusion"])]
    path_hits: list[str] = []
    if path_from is not None and (path_from[0], path_from[1]) != (lon, lat):
        line = {"type": "LineString", "coordinates": [[path_from[0], path_from[1]], [lon, lat]]}
        path_hits = [z.name for z, _ in v.intersecting(line, ["nfz", "exclusion"])]
    stmt = v._zones(["inclusion"])
    inclusion = list(db.execute(stmt).scalars()) if stmt is not None else []
    inside_inclusion = True
    if inclusion:
        inside_inclusion = bool(v.covering(point, ["inclusion"]))
    return {"forbidden_zones": blocked, "path_crosses": sorted(set(path_hits) - set(blocked)), "inclusion_zones": len(inclusion),
            "inside_inclusion": inside_inclusion,
            "ok": not blocked and not path_hits and inside_inclusion}


def _check(cid: str, label: str, status: str, detail: str, required: bool = True) -> dict:
    return {"id": cid, "label": label, "status": status, "detail": detail, "required": required}


def checklist(db: Session, drone: Drone, binding: FlightControllerBinding | None, live: Any, fc: dict | None,
              fc_error: str | None, expected_mode: str | None, received: Any = None, permission: Any = None) -> dict:
    s = get_settings()
    checks: list[dict] = []
    source = ("sitl" if binding.connection_type == "sitl" else "hardware") if binding is not None else None
    ardupilot = (fc or {}).get("autopilot") == "ardupilot"

    # 1. Flight controller connection
    if binding is None:
        checks.append(_check("fc_connected", "Flight controller connected", FAIL, "No flight controller is bound to this drone"))
    elif binding.state != "bound":
        checks.append(_check("fc_connected", "Flight controller connected", FAIL, f"Binding is {binding.state}: {binding.error or ''}".strip()))
    elif fc is None:
        checks.append(_check("fc_connected", "Flight controller connected", UNKNOWN, fc_error or "Controller health unavailable"))
    else:
        age = fc.get("heartbeat_age_s")
        # PX4 heartbeats at 1 Hz of *simulated* time: allow the telemetry staleness window.
        ok = age is not None and age < s.MC_TELEMETRY_STALE_S and not fc.get("closed")
        checks.append(_check("fc_connected", "Flight controller connected", PASS if ok else FAIL,
                             f"Heartbeat {age:.1f} s ago" if age is not None else "No heartbeat received"))

    # 2. Telemetry freshness (the pipeline the operator is looking at)
    last = (received or (live.timestamp if live is not None else None)) or drone.last_telemetry_at
    state, age = connection_state(last, now_utc())
    checks.append(_check("telemetry_fresh", "Telemetry fresh", PASS if state == "connected" else FAIL,
                         f"Last telemetry {age:.1f} s ago (limit {s.MC_TELEMETRY_STALE_S:g} s)" if age is not None else "No telemetry received"))

    # 3. Identity: the controller answering is the one bound to this drone
    if fc is None or binding is None:
        checks.append(_check("identity", "Correct drone identity", UNKNOWN, "Controller identity not available"))
    else:
        same = fc.get("drone_id") == str(drone.id) and (binding.system_id is None or fc.get("system_id") == binding.system_id)
        checks.append(_check("identity", "Correct drone identity", PASS if same else FAIL,
                             f"MAVLink sysid {fc.get('system_id')} routed to {drone.name}" if same else
                             f"Controller reports drone {fc.get('drone_id')} / sysid {fc.get('system_id')}"))

    # 4. Flight mode and armed state
    if fc is None:
        checks.append(_check("flight_mode", "Appropriate flight mode", UNKNOWN, "Mode not available"))
    elif fc.get("armed"):
        checks.append(_check("flight_mode", "Appropriate flight mode", FAIL, "Already armed"))
    else:
        mode = fc.get("mode") or "UNKNOWN"
        bad = ARDUPILOT_NOT_ARMABLE if ardupilot else NOT_ARMABLE_MODES
        checks.append(_check("flight_mode", "Appropriate flight mode", FAIL if mode in bad else PASS, f"Mode {mode}"))

    # 5. On the ground
    if fc is None or fc.get("landed_state") in (None, 0):
        checks.append(_check("on_ground", "On the ground", UNKNOWN, "Landed state not reported"))
    else:
        checks.append(_check("on_ground", "On the ground", PASS if fc["landed_state"] == 1 else FAIL, f"MAV_LANDED_STATE {fc['landed_state']}"))

    # 6. Battery
    pct = fc.get("battery_pct") if fc else None
    min_pct = s.HW_PREARM_MIN_BATTERY_PCT if source == "hardware" else s.MC_PREARM_MIN_BATTERY_PCT
    if pct is None:
        checks.append(_check("battery", "Battery available and sufficient", UNKNOWN, "Battery level not reported"))
    else:
        volts = f", {fc['battery_v']:.1f} V" if fc.get("battery_v") else ""
        checks.append(_check("battery", "Battery available and sufficient", PASS if pct >= min_pct else FAIL,
                             f"{pct:.0f} %{volts} (minimum {min_pct:.0f} %)"))

    # 7. Positioning
    if fc is None or fc.get("gps_fix") is None:
        checks.append(_check("positioning", "GNSS fix and home position", UNKNOWN, "GNSS state not reported"))
    else:
        ok = fc["gps_fix"] >= 3 and (fc.get("satellites") or 0) >= 6 and fc.get("home_set")
        checks.append(_check("positioning", "GNSS fix and home position", PASS if ok else FAIL,
                             f"fix type {fc['gps_fix']}, {fc.get('satellites')} satellites, home {'set' if fc.get('home_set') else 'not set'}"))

    # 8. Estimator / sensor health (SYS_STATUS)
    present, health = (fc or {}).get("sensors_present"), (fc or {}).get("sensors_health")
    if present is None or health is None or not present & AHRS:
        checks.append(_check("estimator", "Attitude/position estimator healthy", UNKNOWN, "SYS_STATUS health not reported"))
    else:
        checks.append(_check("estimator", "Attitude/position estimator healthy", PASS if health & AHRS else FAIL,
                             "AHRS healthy" if health & AHRS else "AHRS reported unhealthy"))

    # 9. Firmware pre-arm checks. PX4 (v1.13+) sets MAV_SYS_STATUS_PREARM_CHECK
    # only in the *health* mask, exactly when it can arm in the current mode
    # (never in `present`); other firmware must declare the bit as present.
    px4 = str((fc or {}).get("firmware") or "").startswith("PX4")
    if health is None or (not px4 and (present is None or not present & PREARM_CHECK)):
        checks.append(_check("firmware_prearm", "Firmware pre-arm checks", UNKNOWN, "Firmware pre-arm status not reported"))
    else:
        fails = [t["text"] for t in (fc.get("statustext") or []) if any(k in t["text"].lower() for k in ("preflight fail", "arming denied", "prearm:"))]
        ok = bool(health & PREARM_CHECK)
        fw = "ArduPilot" if ardupilot else "PX4"
        checks.append(_check("firmware_prearm", "Firmware pre-arm checks", PASS if ok else FAIL,
                             f"{fw} pre-arm checks pass" if ok else (f"{fw}: " + "; ".join(fails[-3:]) if fails else f"{fw} reports pre-arm checks failing")))

    # 10. Operating area: geofences and no-fly zones at the drone's position
    lat = live.latitude if live is not None else None
    lon = live.longitude if live is not None else None
    if lat is None or lon is None:
        checks.append(_check("operating_area", "Geofence / no-fly zones", UNKNOWN, "Position unknown"))
    else:
        z = zone_check(db, drone.organization_id, lon, lat)
        if z["forbidden_zones"]:
            checks.append(_check("operating_area", "Geofence / no-fly zones", FAIL, f"Inside {', '.join(z['forbidden_zones'])}"))
        elif not z["inside_inclusion"]:
            checks.append(_check("operating_area", "Geofence / no-fly zones", FAIL, "Outside every inclusion geofence"))
        else:
            checks.append(_check("operating_area", "Geofence / no-fly zones", PASS,
                                 f"Clear of NFZ/exclusion zones ({z['inclusion_zones']} inclusion zone(s) checked)"))

    # 11. Failsafes configured on the controller
    params = (fc or {}).get("failsafe_params") or {}
    if ardupilot:
        _ardupilot_failsafes(checks, params)
    else:
        dll, lowbat = params.get("NAV_DLL_ACT"), params.get("COM_LOW_BAT_ACT")
        if dll is None or lowbat is None:
            checks.append(_check("failsafes", "Failsafes configured", UNKNOWN, "Failsafe parameters not readable"))
        else:
            ok = dll != 0 and lowbat != 0
            checks.append(_check("failsafes", "Failsafes configured", PASS if ok else FAIL,
                                 f"link loss NAV_DLL_ACT={dll}, low battery COM_LOW_BAT_ACT={lowbat}"))
        gf = params.get("GF_ACTION")
        if gf is not None:
            checks.append(_check("fc_geofence", "Onboard geofence action", PASS if gf != 0 else WARNING,
                                 f"GF_ACTION={gf}" + ("" if gf != 0 else " (no onboard geofence action; backend zones still checked)"), required=False))

    # 12. Simulation / hardware mode confirmed by the operator
    if source is None:
        checks.append(_check("mode_confirmed", "Simulation / hardware mode confirmed", UNKNOWN, "No controller bound"))
    elif expected_mode is None:
        checks.append(_check("mode_confirmed", "Simulation / hardware mode confirmed", UNKNOWN,
                             f"Operator must confirm this is a {source.upper()} drone"))
    else:
        checks.append(_check("mode_confirmed", "Simulation / hardware mode confirmed", PASS if expected_mode == source else FAIL,
                             f"Confirmed {expected_mode.upper()}, controller is {source.upper()}"))

    # 13. Real aircraft: the RC safety pilot must be able to take over, no
    # override may be active, and flying needs a human flight permission.
    if source == "hardware":
        _hardware_checks(checks, fc, permission)

    required = [c for c in checks if c["required"]]
    blocking = [c for c in required if c["status"] != PASS]
    return {"drone_id": str(drone.id), "drone_name": drone.name, "mode": source, "checked_at": now_utc().isoformat(),
            "can_arm": not blocking, "blocking": [c["id"] for c in blocking], "checks": checks}


def _ardupilot_failsafes(checks: list[dict], params: dict) -> None:
    gcs, rc, batt = params.get("FS_GCS_ENABLE"), params.get("FS_THR_ENABLE"), params.get("BATT_FS_LOW_ACT")
    if gcs is None or rc is None or batt is None:
        checks.append(_check("failsafes", "Failsafes configured", UNKNOWN, "Failsafe parameters not readable over the radio yet"))
    else:
        missing = [n for n, v in (("ground-station loss FS_GCS_ENABLE", gcs), ("RC loss FS_THR_ENABLE", rc), ("low battery BATT_FS_LOW_ACT", batt)) if not v]
        checks.append(_check("failsafes", "Failsafes configured", FAIL if missing else PASS,
                             ("Not set: " + ", ".join(missing)) if missing else f"GCS loss={gcs:g}, RC loss={rc:g}, low battery={batt:g}"))
    fence = params.get("FENCE_ENABLE")
    if fence is not None:
        detail = f"FENCE_ENABLE={fence:g}"
        if fence:
            detail += f", max alt {params.get('FENCE_ALT_MAX') or 0:g} m, radius {params.get('FENCE_RADIUS') or 0:g} m, action {params.get('FENCE_ACTION') or 0:g}"
        checks.append(_check("fc_geofence", "Onboard geofence (ArduPilot fence)", PASS if fence else WARNING,
                             detail + ("" if fence else " (no onboard fence; backend zones still checked)"), required=False))


def _hardware_checks(checks: list[dict], fc: dict | None, permission: Any) -> None:
    import time as _time

    rc_ok = (fc or {}).get("rc_receiver_ok")
    if rc_ok is None:
        checks.append(_check("rc_link", "RC transmitter connected (manual override available)", UNKNOWN,
                             "RC receiver status not reported by the flight controller"))
    else:
        rssi = (fc or {}).get("rc_rssi")
        checks.append(_check("rc_link", "RC transmitter connected (manual override available)", PASS if rc_ok else FAIL,
                             ("RC receiver healthy" + (f", RSSI {rssi}" if rssi not in (None, 255) else "")) if rc_ok
                             else "RC receiver not healthy: switch on the transmitter; the safety pilot must be able to take over"))
    ov = (fc or {}).get("override")
    if fc is None:
        checks.append(_check("pilot_override", "No pilot / autopilot override active", UNKNOWN, "Controller state unavailable"))
    else:
        checks.append(_check("pilot_override", "No pilot / autopilot override active", FAIL if ov else PASS,
                             f"Override: {ov.get('reason')}" if ov else "Drone MatrX may send commands"))
    if permission is not None:
        left = max(0, int((permission.expires_at.timestamp() - _time.time()) // 60))
        checks.append(_check("flight_permission", "Human flight permission", PASS, f"Granted, {left} min left", required=False))
    else:
        checks.append(_check("flight_permission", "Human flight permission", WARNING,
                             "Not granted: ARM, take-off, GO TO and missions are refused until an operator grants it", required=False))
