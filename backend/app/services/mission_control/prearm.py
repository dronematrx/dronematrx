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
              fc_error: str | None, expected_mode: str | None, received: Any = None) -> dict:
    s = get_settings()
    checks: list[dict] = []
    source = ("sitl" if binding.connection_type == "sitl" else "hardware") if binding is not None else None

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
        checks.append(_check("flight_mode", "Appropriate flight mode", FAIL if mode in NOT_ARMABLE_MODES else PASS, f"Mode {mode}"))

    # 5. On the ground
    if fc is None or fc.get("landed_state") in (None, 0):
        checks.append(_check("on_ground", "On the ground", UNKNOWN, "Landed state not reported"))
    else:
        checks.append(_check("on_ground", "On the ground", PASS if fc["landed_state"] == 1 else FAIL, f"MAV_LANDED_STATE {fc['landed_state']}"))

    # 6. Battery
    pct = fc.get("battery_pct") if fc else None
    if pct is None:
        checks.append(_check("battery", "Battery available and sufficient", UNKNOWN, "Battery level not reported"))
    else:
        checks.append(_check("battery", "Battery available and sufficient", PASS if pct >= s.MC_PREARM_MIN_BATTERY_PCT else FAIL,
                             f"{pct:.0f} % (minimum {s.MC_PREARM_MIN_BATTERY_PCT:.0f} %)"))

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
        fails = [t["text"] for t in (fc.get("statustext") or []) if "preflight fail" in t["text"].lower() or "arming denied" in t["text"].lower()]
        ok = bool(health & PREARM_CHECK)
        checks.append(_check("firmware_prearm", "Firmware pre-arm checks", PASS if ok else FAIL,
                             "PX4 pre-arm checks pass" if ok else ("PX4: " + "; ".join(fails[-3:]) if fails else "PX4 reports pre-arm checks failing")))

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

    required = [c for c in checks if c["required"]]
    blocking = [c for c in required if c["status"] != PASS]
    return {"drone_id": str(drone.id), "drone_name": drone.name, "mode": source, "checked_at": now_utc().isoformat(),
            "can_arm": not blocking, "blocking": [c["id"] for c in blocking], "checks": checks}
