"""Auto Mission API: AOI + mission type + number of drones -> everything else
derived (see app/services/auto_mission.py).

    GET  /auto-missions/availability          ready drones per mission type, limits
    POST /auto-missions                        plan (creates the mission, runs CBBA + validation)
    GET  /auto-missions/{id}                   plan summary + live per-drone status
    POST /auto-missions/{id}/launch            simulation: session/world/preflight/start;
                                               real aircraft: one approval -> permissions, uploads, staggered start
    POST /auto-missions/{id}/reassign          send a dropped drone's unfinished tasks to a ready drone

RBAC: viewing = any role; planning and launching = ADMIN / OPERATOR.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_any_authenticated, require_fleet_write
from app.db.session import get_db
from app.models.user import User
from app.services import auto_mission as svc
from app.services.mission_control import fleet as fleetsvc

router = APIRouter(prefix="/auto-missions", tags=["auto-mission"])


class AutoPlanIn(BaseModel):
    name: str | None = Field(None, max_length=200)
    template: Literal["survey", "search", "inspection", "mapping", "agriculture", "custom"]
    aoi: dict[str, Any]
    drone_count: int = Field(..., ge=1, le=16)
    mode: Literal["simulation", "hardware"]


class LaunchIn(BaseModel):
    acknowledgements: dict[str, bool] = Field(default_factory=dict)
    minutes: int = Field(20, ge=1, le=60)


class ReassignIn(BaseModel):
    from_drone_id: uuid.UUID
    acknowledgements: dict[str, bool]
    minutes: int = Field(20, ge=1, le=60)


def _live(request: Request) -> dict[str, Any]:
    return fleetsvc.latest_telemetry(getattr(request.app.state, "runtime", None))


def _mc(request: Request) -> Any:
    mc = getattr(request.app.state, "mc", None)
    if mc is None:
        raise HTTPException(status_code=503, detail="Mission control is not running")
    return mc


@router.get("/availability")
async def availability(request: Request, db: Session = Depends(get_db), user: User = Depends(require_any_authenticated)) -> dict:
    return await asyncio.to_thread(svc.counts, db, user, _live(request))


@router.post("", status_code=201)
async def plan(payload: AutoPlanIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_fleet_write)) -> dict:
    ip = request.client.host if request.client else None
    return await asyncio.to_thread(svc.plan, db, user, name=payload.name, template=payload.template, aoi=payload.aoi,
                                   drone_count=payload.drone_count, mode=payload.mode, live_by_id=_live(request), ip=ip)


@router.get("/{mission_id}")
async def get_status(mission_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_any_authenticated)) -> dict:
    return await asyncio.to_thread(svc.status, db, user, mission_id, _live(request))


@router.post("/{mission_id}/launch", status_code=202)
async def launch(mission_id: uuid.UUID, payload: LaunchIn, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_fleet_write)) -> dict:
    mission_mode = (await asyncio.to_thread(svc.status, db, user, mission_id, {})).get("mode")
    if mission_mode == "simulation":
        out = await asyncio.to_thread(svc.launch_simulation, db, user, mission_id)
        from app.services.simulation.orchestrator import SimulationOrchestrator

        if SimulationOrchestrator.instance is None:
            raise HTTPException(status_code=503, detail="Simulation orchestrator is not running")
        await SimulationOrchestrator.instance.launch(uuid.UUID(out["session_id"]), user.id)
        return out
    return await asyncio.to_thread(svc.launch_hardware, db, _mc(request), _live(request), user, mission_id, payload.acknowledgements,
                                   payload.minutes)


@router.post("/{mission_id}/reassign")
async def reassign(mission_id: uuid.UUID, payload: ReassignIn, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_fleet_write)) -> dict:
    return await asyncio.to_thread(svc.reassign, db, _mc(request), _live(request), user, mission_id, payload.from_drone_id,
                                   payload.acknowledgements, payload.minutes)
