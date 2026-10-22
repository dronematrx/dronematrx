"""Sprint 5 simulation API.

    GET  /simulations/fleet                       simulation drones + simulator capacity
    POST /simulations/missions/{mission_id}/fleet stage N simulation drones (wizard step 4)
    POST /simulations                             create a session for a planned simulation mission
    GET  /simulations?mission_id=                 list sessions
    GET  /simulations/{id}                        session, instances, tracks, live snapshot
    POST /simulations/{id}/world/generate         AOI -> Gazebo world
    GET  /simulations/{id}/world/status
    POST /simulations/{id}/preflight              launch checklist
    POST /simulations/{id}/start                  Gazebo + PX4 SITL, routes, mission ACTIVE
    POST /simulations/{id}/pause | resume | stop | abort
    POST /simulations/{id}/speed                  1x / 2x / 5x / 10x
    POST /simulations/{id}/failures               kill | link | battery | gps_drift | obstacle | wind
    GET  /simulations/{id}/events
    GET  /simulations/{id}/report
    GET  /simulations/{id}/report/candidates      previous reports to compare with
    POST /simulations/{id}/report/compare
    POST /simulations/{id}/rerun                  copy the mission for a comparable re-run

Writes need ADMIN/OPERATOR; reads any authenticated role.
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_mission_write
from app.db.session import get_db
from app.models import Drone, Mission, User
from app.models.simulation import TERMINAL_SESSION_STATES, SimulationEvent, SimulationReport, SimulationSession
from app.repositories.audit_repository import AuditRepository
from app.schemas.drone import DroneOut
from app.services.mission_planning_service import MissionPlanningService
from app.services.mission_service import MissionNotFoundError, MissionService
from app.services.simulation.orchestrator import SimulationError, SimulationOrchestrator, SimulationService, event_out
from app.services.simulation.reports import compare_reports

router = APIRouter(prefix="/simulations", tags=["simulation"])


# ------------------------------------------------------------------ inputs


class StageIn(BaseModel):
    drone_count: int = Field(..., ge=1, le=16)


class SessionCreate(BaseModel):
    mission_id: uuid.UUID
    speed: Literal[1.0, 2.0, 5.0, 10.0] = 1.0
    dem_source: Literal["terrarium", "flat"] = "terrarium"
    allow_fallback: bool = True
    auto_reallocate: bool = True
    seed: int = Field(42, ge=0, le=1_000_000)


class SpeedIn(BaseModel):
    speed: Literal[1.0, 2.0, 5.0, 10.0]


class FailureIn(BaseModel):
    type: Literal["kill", "link", "battery", "gps_drift", "obstacle", "wind"]
    drone_id: uuid.UUID | None = None
    params: dict = Field(default_factory=dict)


class CompareIn(BaseModel):
    baseline_session_id: uuid.UUID


# ------------------------------------------------------------------ helpers


def _mission(db: Session, mission_id: uuid.UUID, user: User) -> Mission:
    try:
        return MissionService(db).get_mission(mission_id, user.organization_id)
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mission not found")


def _session(db: Session, session_id: uuid.UUID, user: User) -> SimulationSession:
    sess = db.get(SimulationSession, session_id)
    if sess is None or sess.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation session not found")
    return sess


def _orch() -> SimulationOrchestrator:
    orch = SimulationOrchestrator.instance
    if orch is None:
        raise SimulationError("ORCHESTRATOR", "Simulation orchestrator not running", 503)
    return orch


def _audit(db: Session, user: User, action: str, sess: SimulationSession, request: Request, **meta) -> None:
    AuditRepository(db).record(user_id=user.id, action=action, resource="simulation", resource_id=str(sess.id),
                               ip_address=request.client.host if request.client else None, metadata=meta)


def session_out(db: Session, sess: SimulationSession, include_tracks: bool = True) -> dict:
    orch = SimulationOrchestrator.instance
    run = orch.runs.get(sess.id) if orch else None
    instances = []
    for inst in sess.instances:
        drone = db.get(Drone, inst.drone_id)
        live = None
        if run is not None and inst.uin in run.drones:
            dr = run.drones[inst.uin]
            live = {"state": "failed" if dr.failed else ("comm_lost" if dr.comm_lost else dr.state), "track": dr.track if include_tracks else None,
                    "planned_route": dr.planned_route, "remaining_tasks": [k for k, _ in dr.path], "stats": orch.drone_stats(dr)}
        instances.append({
            "id": str(inst.id), "drone_id": str(inst.drone_id), "name": drone.name, "uin": inst.uin, "instance_index": inst.instance_index,
            "state": live["state"] if live else inst.state, "gz_model_name": inst.gz_model_name, "mavlink_port": inst.mavlink_port,
            "sitl_pid": inst.sitl_pid, "spawn": inst.spawn, "bundle": inst.bundle,
            "planned_route": live["planned_route"] if live else (inst.route or {}).get("planned_route", []),
            "track": (live["track"] if live else inst.track) if include_tracks else None,
            "stats": live["stats"] if live else inst.stats, "failure_reason": inst.failure_reason,
        })
    return {
        "id": str(sess.id), "mission_id": str(sess.mission_id), "plan_id": str(sess.plan_id) if sess.plan_id else None,
        "state": run.state if run and run.state in ("running", "paused") else sess.state, "simulation": True,
        "task_type": sess.task_type, "requested_drones": sess.requested_drones, "spawned_drones": sess.spawned_drones,
        "speed": run.speed if run else sess.speed, "sim_time_s": run.sim_time_s if run else sess.sim_time_s,
        "config": {k: v for k, v in (sess.config or {}).items()}, "world": sess.world, "environment": sess.environment,
        "progress": orch.progress(run) if run else {k: v for k, v in (sess.progress or {}).items()},
        "preflight": (sess.progress or {}).get("preflight"), "guardrail": run.guardrail if run else (sess.progress or {}).get("guardrail"),
        "error": sess.error, "created_at": sess.created_at, "started_at": sess.started_at, "ended_at": sess.ended_at,
        "instances": instances, "live": orch.snapshot(run) if run else None,
        "has_report": db.execute(select(SimulationReport.id).where(SimulationReport.session_id == sess.id)).scalar_one_or_none() is not None,
    }


# --------------------------------------------------------------- endpoints


@router.get("/fleet")
def sim_fleet(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    from app.models import Organization

    out = SimulationService(db).fleet(db.get(Organization, user.organization_id))
    return {"drones": [DroneOut.from_model(d) for d in out["drones"]], "capacity": out["capacity"]}


@router.post("/missions/{mission_id}/fleet")
def stage_fleet(mission_id: uuid.UUID, body: StageIn, request: Request, user: User = Depends(require_mission_write), db: Session = Depends(get_db)) -> dict:
    mission = _mission(db, mission_id, user)
    out = SimulationService(db).stage_fleet(mission, user, body.drone_count, request.client.host if request.client else None)
    return {"wizard": out["wizard"], "drones": [DroneOut.from_model(d) for d in out["drones"]], "launch_positions": out["positions"],
            "min_separation_m": out["min_separation_m"]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_session(body: SessionCreate, request: Request, user: User = Depends(require_mission_write), db: Session = Depends(get_db)) -> dict:
    mission = _mission(db, body.mission_id, user)
    sess = SimulationService(db).create_session(mission, user, body.model_dump(mode="json"))
    _audit(db, user, "simulation_created", sess, request, mission_id=str(mission.id))
    return session_out(db, sess)


@router.get("")
def list_sessions(mission_id: uuid.UUID | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    q = select(SimulationSession).where(SimulationSession.organization_id == user.organization_id).order_by(SimulationSession.created_at.desc())
    if mission_id:
        q = q.where(SimulationSession.mission_id == mission_id)
    return [session_out(db, s, include_tracks=False) for s in db.execute(q.limit(50)).scalars()]


@router.get("/{session_id}")
def get_session(session_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return session_out(db, _session(db, session_id, user))


@router.post("/{session_id}/world/generate")
def generate_world(session_id: uuid.UUID, user: User = Depends(require_mission_write), db: Session = Depends(get_db)) -> dict:
    sess = _session(db, session_id, user)
    world = SimulationService(db).generate_world(sess)
    return {"state": sess.state, "world": world}


@router.get("/{session_id}/world/status")
def world_status(session_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    sess = _session(db, session_id, user)
    return {"state": sess.state, "world": sess.world, "error": sess.error if sess.state == "failed" else None}


@router.post("/{session_id}/preflight")
def preflight(session_id: uuid.UUID, user: User = Depends(require_mission_write), db: Session = Depends(get_db)) -> dict:
    return SimulationService(db).preflight(_session(db, session_id, user))


@router.post("/{session_id}/start", status_code=status.HTTP_202_ACCEPTED)
async def start(session_id: uuid.UUID, request: Request, user: User = Depends(require_mission_write), db: Session = Depends(get_db)) -> dict:
    sess = _session(db, session_id, user)
    await _orch().launch(sess.id, user.id)
    _audit(db, user, "simulation_started", sess, request)
    return {"state": "starting"}


@router.post("/{session_id}/pause")
async def pause(session_id: uuid.UUID, user: User = Depends(require_mission_write), db: Session = Depends(get_db)) -> dict:
    return await _orch().pause(_session(db, session_id, user).id, True)


@router.post("/{session_id}/resume")
async def resume(session_id: uuid.UUID, user: User = Depends(require_mission_write), db: Session = Depends(get_db)) -> dict:
    return await _orch().pause(_session(db, session_id, user).id, False)


@router.post("/{session_id}/speed")
async def speed(session_id: uuid.UUID, body: SpeedIn, user: User = Depends(require_mission_write), db: Session = Depends(get_db)) -> dict:
    return await _orch().set_speed(_session(db, session_id, user).id, float(body.speed))


@router.post("/{session_id}/failures")
async def failures(session_id: uuid.UUID, body: FailureIn, request: Request, user: User = Depends(require_mission_write), db: Session = Depends(get_db)) -> dict:
    sess = _session(db, session_id, user)
    res = await _orch().inject_failure(sess.id, body.type, str(body.drone_id) if body.drone_id else None, body.params)
    _audit(db, user, "simulation_failure_injected", sess, request, type=body.type, drone_id=str(body.drone_id) if body.drone_id else None)
    return res


@router.post("/{session_id}/stop")
async def stop(session_id: uuid.UUID, request: Request, user: User = Depends(require_mission_write), db: Session = Depends(get_db)) -> dict:
    sess = _session(db, session_id, user)
    if sess.state in TERMINAL_SESSION_STATES:
        raise SimulationError("STATE", f"Session already {sess.state}", 409)
    _audit(db, user, "simulation_stopped", sess, request)
    return await _orch().finish(sess.id, "stopped", "Stopped by operator", user.id)


@router.post("/{session_id}/abort")
async def abort(session_id: uuid.UUID, request: Request, user: User = Depends(require_mission_write), db: Session = Depends(get_db)) -> dict:
    sess = _session(db, session_id, user)
    if sess.state in TERMINAL_SESSION_STATES:
        raise SimulationError("STATE", f"Session already {sess.state}", 409)
    _audit(db, user, "simulation_aborted", sess, request)
    return await _orch().finish(sess.id, "aborted", "Aborted by operator", user.id)


@router.get("/{session_id}/events")
def events(session_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    sess = _session(db, session_id, user)
    rows = db.execute(select(SimulationEvent).where(SimulationEvent.session_id == sess.id).order_by(SimulationEvent.created_at)).scalars()
    return [event_out(e) for e in rows]


def _report(db: Session, sess: SimulationSession) -> SimulationReport:
    report = db.execute(select(SimulationReport).where(SimulationReport.session_id == sess.id)).scalar_one_or_none()
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No report yet: reports are generated when the simulation ends")
    return report


@router.get("/{session_id}/report")
def report(session_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    r = _report(db, _session(db, session_id, user))
    return {"id": str(r.id), "session_id": str(r.session_id), "generation_ms": r.generation_ms, "created_at": r.created_at, "data": r.data}


@router.get("/{session_id}/report/candidates")
def report_candidates(session_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    sess = _session(db, session_id, user)
    rows = db.execute(select(SimulationReport).where(SimulationReport.organization_id == user.organization_id, SimulationReport.session_id != sess.id)
                      .order_by(SimulationReport.created_at.desc()).limit(30)).scalars()
    return [{"session_id": str(r.session_id), "mission_name": r.data["mission_summary"]["mission_name"], "task_type": r.data["mission_summary"]["task_type"],
             "drones": r.data["mission_summary"]["drones_spawned"], "status": r.data["mission_summary"]["status"], "created_at": r.created_at} for r in rows]


@router.post("/{session_id}/report/compare")
def compare(session_id: uuid.UUID, body: CompareIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    current = _report(db, _session(db, session_id, user))
    baseline = _report(db, _session(db, body.baseline_session_id, user))
    comp = compare_reports(db, current, baseline)
    return {"id": str(comp.id), "compatible": comp.compatible, **comp.data}


@router.post("/{session_id}/rerun", status_code=status.HTTP_201_CREATED)
def rerun(session_id: uuid.UUID, request: Request, user: User = Depends(require_mission_write), db: Session = Depends(get_db)) -> dict:
    """New simulation mission with the same AOI, task type, fleet size and
    parameters (re-planned with CBBA), so its report is comparable."""
    sess = _session(db, session_id, user)
    src = db.get(Mission, sess.mission_id)
    from app.schemas.mission import MissionCreate

    mission = MissionService(db).create_mission(user.organization_id, user.id, MissionCreate(name=f"{src.name} (re-run)", mission_type=src.mission_type,
                                                                                           execution_mode="simulation"))
    planning = MissionPlanningService(db)
    ip = request.client.host if request.client else None
    cfg = src.config or {}
    planning.save_step(mission, 1, cfg["template"], user, ip)
    planning.save_step(mission, 2, {"source": "copied", "copy_from_mission_id": str(src.id)}, user, ip)
    planning.save_step(mission, 3, cfg["flight"], user, ip)
    SimulationService(db).stage_fleet(mission, user, sess.requested_drones, ip)
    planning.save_step(mission, 5, cfg["constraints"], user, ip)
    planning.plan(mission, user)
    return {"mission_id": str(mission.id)}
