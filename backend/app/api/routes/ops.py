"""Sprint 4 operations API: communication links, telemetry history
(TimescaleDB), and the NATS message bus."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_mission_write
from app.db.session import get_db
from app.messaging.bus import NatsUnavailableError
from app.models.drone import Drone
from app.models.user import User
from app.repositories.audit_repository import AuditRepository
from app.services.comms.links import LinkType
from app.services.telemetry.store import TelemetryStore

comms_router = APIRouter(prefix="/comms", tags=["communications"])
telemetry_router = APIRouter(prefix="/telemetry", tags=["telemetry"])
messaging_router = APIRouter(prefix="/messaging", tags=["messaging"])


def _runtime(request: Request):
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None or not runtime.running:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Live fleet runtime is not running")
    return runtime


def _org_drone(db: Session, user: User, drone_id: uuid.UUID) -> Drone:
    drone = db.get(Drone, drone_id)
    if drone is None or drone.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Drone not found")
    return drone


# ------------------------------------------------------------------- comms


def org_drone_ids(db: Session, user: User) -> set[str]:
    return {str(i) for (i,) in db.query(Drone.id).filter(Drone.organization_id == user.organization_id).all()}


def org_uins(db: Session, user: User) -> set[str]:
    """NATS subject tokens of the org's drones (the runtime falls back to the id when a drone has no UIN)."""
    rows = db.query(Drone.id, Drone.uin).filter(Drone.organization_id == user.organization_id).all()
    return {uin or str(drone_id) for drone_id, uin in rows}


@comms_router.get("/links")
def comms_links(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    snapshot = _runtime(request).comms.snapshot()
    mine = org_drone_ids(db, current_user)
    return {**snapshot, "drones": [d for d in snapshot["drones"] if d["drone_id"] in mine]}


@comms_router.get("/events")
def comms_events(request: Request, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    comms = _runtime(request).comms
    mine = org_drone_ids(db, current_user)
    events = [e for e in comms.events if e["drone_id"] in mine]
    switches = [s for s in comms.switches if s["drone_id"] in mine]
    return {"events": events[-limit:], "switches": switches[-limit:]}


class DegradeIn(BaseModel):
    link: Literal["wifi", "lte", "lora"]
    rssi_dbm: float | None = Field(None, ge=-150, le=0)
    packet_loss_pct: float | None = Field(None, ge=0, le=100)
    latency_ms: float | None = Field(None, ge=0, le=60000)
    available: bool | None = None


@comms_router.post("/{drone_id}/degrade")
def comms_degrade(drone_id: uuid.UUID, payload: DegradeIn, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(require_mission_write)) -> dict[str, Any]:
    _org_drone(db, current_user, drone_id)
    runtime = _runtime(request)
    if str(drone_id) not in runtime.comms.drones:
        raise HTTPException(status_code=409, detail="Drone is not connected to the communication manager")
    runtime.comms.environment.degrade(str(drone_id), LinkType(payload.link), time.time(), payload.rssi_dbm, payload.packet_loss_pct, payload.latency_ms, payload.available)
    AuditRepository(db).record(current_user.id, "link_degraded", "drone", str(drone_id), request.client.host if request.client else None, payload.model_dump())
    return runtime.comms.drones[str(drone_id)].as_dict()


@comms_router.post("/{drone_id}/restore")
def comms_restore(drone_id: uuid.UUID, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_mission_write)) -> dict[str, Any]:
    _org_drone(db, current_user, drone_id)
    runtime = _runtime(request)
    runtime.comms.environment.restore(str(drone_id), None, now=time.time())
    AuditRepository(db).record(current_user.id, "link_restored", "drone", str(drone_id), request.client.host if request.client else None)
    return {"restored": True}


# --------------------------------------------------------------- telemetry


def _window(start: datetime | None, end: datetime | None, minutes: int) -> tuple[datetime, datetime]:
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(minutes=minutes)
    if start >= end:
        raise HTTPException(status_code=422, detail="start must be before end")
    if end - start > timedelta(hours=24):
        raise HTTPException(status_code=422, detail="window is limited to 24 hours")
    return start, end


@telemetry_router.get("/drones/{drone_id}")
def telemetry_drone(drone_id: uuid.UUID, start: datetime | None = None, end: datetime | None = None, minutes: int = Query(60, ge=1, le=1440),
                    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    _org_drone(db, current_user, drone_id)
    start, end = _window(start, end, minutes)
    rows, ms = TelemetryStore(lambda: db).drone_window(db, drone_id, start, end)
    return {"drone_id": str(drone_id), "start": start, "end": end, "rows": len(rows), "query_ms": round(ms, 2), "records": rows}


@telemetry_router.get("/fleet")
def telemetry_fleet(start: datetime | None = None, end: datetime | None = None, minutes: int = Query(60, ge=1, le=1440),
                    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    start, end = _window(start, end, minutes)
    ids = [d.id for d in db.query(Drone).filter(Drone.organization_id == current_user.organization_id).all()]
    rows, ms = TelemetryStore(lambda: db).fleet_window(db, start, end, ids)
    return {"start": start, "end": end, "rows": len(rows), "query_ms": round(ms, 2), "columns": list(TelemetryStore.FLEET_COLUMNS), "data": rows}


@telemetry_router.get("/stats")
def telemetry_stats(request: Request, minutes: int = Query(60, ge=1, le=1440), db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    store = TelemetryStore(lambda: db)
    started = time.perf_counter()
    counts = store.counts(db, since, current_user.organization_id)
    runtime = getattr(request.app.state, "runtime", None)
    return {
        "since": since,
        "per_drone": counts,
        "total_rows": sum(c["rows"] for c in counts),
        "query_ms": round((time.perf_counter() - started) * 1000, 2),
        "ingestor": runtime.ingestor.snapshot() if runtime and runtime.nats_ok else None,
    }


# --------------------------------------------------------------- messaging


@messaging_router.get("/status")
async def messaging_status(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    runtime = _runtime(request)
    view = runtime.messaging_view()
    mine = org_uins(db, current_user)
    view["recent"] = [m for m in view["recent"] if m["uin"] in mine]
    view["commands"] = [c for c in view["commands"] if c.get("uin") in mine]
    return {"bus": await runtime.bus.status(), **view}


class ReplayIn(BaseModel):
    kind: Literal["telemetry", "state", "alert", "command"] | None = "telemetry"
    uin: str | None = None
    minutes: float | None = Field(None, gt=0, le=24 * 60)
    start_seq: int | None = Field(None, ge=1)
    limit: int = Field(200, ge=1, le=2000)


@messaging_router.post("/replay")
async def messaging_replay(payload: ReplayIn, request: Request, db: Session = Depends(get_db),
                           current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    runtime = _runtime(request)
    mine = org_uins(db, current_user)
    if payload.uin is not None and payload.uin not in mine:
        raise HTTPException(status_code=404, detail="Drone not found")
    start_time = datetime.now(timezone.utc) - timedelta(minutes=payload.minutes) if payload.minutes else None
    started = time.perf_counter()
    try:
        messages = await runtime.bus.replay(kind=payload.kind, uin=payload.uin, start_time=start_time, start_seq=payload.start_seq, limit=payload.limit)
    except NatsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # The stream is shared by every organization: only return this org's drones.
    messages = [m for m in messages if m.uin in mine]
    return {
        "count": len(messages),
        "replay_ms": round((time.perf_counter() - started) * 1000, 2),
        "first_seq": messages[0].stream_seq if messages else None,
        "last_seq": messages[-1].stream_seq if messages else None,
        "messages": [{k: v for k, v in m.as_dict().items() if k != "latency_ms"} for m in messages],
    }
