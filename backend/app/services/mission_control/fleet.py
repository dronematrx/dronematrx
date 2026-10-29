"""Mission-control fleet view built from the existing fleet, telemetry and
mission tables. It adds what the dashboard needs and the drone row does not
say: where each drone's telemetry actually comes from, and whether it is
fresh.

Telemetry source (never guessed from the drone's name):

    sitl            PX4 SITL flight controller bound to the drone (simhost bench)
    hardware        hardware flight controller bound to the drone (adapter locked)
    kinematic_demo  the Sprint 4 in-process kinematic simulator (no flight controller)
    none            nothing is producing telemetry for this drone
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.drone import Drone
from app.models.mission import Mission
from app.models.mission_control import FlightControllerBinding
from app.models.mission_planning import MissionPlan, MissionTask, TaskAssignment

ACTIVE_BINDING = ("binding", "bound")
MISSION_LIVE_STATES = ("armed", "active")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def kinematic_drone_ids(runtime: Any) -> set[str]:
    sim = getattr(runtime, "simulator", None) if runtime is not None and getattr(runtime, "running", False) else None
    return {s.drone_id for s in getattr(sim, "states", [])} if sim is not None else set()


def telemetry_source(drone_id: str, binding: FlightControllerBinding | None, kinematic: set[str]) -> str:
    if binding is not None and binding.state in (*ACTIVE_BINDING, "connection_failed"):
        return "sitl" if binding.connection_type == "sitl" else "hardware"
    if drone_id in kinematic:
        return "kinematic_demo"
    return "none"


def connection_state(last: datetime | None, now: datetime, stale_s: float | None = None, offline_s: float | None = None) -> tuple[str, float | None]:
    """connected | stale | offline | no_telemetry, plus the telemetry age in seconds."""
    s = get_settings()
    stale_s = s.MC_TELEMETRY_STALE_S if stale_s is None else stale_s
    offline_s = s.MC_TELEMETRY_OFFLINE_S if offline_s is None else offline_s
    if last is None:
        return "no_telemetry", None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = max(0.0, (now - last).total_seconds())
    if age <= stale_s:
        return "connected", age
    if age <= offline_s:
        return "stale", age
    return "offline", age


def active_bindings(db: Session, org_id: uuid.UUID) -> dict[str, FlightControllerBinding]:
    """Current binding per drone: an active one, else the latest failed one (shown as CONNECTION FAILED)."""
    rows = db.execute(select(FlightControllerBinding).where(
        FlightControllerBinding.organization_id == org_id,
        FlightControllerBinding.state.in_((*ACTIVE_BINDING, "connection_failed"))).order_by(FlightControllerBinding.created_at)).scalars()
    out: dict[str, FlightControllerBinding] = {}
    for b in rows:
        cur = out.get(str(b.drone_id))
        if cur is None or cur.state not in ACTIVE_BINDING or b.state in ACTIVE_BINDING:
            out[str(b.drone_id)] = b
    return out


def mission_assignments(db: Session, org_id: uuid.UUID) -> dict[str, dict]:
    """Drone -> its live mission and current (first unfinished) task, from the current CBBA plan."""
    rows = db.execute(
        select(TaskAssignment.drone_id, Mission.id, Mission.name, Mission.status, MissionTask.task_key, MissionTask.status, TaskAssignment.assignment_order)
        .join(MissionPlan, TaskAssignment.plan_id == MissionPlan.id)
        .join(Mission, MissionPlan.mission_id == Mission.id)
        .join(MissionTask, TaskAssignment.task_id == MissionTask.id)
        .where(Mission.organization_id == org_id, MissionPlan.is_current.is_(True), Mission.status.in_(MISSION_LIVE_STATES),
               TaskAssignment.drone_id.is_not(None))
        .order_by(TaskAssignment.assignment_order)
    ).all()
    out: dict[str, dict] = {}
    for drone_id, mission_id, name, status, task_key, task_status, _order in rows:
        entry = out.setdefault(str(drone_id), {"mission_id": str(mission_id), "mission_name": name, "mission_status": status,
                                               "current_task": None, "tasks_total": 0, "tasks_done": 0})
        entry["tasks_total"] += 1
        if task_status == "completed":
            entry["tasks_done"] += 1
        elif entry["current_task"] is None:
            entry["current_task"] = task_key
    return out


def latest_telemetry(runtime: Any) -> dict[str, Any]:
    manager = getattr(runtime, "telemetry_manager", None) if runtime is not None else None
    return dict(getattr(manager, "latest", {}) or {})


def received_times(runtime: Any) -> dict[str, datetime]:
    manager = getattr(runtime, "telemetry_manager", None) if runtime is not None else None
    return dict(getattr(manager, "received_at", {}) or {})


def freshness_time(live: Any, received: datetime | None) -> datetime | None:
    """Backend receive time of the latest frame; the producer timestamp only when no receive time is known."""
    return received or (live.timestamp if live is not None else None)


def drone_view(drone: Drone, binding: FlightControllerBinding | None, kinematic: set[str], live: Any, mission: dict | None,
               now: datetime, received: datetime | None = None) -> dict:
    did = str(drone.id)
    last = freshness_time(live, received) or drone.last_telemetry_at
    state, age = connection_state(last, now)
    src = telemetry_source(did, binding, kinematic)
    t = live
    return {
        "id": did,
        "name": drone.name,
        "uin": drone.uin,
        "model": drone.model,
        "simulated": drone.simulated,
        "hardware": drone.hardware,
        "telemetry_source": src,
        "connection_state": state,
        "telemetry_age_s": round(age, 2) if age is not None else None,
        "last_telemetry_at": last.isoformat() if last else None,  # backend receive time (freshness)
        "telemetry_timestamp": live.timestamp.isoformat() if live is not None else None,  # producer's own time
        "status": t.status if t is not None else drone.status,
        "flight_mode": t.flight_mode if t is not None else drone.flight_mode,
        "armed": t.armed if t is not None else drone.armed,
        "battery": t.battery if t is not None else drone.battery,
        "latitude": t.latitude if t is not None else None,
        "longitude": t.longitude if t is not None else None,
        "altitude": t.altitude if t is not None else drone.altitude,
        "heading": t.heading if t is not None else drone.heading,
        "speed": t.speed if t is not None else drone.speed,
        "gps_status": t.gps_status if t is not None else drone.gps_status,
        "binding": binding_view(binding) if binding is not None else None,
        "mission": mission,
    }


def binding_view(b: FlightControllerBinding) -> dict:
    return {"id": str(b.id), "drone_id": str(b.drone_id), "fc_identity": b.fc_identity, "connection_type": b.connection_type,
            "endpoint": b.endpoint, "system_id": b.system_id, "firmware": b.firmware, "state": b.state, "error": b.error,
            "bound_via": b.bound_via, "sitl_session_id": b.sitl_session_id,
            "bound_at": b.bound_at.isoformat() if b.bound_at else None, "unbound_at": b.unbound_at.isoformat() if b.unbound_at else None}


def fleet(db: Session, org_id: uuid.UUID, runtime: Any) -> dict:
    now = now_utc()
    drones = db.execute(select(Drone).where(Drone.organization_id == org_id, Drone.active.is_(True)).order_by(Drone.name)).scalars().all()
    bindings = active_bindings(db, org_id)
    kinematic = kinematic_drone_ids(runtime)
    live = latest_telemetry(runtime)
    recv = received_times(runtime)
    missions = mission_assignments(db, org_id)
    rows = [drone_view(d, bindings.get(str(d.id)), kinematic, live.get(str(d.id)), missions.get(str(d.id)), now, recv.get(str(d.id))) for d in drones]
    summary = {
        "total": len(rows),
        "connected": sum(r["connection_state"] == "connected" for r in rows),
        "stale": sum(r["connection_state"] == "stale" for r in rows),
        "offline": sum(r["connection_state"] in ("offline", "no_telemetry") for r in rows),
        "available": sum(r["connection_state"] == "connected" and r["status"] in ("online", "active") and r["mission"] is None for r in rows),
        "on_mission": sum(r["mission"] is not None for r in rows),
        "failed": sum(r["status"] == "critical" or (r["binding"] or {}).get("state") == "connection_failed" for r in rows),
        "by_source": {k: sum(r["telemetry_source"] == k for r in rows) for k in ("sitl", "hardware", "kinematic_demo", "none")},
    }
    s = get_settings()
    return {"server_time": now.isoformat(), "stale_after_s": s.MC_TELEMETRY_STALE_S, "offline_after_s": s.MC_TELEMETRY_OFFLINE_S,
            "summary": summary, "drones": rows}
