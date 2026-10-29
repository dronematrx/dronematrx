"""Human flight permission, pilot-override release and CBBA missions on real aircraft.

Nothing flies a real aircraft without a person deciding it should:

* After binding, pre-arm checks and any planning (CBBA), ARM / TAKEOFF /
  GOTO / MISSION_START on a hardware drone are refused until an operator
  grants a flight permission for that drone. Granting needs the safety
  acknowledgements (RC safety pilot ready, area clear, observer present,
  legal clearance, weather) and a passing pre-arm checklist; it lasts at most
  HW_FLIGHT_PERMISSION_MAX_MIN minutes, is mirrored on the hardware bridge
  (which refuses on its own when it expires), and every grant / revoke is
  audited.
* A pilot / autopilot override (RC mode switch, failsafe, stick arming)
  locks out automated commands. Releasing it is an explicit, audited action.
* A CBBA plan is flown on hardware by uploading the drone's bundle as an
  ArduPilot mission (checked against the bridge's altitude/distance limits)
  and then MISSION_START -- which itself needs the flight permission.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.drone import Drone
from app.models.mission_control import FlightPermission
from app.models.user import User
from app.services.mission_control import fc as fcsvc
from app.services.mission_control.fleet import now_utc
from app.services.mission_control.hwbridge import HwBridgeError, parse_identity

REQUIRED_ACKS = {
    "rc_pilot_ready": "A safety pilot holds the RC transmitter, powered on and bound, and can take over at any moment",
    "area_clear": "The take-off area and flight area are clear of people, vehicles and obstacles",
    "observer_present": "A visual observer keeps the aircraft in sight for the whole flight",
    "legal_ok": "The flight is legally permitted here (airspace zone / permission, registration)",
    "weather_ok": "Wind and weather are within the aircraft's limits",
}


class FlightPermissionError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, extra: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra or {}


def active_permission(db: Session, drone_id: uuid.UUID) -> FlightPermission | None:
    return db.execute(select(FlightPermission).where(
        FlightPermission.drone_id == drone_id, FlightPermission.revoked_at.is_(None), FlightPermission.expires_at > now_utc())
        .order_by(FlightPermission.expires_at.desc())).scalars().first()


def permission_view(p: FlightPermission | None) -> dict | None:
    if p is None:
        return None
    remaining = (p.expires_at - now_utc()).total_seconds()
    return {"id": str(p.id), "drone_id": str(p.drone_id), "mission_id": str(p.mission_id) if p.mission_id else None,
            "granted_by": str(p.granted_by) if p.granted_by else None, "granted_at": p.granted_at.isoformat() if p.granted_at else None,
            "expires_at": p.expires_at.isoformat(), "remaining_s": max(0, round(remaining)), "active": p.revoked_at is None and remaining > 0,
            "revoked_at": p.revoked_at.isoformat() if p.revoked_at else None, "acknowledgements": p.acknowledgements, "note": p.note}


def _hardware_binding(db: Session, user: User, drone_id: uuid.UUID) -> tuple[Drone, dict]:
    drone = db.get(Drone, drone_id)
    if drone is None or drone.organization_id != user.organization_id or not drone.active:
        raise FlightPermissionError("DRONE_NOT_FOUND", "Drone not found", 404)
    if not drone.hardware:
        raise FlightPermissionError("NOT_HARDWARE", f"{drone.name} is not a real aircraft; flight permissions apply to hardware drones")
    b = fcsvc.active_binding(db, drone.id)
    hw = parse_identity(b.endpoint) if b is not None and b.state == "bound" else None
    if hw is None:
        raise FlightPermissionError("NOT_BOUND", f"{drone.name} has no bound flight controller")
    return drone, hw


def grant(db: Session, user: User, drone_id: uuid.UUID, minutes: int, acks: dict, prearm: dict, mission_id: uuid.UUID | None = None,
          note: str | None = None) -> dict:
    s = get_settings()
    drone, hw = _hardware_binding(db, user, drone_id)
    missing = [k for k in REQUIRED_ACKS if acks.get(k) is not True]
    if missing:
        raise FlightPermissionError("ACKNOWLEDGEMENTS_MISSING", "All safety acknowledgements are required: " + ", ".join(missing), 422,
                               {"missing": missing, "required": REQUIRED_ACKS})
    if not 1 <= minutes <= s.HW_FLIGHT_PERMISSION_MAX_MIN:
        raise FlightPermissionError("INVALID_DURATION", f"Permission lasts 1-{s.HW_FLIGHT_PERMISSION_MAX_MIN} minutes", 422)
    # On the ground every required pre-arm check must pass; airborne (extending a
    # permission during a flight) only the override lock is checked.
    if not drone.armed and not prearm.get("can_arm"):
        raise FlightPermissionError("PREARM_FAILED", "Pre-arm checks must pass before granting a flight permission: " + ", ".join(prearm.get("blocking", [])),
                               409, {"prearm": prearm})
    if any(c["id"] == "pilot_override" and c["status"] != "PASS" for c in prearm.get("checks", [])):
        raise FlightPermissionError("PILOT_OVERRIDE", "Release the pilot override before granting a flight permission")
    for old in db.execute(select(FlightPermission).where(FlightPermission.drone_id == drone.id, FlightPermission.revoked_at.is_(None),
                                                         FlightPermission.expires_at > now_utc())).scalars():
        old.revoked_at, old.revoked_by, old.revoke_reason = now_utc(), user.id, "replaced by a new permission"
    expires = now_utc() + timedelta(minutes=minutes)
    p = FlightPermission(organization_id=user.organization_id, drone_id=drone.id, mission_id=mission_id, granted_by=user.id, expires_at=expires,
                         acknowledgements={k: True for k in REQUIRED_ACKS}, note=note)
    db.add(p)
    db.flush()
    try:
        fcsvc.hw_client().authorize(hw["fc_id"], expires.timestamp(), user.email)
    except HwBridgeError as exc:
        db.rollback()
        raise FlightPermissionError("BRIDGE_UNAVAILABLE", f"Hardware bridge did not accept the permission: {exc}") from exc
    db.add(AuditLog(user_id=user.id, action="flight_permission_granted", resource="drone", resource_id=str(drone.id),
                    event_metadata={"minutes": minutes, "expires_at": expires.isoformat(), "acknowledgements": list(REQUIRED_ACKS),
                                    "mission_id": str(mission_id) if mission_id else None, "note": note}))
    db.commit()
    db.refresh(p)
    return permission_view(p)


def revoke(db: Session, user: User, drone_id: uuid.UUID, reason: str | None = None) -> dict:
    drone = db.get(Drone, drone_id)
    if drone is None or drone.organization_id != user.organization_id:
        raise FlightPermissionError("DRONE_NOT_FOUND", "Drone not found", 404)
    p = active_permission(db, drone.id)
    if p is not None:
        p.revoked_at, p.revoked_by, p.revoke_reason = now_utc(), user.id, (reason or "revoked by operator")[:200]
    b = fcsvc.active_binding(db, drone.id)
    hw = parse_identity(b.endpoint) if b is not None else None
    bridge_error = None
    if hw is not None:
        try:
            fcsvc.hw_client().revoke(hw["fc_id"])
        except HwBridgeError as exc:
            bridge_error = str(exc)  # the permission still expires on the bridge at its own deadline
    db.add(AuditLog(user_id=user.id, action="flight_permission_revoked", resource="drone", resource_id=str(drone.id),
                    event_metadata={"reason": reason, "bridge_error": bridge_error}))
    db.commit()
    return {"revoked": p is not None, "bridge_error": bridge_error}


def release_override(db: Session, user: User, drone_id: uuid.UUID, reason: str) -> dict:
    drone, hw = _hardware_binding(db, user, drone_id)
    if not reason or len(reason.strip()) < 3:
        raise FlightPermissionError("REASON_REQUIRED", "Say why the override can be released (e.g. 'pilot handed control back')", 422)
    try:
        info = fcsvc.hw_client().release_override(hw["fc_id"], user.email)
    except HwBridgeError as exc:
        raise FlightPermissionError("BRIDGE_UNAVAILABLE", str(exc)) from exc
    db.add(AuditLog(user_id=user.id, action="pilot_override_released", resource="drone", resource_id=str(drone.id),
                    event_metadata={"reason": reason[:200]}))
    db.commit()
    return info


def upload_mission(db: Session, user: User, drone_id: uuid.UUID, mission_id: uuid.UUID, live, altitude_m: float | None = None,
                   speed_mps: float | None = None) -> dict:
    """Fly this drone's CBBA bundle: build the waypoint mission from the latest
    plan of `mission_id` and upload it to the aircraft. Starting it is a
    separate MISSION_START command that needs the flight permission.

    `altitude_m` overrides the mission altitude for this drone. With several
    real aircraft give each its own altitude: no collision avoidance runs on
    hardware, so vertical separation keeps crossing lanes apart."""
    from app.models.mission import Mission
    from app.models.mission_planning import MissionPlan, MissionTask
    from app.services.simulation.routes import build_mission, task_geometries_from_rows

    drone, hw = _hardware_binding(db, user, drone_id)
    mission = db.get(Mission, mission_id)
    if mission is None or mission.organization_id != user.organization_id:
        raise FlightPermissionError("MISSION_NOT_FOUND", "Mission not found", 404)
    plan = db.execute(select(MissionPlan).where(MissionPlan.mission_id == mission.id).order_by(MissionPlan.created_at.desc())).scalars().first()
    if plan is None:
        raise FlightPermissionError("NO_PLAN", "Run CBBA planning for this mission first")
    entry = ((plan.diagnostics or {}).get("agent_state") or {}).get(str(drone.id))
    path = [(k, int(o)) for k, o, _bid in (entry or {}).get("path", [])]
    if not path:
        raise FlightPermissionError("NO_TASKS", f"The CBBA plan assigns no tasks to {drone.name}; include it in the plan")
    if live is None or live.latitude is None:
        raise FlightPermissionError("NO_POSITION", f"{drone.name} has no live position (telemetry) to start from")
    rows = db.execute(select(MissionTask).where(MissionTask.mission_id == mission.id)).scalars().all()
    flight = (mission.config or {}).get("flight", {})
    alt = float(altitude_m if altitude_m is not None else flight.get("altitude_m", 20))
    speed = float(speed_mps if speed_mps is not None else flight.get("speed_mps", 5))
    built = build_mission((live.longitude, live.latitude), path, task_geometries_from_rows(rows), alt, speed, rth_on_complete=True)
    try:
        out = fcsvc.hw_client().upload_mission(hw["fc_id"], built.items)
    except HwBridgeError as exc:
        raise FlightPermissionError(exc.code or "UPLOAD_FAILED", str(exc)) from exc
    db.add(AuditLog(user_id=user.id, action="hardware_mission_uploaded", resource="drone", resource_id=str(drone.id),
                    event_metadata={"mission_id": str(mission.id), "plan_id": str(plan.id), "items": len(built.items),
                                    "tasks": built.task_keys, "altitude_m": alt, "speed_mps": speed}))
    db.commit()
    return {"drone_id": str(drone.id), "mission_id": str(mission.id), "plan_id": str(plan.id), "uploaded": out.get("uploaded"),
            "tasks": built.task_keys, "planned_route": built.planned_route, "altitude_m": alt, "speed_mps": speed}
