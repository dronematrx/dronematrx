"""Sprint 6 mission-control API: fleet view, SITL bench, flight-controller
discovery/binding, pre-arm checklist, commands, video wall, detections.

RBAC (backend-enforced; see docs/sprint6/IMPLEMENTATION_PLAN.md §6):
view = any role; bind/bench/commands/recordings = ADMIN, OPERATOR;
detection review = ADMIN, OPERATOR, ANALYST; test fixtures = ADMIN (test mode).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import RequireRole, require_admin, require_any_authenticated, require_fleet_write
from app.core.config import get_settings
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.drone import Drone
from app.models.mission import Mission
from app.models.mission_planning import MissionPlan, MissionTask, TaskAssignment
from app.models.user import User
from app.services.mission_control import detections as detsvc
from app.services.mission_control import fc as fcsvc
from app.services.mission_control import fleet as fleetsvc
from app.services.mission_control import qr as qrsvc
from app.services.mission_control import video as videosvc
from app.services.mission_control.adapters import BenchSitlAdapter, KinematicDemoAdapter
from app.services.mission_control.commands import FLEET_COMMANDS, SINGLE_COMMANDS
from app.services.simulation.simhost import SimhostError

router = APIRouter(prefix="/mission-control", tags=["mission-control"])
video_router = APIRouter(prefix="/video", tags=["video"])
detections_router = APIRouter(prefix="/detections", tags=["detections"])

require_reviewer = RequireRole("ADMIN", "OPERATOR", "ANALYST")
CAPS = {"sitl": sorted(BenchSitlAdapter.capabilities), "kinematic_demo": sorted(KinematicDemoAdapter.capabilities), "hardware": [], "none": []}


def _mc(request: Request):
    mc = getattr(request.app.state, "mc", None)
    if mc is None:
        raise HTTPException(status_code=503, detail="Mission control is not running")
    return mc


def _runtime(request: Request) -> Any:
    return getattr(request.app.state, "runtime", None)


def _org_drone(db: Session, user: User, drone_id: uuid.UUID) -> Drone:
    drone = db.get(Drone, drone_id)
    if drone is None or drone.organization_id != user.organization_id or not drone.active:
        raise HTTPException(status_code=404, detail="Drone not found")
    return drone


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ----------------------------------------------------------------- fleet


@router.get("/fleet")
def mc_fleet(request: Request, db: Session = Depends(get_db), user: User = Depends(require_any_authenticated)) -> dict:
    out = fleetsvc.fleet(db, user.organization_id, _runtime(request))
    for d in out["drones"]:
        d["capabilities"] = CAPS[d["telemetry_source"]] if (d["binding"] or {}).get("state", "bound") == "bound" else []
    return out


@router.get("/overview")
def mc_overview(request: Request, db: Session = Depends(get_db), user: User = Depends(require_any_authenticated)) -> dict:
    mc = _mc(request)
    fleet = mc_fleet(request, db, user)
    try:
        benches = {sid: st for sid, st in mc.client.sessions().items() if sid.startswith(fcsvc.BENCH_PREFIX)}
        simhost = "ok"
    except SimhostError as exc:
        benches, simhost = {}, f"unavailable: {exc}"
    pending = detsvc.pending_count(db, user.organization_id)
    return {**fleet, "bench_sessions": benches, "simhost": simhost, "detections_pending": pending,
            "role": user.role, "capabilities_by_source": CAPS}


@router.get("/drones/{drone_id}")
def mc_drone(drone_id: uuid.UUID, request: Request, db: Session = Depends(get_db), user: User = Depends(require_any_authenticated)) -> dict:
    _org_drone(db, user, drone_id)
    for d in mc_fleet(request, db, user)["drones"]:
        if d["id"] == str(drone_id):
            return d
    raise HTTPException(status_code=404, detail="Drone not found")


# ----------------------------------------------------------------- bench


class BenchIn(BaseModel):
    count: int = Field(3, ge=1, le=8)
    cameras: int | None = Field(None, ge=0, le=8)  # drones with a camera (default: all); software rendering is costly


@router.post("/bench", status_code=status.HTTP_201_CREATED)
async def bench_start(payload: BenchIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_fleet_write)) -> dict:
    mc = _mc(request)
    try:
        return await asyncio.to_thread(fcsvc.start_bench, db, user, payload.count, mc.client, payload.cameras)
    except SimhostError as exc:
        raise HTTPException(status_code=502, detail=f"SITL bench failed to start: {exc}") from exc


@router.delete("/bench/{session_id}")
async def bench_stop(session_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_fleet_write)) -> dict:
    mc = _mc(request)
    try:
        return await asyncio.to_thread(fcsvc.stop_bench, db, user, session_id, mc.client)
    except SimhostError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --------------------------------------------------------- flight controllers


@router.post("/fc/discover")
async def fc_discover(request: Request, db: Session = Depends(get_db), user: User = Depends(require_fleet_write)) -> dict:
    mc = _mc(request)
    t0 = time.perf_counter()
    out = await asyncio.to_thread(fcsvc.discover, db, user.organization_id, mc.client)
    out["request_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    db.add(AuditLog(user_id=user.id, action="fc_discover", resource="flight_controller", ip_address=_ip(request),
                    event_metadata={"found": len(out["controllers"]), "errors": out["errors"]}))
    db.commit()
    return out


class BindIn(BaseModel):
    drone_id: uuid.UUID
    connection_type: Literal["sitl", "udp", "serial"]
    endpoint: str = Field(..., max_length=200)
    via: Literal["discovery", "manual"] = "discovery"
    system_id: int | None = Field(None, ge=1, le=255)


@router.post("/fc/bind", status_code=status.HTTP_201_CREATED)
async def fc_bind(payload: BindIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_fleet_write)) -> dict:
    mc = _mc(request)
    return await asyncio.to_thread(fcsvc.bind, db, user, payload.drone_id, payload.connection_type, payload.endpoint.strip(), payload.via,
                                   mc.client, payload.system_id)


class QrIn(BaseModel):
    qr_text: str = Field(..., max_length=1024)
    drone_id: uuid.UUID | None = None


@router.post("/fc/qr/validate")
async def fc_qr_validate(payload: QrIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_fleet_write)) -> dict:
    try:
        parsed = qrsvc.parse(payload.qr_text)
    except qrsvc.QrError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    found = await asyncio.to_thread(fcsvc.discover, db, user.organization_id, _mc(request).client)
    match = next((c for c in found["controllers"] if c["fc_identity"] == parsed["fc"]), None)
    return {"payload": parsed, "discovered": match, "bindable": match is not None and not match["bound"]}


@router.post("/fc/bind-qr", status_code=status.HTTP_201_CREATED)
async def fc_bind_qr(payload: QrIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_fleet_write)) -> dict:
    if payload.drone_id is None:
        raise HTTPException(status_code=422, detail="drone_id is required")
    try:
        parsed = qrsvc.parse(payload.qr_text)
    except qrsvc.QrError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    return await asyncio.to_thread(fcsvc.bind, db, user, payload.drone_id, parsed["conn"], parsed["fc"], "qr", _mc(request).client, parsed["sysid"])


@router.get("/fc/qr")
def fc_qr_payload(fc_identity: str, conn: Literal["sitl", "udp", "serial"], sysid: int | None = None, ref: str | None = None,
                  user: User = Depends(require_fleet_write)) -> dict:
    try:
        return {"qr_text": qrsvc.build(fc_identity, conn, sysid, ref)}
    except qrsvc.QrError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/fc/bindings")
def fc_bindings(history: bool = False, db: Session = Depends(get_db), user: User = Depends(require_any_authenticated)) -> list[dict]:
    return fcsvc.bindings(db, user.organization_id, history)


@router.post("/fc/bindings/{binding_id}/unbind")
async def fc_unbind(binding_id: uuid.UUID, request: Request, db: Session = Depends(get_db), user: User = Depends(require_fleet_write)) -> dict:
    return await asyncio.to_thread(fcsvc.unbind, db, user, binding_id, _mc(request).client)


@router.post("/fc/bindings/{binding_id}/reconnect")
async def fc_reconnect(binding_id: uuid.UUID, request: Request, db: Session = Depends(get_db), user: User = Depends(require_fleet_write)) -> dict:
    return await asyncio.to_thread(fcsvc.reconnect, db, user, binding_id, _mc(request).client)


# ------------------------------------------------------------- pre-arm / commands


@router.get("/drones/{drone_id}/prearm")
async def drone_prearm(drone_id: uuid.UUID, request: Request, expected_mode: Literal["sitl", "hardware"] | None = None,
                       db: Session = Depends(get_db), user: User = Depends(require_any_authenticated)) -> dict:
    drone = _org_drone(db, user, drone_id)
    return await asyncio.to_thread(_mc(request).commands.prearm, db, drone, expected_mode)


class CommandIn(BaseModel):
    command: Literal[SINGLE_COMMANDS]  # type: ignore[valid-type]
    params: dict = Field(default_factory=dict)
    confirm_mode: Literal["sitl", "hardware", "kinematic_demo"] | None = None
    idempotency_key: str | None = Field(None, min_length=8, max_length=80)


@router.post("/drones/{drone_id}/commands", status_code=status.HTTP_202_ACCEPTED)
async def drone_command(drone_id: uuid.UUID, payload: CommandIn, request: Request, user: User = Depends(require_fleet_write)) -> dict:
    t0 = time.perf_counter()
    return await asyncio.to_thread(_mc(request).commands.execute, user, drone_id, payload.command, payload.params, payload.confirm_mode,
                                   payload.idempotency_key, t0)


@router.get("/commands")
def list_commands(request: Request, drone_id: uuid.UUID | None = None, fleet_command_id: uuid.UUID | None = None,
                  limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), user: User = Depends(require_any_authenticated)) -> list[dict]:
    return _mc(request).commands.list(db, user.organization_id, drone_id, fleet_command_id, limit)


class FleetPreviewIn(BaseModel):
    fleet_command: Literal[tuple(FLEET_COMMANDS)]  # type: ignore[valid-type]


@router.post("/fleet-commands/preview")
async def fleet_preview(payload: FleetPreviewIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_fleet_write)) -> dict:
    return await asyncio.to_thread(_mc(request).commands.fleet_preview, db, user, payload.fleet_command)


class FleetCommandIn(BaseModel):
    fleet_command: Literal[tuple(FLEET_COMMANDS)]  # type: ignore[valid-type]
    expected_targets: list[str]
    confirm: bool
    confirm_text: str | None = None
    confirm_modes: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str = Field(..., min_length=8, max_length=60)


@router.post("/fleet-commands", status_code=status.HTTP_202_ACCEPTED)
async def fleet_command(payload: FleetCommandIn, request: Request, user: User = Depends(require_fleet_write)) -> dict:
    if not payload.confirm:
        raise HTTPException(status_code=409, detail="Fleet-wide commands require explicit confirmation")
    t0 = time.perf_counter()
    return await asyncio.to_thread(_mc(request).commands.fleet_execute, user, payload.fleet_command, payload.expected_targets,
                                   payload.confirm_text, payload.confirm_modes, payload.idempotency_key, t0)


class AssignTaskIn(BaseModel):
    mission_id: uuid.UUID
    task_key: str = Field(..., max_length=16)


@router.post("/drones/{drone_id}/assign-task")
def assign_task(drone_id: uuid.UUID, payload: AssignTaskIn, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_fleet_write)) -> dict:
    """Re-assign one task of the mission's current CBBA plan to this drone.
    It is a planning record: nothing is uploaded to the aircraft here."""
    drone = _org_drone(db, user, drone_id)
    mission = db.get(Mission, payload.mission_id)
    if mission is None or mission.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Mission not found")
    if mission.status not in ("planning", "validating", "armed", "active"):
        raise HTTPException(status_code=409, detail=f"Tasks of a {mission.status} mission cannot be re-assigned")
    row = db.query(TaskAssignment, MissionTask).join(MissionTask, TaskAssignment.task_id == MissionTask.id).join(
        MissionPlan, TaskAssignment.plan_id == MissionPlan.id).filter(
        MissionPlan.mission_id == mission.id, MissionPlan.is_current.is_(True), MissionTask.task_key == payload.task_key).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task {payload.task_key} is not in the mission's current plan")
    assignment, task = row
    if task.status == "completed":
        raise HTTPException(status_code=409, detail=f"Task {task.task_key} is already completed")
    previous = str(assignment.drone_id) if assignment.drone_id else None
    if previous == str(drone.id):
        return {"task_key": task.task_key, "drone_id": str(drone.id), "previous_drone_id": previous, "changed": False}
    assignment.drone_id = drone.id
    db.add(AuditLog(user_id=user.id, action="task_assigned", resource="mission", resource_id=str(mission.id), ip_address=_ip(request),
                    event_metadata={"task_key": task.task_key, "drone_id": str(drone.id), "previous_drone_id": previous, "role": user.role}))
    db.commit()
    return {"task_key": task.task_key, "drone_id": str(drone.id), "drone_name": drone.name, "previous_drone_id": previous, "changed": True,
            "mission_id": str(mission.id)}


@router.get("/audit")
def mc_audit(limit: int = Query(100, ge=1, le=500), action: str | None = None, db: Session = Depends(get_db),
             user: User = Depends(require_fleet_write)) -> list[dict]:
    """Mission-control audit trail (commands, bindings, reviews, recordings) for this organization."""
    prefixes = ("command_", "fc_", "sitl_bench_", "detection_", "video_recording_", "task_assigned")
    q = db.query(AuditLog, User.email, User.role).outerjoin(User, User.id == AuditLog.user_id).filter(
        (User.organization_id == user.organization_id) | (AuditLog.user_id.is_(None)))
    rows = [(a, e, r) for a, e, r in q.order_by(AuditLog.timestamp.desc()).limit(2000).all() if a.action.startswith(prefixes)]
    if action:
        rows = [x for x in rows if x[0].action == action]
    return [{"id": str(a.id), "action": a.action, "resource": a.resource, "resource_id": a.resource_id, "user": e, "role": r,
             "timestamp": a.timestamp.isoformat() if a.timestamp else None, "metadata": a.event_metadata} for a, e, r in rows[:limit]]


# ------------------------------------------------------------------- video


@video_router.get("/feeds")
def video_feeds(request: Request, db: Session = Depends(get_db), user: User = Depends(require_any_authenticated)) -> dict:
    sync = videosvc.sync_sources(db, user.organization_id, _mc(request).client)
    return {**videosvc.feeds(db, user.organization_id), "sync": sync}


class WhepIn(BaseModel):
    sdp: str = Field(..., max_length=20000)
    rendition: Literal["hi", "lo"] = "hi"


@video_router.post("/feeds/{feed_id}/whep")
async def video_whep(feed_id: uuid.UUID, payload: WhepIn, db: Session = Depends(get_db), user: User = Depends(require_any_authenticated)) -> dict:
    return await asyncio.to_thread(videosvc.whep_offer, db, user, feed_id, payload.rendition, payload.sdp)


@video_router.delete("/sessions/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def video_whep_close(token: str, user: User = Depends(require_any_authenticated)) -> Response:
    await asyncio.to_thread(videosvc.whep_close, user, token)
    return Response(status_code=204)


class RecordIn(BaseModel):
    record: bool


@video_router.post("/feeds/{feed_id}/recordings")
def video_record(feed_id: uuid.UUID, payload: RecordIn, db: Session = Depends(get_db), user: User = Depends(require_fleet_write)) -> dict:
    return videosvc.set_recording(db, user, feed_id, payload.record)


@video_router.get("/feeds/{feed_id}/recordings")
def video_recordings(feed_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_any_authenticated)) -> dict:
    return videosvc.recordings(db, user.organization_id, feed_id)


@video_router.get("/feeds/{feed_id}/playback")
def video_playback(feed_id: uuid.UUID, start: str, duration_s: float = Query(..., ge=1, le=600), db: Session = Depends(get_db),
                   user: User = Depends(require_any_authenticated)) -> Response:
    data = videosvc.playback(db, user.organization_id, feed_id, start, duration_s)
    return Response(content=data, media_type="video/mp4", headers={"Cache-Control": "no-store"})


@video_router.get("/clock-offset")
def video_clock_offset(request: Request, user: User = Depends(require_any_authenticated)) -> dict:
    return videosvc.clock_offset(_mc(request).client)


class TestSourcesIn(BaseModel):
    count: int = Field(..., ge=1, le=9)


@video_router.post("/test-sources")
def video_test_sources(payload: TestSourcesIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_fleet_write)) -> dict:
    mc = _mc(request)
    try:
        mc.client.start_test_sources(payload.count)
    except SimhostError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {**videosvc.feeds(db, user.organization_id), "sync": videosvc.sync_sources(db, user.organization_id, mc.client)}


@video_router.post("/test-sources/{number}/{action}")
def video_test_source_control(number: int, action: Literal["start", "stop"], request: Request, user: User = Depends(require_fleet_write)) -> dict:
    try:
        return _mc(request).client.control_test_source(number, action)
    except SimhostError as exc:
        raise HTTPException(status_code=exc.status or 502, detail=str(exc)) from exc


# -------------------------------------------------------------- detections


@detections_router.get("")
def detections_list(status_: Literal["pending", "accepted", "rejected"] | None = Query(None, alias="status"), mission_id: uuid.UUID | None = None,
                    limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), user: User = Depends(require_any_authenticated)) -> list[dict]:
    return detsvc.listing(db, user.organization_id, status_, mission_id, limit)


@detections_router.get("/accepted.geojson")
def detections_accepted(mission_id: uuid.UUID | None = None, db: Session = Depends(get_db), user: User = Depends(require_any_authenticated)) -> dict:
    return detsvc.accepted_geojson(db, user.organization_id, mission_id)


@detections_router.get("/heatmap")
def detections_heatmap(mission_id: uuid.UUID | None = None, cell_m: float = Query(20.0, ge=5, le=500), db: Session = Depends(get_db),
                       user: User = Depends(require_any_authenticated)) -> dict:
    return detsvc.heatmap(db, user.organization_id, mission_id, cell_m)


@detections_router.get("/stats")
def detections_stats(request: Request, user: User = Depends(require_any_authenticated)) -> dict:
    return _mc(request).detections.stats


@detections_router.get("/{detection_id}/thumbnail")
def detection_thumbnail(detection_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_any_authenticated)) -> Response:
    data, media = detsvc.thumbnail(db, user.organization_id, detection_id)
    return Response(content=data, media_type=media, headers={"Cache-Control": "private, max-age=3600"})


class ReviewIn(BaseModel):
    decision: Literal["accepted", "rejected"]
    reason: str | None = Field(None, max_length=500)


@detections_router.post("/{detection_id}/review")
def detection_review(detection_id: uuid.UUID, payload: ReviewIn, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_reviewer)) -> dict:
    data, changed = detsvc.review(db, user, detection_id, payload.decision, payload.reason)
    if changed:
        loop = getattr(_mc(request).commands, "loop", None)
        if loop is not None:
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_mc(request).connections.broadcast_json(
                {"type": "detection_review", "data": data}, organization_id=str(user.organization_id))))
    return {**data, "changed": changed}


class FixtureIn(BaseModel):
    drone_id: uuid.UUID
    count: int = Field(1, ge=1, le=20)
    location: Literal["valid", "missing", "invalid"] = "valid"
    anchor_lat: float | None = Field(None, ge=-90, le=90)
    anchor_lon: float | None = Field(None, ge=-180, le=180)


@detections_router.post("/test-fixtures", status_code=status.HTTP_202_ACCEPTED)
async def detection_fixtures(payload: FixtureIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> dict:
    """Deterministic TEST FIXTURE detections, published through the same NATS
    path as real inference events. Admin only; disabled unless DETECTION_TEST_FIXTURES."""
    if not get_settings().DETECTION_TEST_FIXTURES:
        raise HTTPException(status_code=403, detail="Detection test fixtures are disabled on this server")
    mc = _mc(request)
    drone = _org_drone(db, user, payload.drone_id)
    s = get_settings()
    anchor = (payload.anchor_lat if payload.anchor_lat is not None else s.MC_BENCH_LAT, payload.anchor_lon if payload.anchor_lon is not None else s.MC_BENCH_LON)
    events = detsvc.fixture_events(drone, payload.count, anchor, mc.detections.fixture_seq, payload.location)
    mc.detections.fixture_seq += payload.count
    runtime = _runtime(request)
    via = "nats"
    if runtime is not None and runtime.nats_ok:
        for ev in events:
            await runtime.bus.publish(drone.uin or str(drone.id), "detection", ev)
    else:
        via = "direct (NATS unavailable)"
        for ev in events:
            await mc.detections.handle(ev)
    db.add(AuditLog(user_id=user.id, action="detection_fixtures_emitted", resource="drone", resource_id=str(drone.id),
                    event_metadata={"count": payload.count, "location": payload.location, "via": via}))
    db.commit()
    return {"emitted": [{"event_id": e["event_id"], "inferred_at": e["inferred_at"], "label": e["label"]} for e in events], "via": via}

