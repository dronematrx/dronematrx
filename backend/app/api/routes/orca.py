"""ORCA collision-avoidance API.

POST /orca/step      one ORCA cycle for the drones you send (stateless)
POST /orca/simulate  closed-loop simulation of a named or custom scenario
GET  /orca/state     live 10 Hz runtime snapshot (drones, predictions, RTH queue, timing)
GET  /orca/events    live ORCA event timeline
POST /orca/demo/start | /orca/demo/stop, GET /orca/demo   deterministic 8-drone demo
POST /orca/command   HOLD / RTH / RESUME over the NATS command topic
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, model_validator

from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin, require_mission_write
from app.db.session import get_db
from app.messaging.bus import NatsUnavailableError
from app.models.user import User
from app.services.orca.engine import OrcaEngine
from app.services.orca.models import AgentState, InvalidAgentStateError, OrcaParams
from app.services.orca.scenarios import FAMILIES, eight_drone_run
from app.services.orca.simulation import OrcaSimulation, SimAgent

router = APIRouter(prefix="/orca", tags=["orca"])

Vec = tuple[float, float]


class ParamsIn(BaseModel):
    agent_radius_m: float = Field(1.0, gt=0, le=20)
    safety_margin_m: float = Field(2.0, ge=0, le=50)
    time_horizon_s: float = Field(5.0, gt=0, le=60)
    timestep_s: float = Field(0.1, gt=0, le=1)
    max_speed_mps: float = Field(15.0, gt=0, le=60)
    neighbor_radius_m: float = Field(150.0, gt=0, le=5000)
    altitude_separation_m: float = Field(8.0, gt=0, le=200)
    prediction_horizon_s: float = Field(10.0, gt=0, le=120)

    def build(self) -> OrcaParams:
        return OrcaParams(**self.model_dump())


class DroneIn(BaseModel):
    drone_id: str = Field(..., min_length=1, max_length=64)
    x: float
    y: float
    altitude: float = 50.0
    vx: float = 0.0
    vy: float = 0.0
    preferred_vx: float = 0.0
    preferred_vy: float = 0.0
    heading: float = 0.0
    radius: float | None = Field(None, gt=0, le=20)
    max_speed: float | None = Field(None, gt=0, le=60)
    priority: int = Field(0, ge=0, le=10)
    status: Literal["active", "hold", "rth", "landed", "offline"] = "active"
    comm_degraded: bool = False

    @model_validator(mode="after")
    def _finite(self) -> "DroneIn":
        if not all(math.isfinite(v) for v in (self.x, self.y, self.altitude, self.vx, self.vy, self.preferred_vx, self.preferred_vy)):
            raise ValueError("coordinates and velocities must be finite")
        return self

    def build(self) -> AgentState:
        return AgentState(
            id=self.drone_id, position=(self.x, self.y), velocity=(self.vx, self.vy),
            preferred_velocity=(self.preferred_vx, self.preferred_vy), altitude=self.altitude, radius=self.radius,
            max_speed=self.max_speed, heading=self.heading, status=self.status, priority=self.priority, comm_degraded=self.comm_degraded,
        )


class StepRequest(BaseModel):
    drones: list[DroneIn] = Field(..., min_length=1, max_length=64)
    params: ParamsIn | None = None


class SimDroneIn(BaseModel):
    drone_id: str = Field(..., min_length=1, max_length=64)
    x: float
    y: float
    goal_x: float
    goal_y: float
    cruise_speed: float = Field(10.0, gt=0, le=40)
    altitude: float = 50.0
    radius: float | None = Field(None, gt=0, le=20)
    priority: int = Field(0, ge=0, le=10)


class SimulateRequest(BaseModel):
    scenario: str | None = Field(None, description=f"Named family {FAMILIES} or 'run:<n>' for the n-th acceptance run")
    drones: list[SimDroneIn] | None = Field(None, max_length=64)
    max_time_s: float = Field(90.0, gt=0, le=600)
    params: ParamsIn | None = None
    record_every: int = Field(5, ge=0, le=100)

    @model_validator(mode="after")
    def _one_source(self) -> "SimulateRequest":
        if (self.scenario is None) == (self.drones is None):
            raise ValueError("Provide exactly one of 'scenario' or 'drones'")
        return self


def _vec(v: Vec) -> dict[str, float]:
    return {"x": round(v[0], 4), "y": round(v[1], 4)}


@router.post("/step")
def orca_step(payload: StepRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    try:
        engine = OrcaEngine(payload.params.build() if payload.params else OrcaParams())
        result = engine.step([d.build() for d in payload.drones])
    except InvalidAgentStateError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    predictions = {}
    for p in result.predictions:
        predictions.setdefault(p.a, []).append({"with": p.b, "time_to_collision_s": round(p.time_to_collision_s, 3), "point": _vec(p.point)})
        predictions.setdefault(p.b, []).append({"with": p.a, "time_to_collision_s": round(p.time_to_collision_s, 3), "point": _vec(p.point)})
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "drones": [
            {
                "drone_id": d.id,
                "preferred_velocity": _vec(d.preferred_velocity),
                "safe_velocity": _vec(d.safe_velocity),
                "avoidance_vector": _vec(d.avoidance_vector),
                "neighbors": d.neighbors,
                "collision_predicted": d.collision_predicted,
                "predictions": predictions.get(d.id, []),
                "constraints": [line.as_dict() for line in d.constraints],
                "infeasible": d.infeasible,
            }
            for d in result.decisions.values()
        ],
        "events": [e.as_dict() for e in result.events],
        "computation_time_ms": round(result.compute_ms, 3),
    }


@router.post("/simulate")
def orca_simulate(payload: SimulateRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    params = payload.params.build() if payload.params else OrcaParams()
    if payload.scenario is not None:
        if payload.scenario.startswith("run:"):
            try:
                index = int(payload.scenario.split(":", 1)[1])
            except ValueError:
                raise HTTPException(status_code=422, detail="run:<n> needs an integer") from None
            family, agents = eight_drone_run(index)
        elif payload.scenario in FAMILIES:
            family, agents = payload.scenario, eight_drone_run(FAMILIES.index(payload.scenario))[1]
        else:
            raise HTTPException(status_code=422, detail=f"Unknown scenario; use one of {FAMILIES} or run:<n>")
    else:
        family = "custom"
        agents = [SimAgent(d.drone_id, (d.x, d.y), (d.goal_x, d.goal_y), d.cruise_speed, d.altitude, d.radius, priority=d.priority) for d in payload.drones or []]
    started = time.perf_counter()
    try:
        sim = OrcaSimulation(agents, params, record_every=payload.record_every)
        report = sim.run(payload.max_time_s)
    except InvalidAgentStateError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "scenario": family,
        "summary": report.summary(),
        "wall_time_ms": round((time.perf_counter() - started) * 1000, 2),
        "events": [e.as_dict() for e in sim.engine.events],
        "trajectories": report.trajectories,
        "final_positions": {a.id: _vec(a.position) for a in sim.agents},
    }


def _runtime(request: Request):
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None or not runtime.running:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Live fleet runtime is not running")
    return runtime


@router.get("/state")
def orca_state(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    from app.api.routes.ops import org_drone_ids

    view = _runtime(request).orca_view()
    mine = org_drone_ids(db, current_user)
    return {
        **view,
        "drones": [d for d in view["drones"] if d["drone_id"] in mine],
        "predictions": [p for p in view["predictions"] if p["a_id"] in mine and p["b_id"] in mine],
    }


@router.get("/events")
def orca_events(request: Request, since: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000),
                db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[dict]:
    from app.api.routes.ops import org_drone_ids

    runtime = _runtime(request)
    mine = org_drone_ids(db, current_user)
    return [runtime.orca.event_dict(e) for e in runtime.orca.engine.recent_events(since, limit) if set(e.drones) <= mine]


@router.get("/demo")
def demo_status(request: Request, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return _runtime(request).demo.status.as_dict()


@router.post("/demo/start")
async def demo_start(request: Request, current_user: User = Depends(require_mission_write)) -> dict[str, Any]:
    try:
        return await _runtime(request).demo.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post("/demo/stop")
async def demo_stop(request: Request, current_user: User = Depends(require_mission_write)) -> dict[str, Any]:
    return await _runtime(request).demo.stop()


@router.post("/fleet/reset")
async def fleet_reset(request: Request, current_user: User = Depends(require_admin)) -> dict[str, Any]:
    """ADMIN: restore the simulated fleet to its seeded state (positions,
    batteries, statuses), clear RTH queue / bands / link degradations."""
    return await _runtime(request).reset_fleet(current_user.organization_id)


class CommandIn(BaseModel):
    drone_id: str
    command: Literal["HOLD", "RTH", "RESUME"]


@router.post("/command", status_code=status.HTTP_202_ACCEPTED)
async def orca_command(payload: CommandIn, request: Request, current_user: User = Depends(require_mission_write)) -> dict[str, Any]:
    runtime = _runtime(request)
    try:
        return await runtime.send_command(payload.drone_id, payload.command, current_user.email)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NatsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
