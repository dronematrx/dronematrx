"""Sprint 3 mission planning API: wizard, CBBA allocation/reallocation,
validation, and the mission state machine.

Error mapping (handlers registered in app.main):
    PlanningError           -> 422 {"detail", "code"}
    MissionLockedError      -> 409
    InvalidTransitionError  -> 409 {"detail", "current", "target"}
    TransitionBlockedError  -> 422 {"detail", "checks", "report"}
"""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_mission_write
from app.db.session import get_db
from app.models.mission import Mission
from app.models.mission_planning import MissionStateHistory
from app.models.user import User
from app.repositories.audit_repository import AuditRepository
from app.schemas.mission_planning import (
    AllocateRequest,
    MissionDroneAdd,
    PlanOut,
    ReallocateRequest,
    StateHistoryOut,
    TemplateOut,
    TransitionRequest,
    WizardState,
    WizardStepSave,
)
from app.services import mission_events
from app.services.mission_planning_service import MissionPlanningService
from app.services.mission_service import MissionNotFoundError, MissionService
from app.services.mission_state_service import MissionStateService
from app.services.mission_templates import TEMPLATES

router = APIRouter(prefix="/missions", tags=["mission planning"])


def _mission(db: Session, mission_id: uuid.UUID, user: User) -> Mission:
    try:
        return MissionService(db).get_mission(mission_id, user.organization_id)
    except MissionNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _audit(db: Session, user: User, action: str, mission: Mission, request: Request, **metadata) -> None:
    AuditRepository(db).record(
        user_id=user.id, action=action, resource="mission", resource_id=str(mission.id), ip_address=_ip(request), metadata=metadata
    )


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(current_user: User = Depends(get_current_user)) -> list[TemplateOut]:
    return list(TEMPLATES.values())


# ------------------------------------------------------------------ wizard


@router.get("/{mission_id}/wizard", response_model=WizardState)
def get_wizard(mission_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> WizardState:
    return MissionPlanningService(db).wizard_state(_mission(db, mission_id, current_user))


@router.put("/{mission_id}/wizard/steps/{step}", response_model=WizardState)
def save_wizard_step(
    mission_id: uuid.UUID,
    payload: WizardStepSave,
    request: Request,
    background_tasks: BackgroundTasks,
    step: int = Path(..., ge=1, le=5),
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> WizardState:
    mission = _mission(db, mission_id, current_user)
    try:
        state = MissionPlanningService(db).save_step(mission, step, payload.data, current_user, _ip(request))
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()],
        )
    _audit(db, current_user, "mission_wizard_saved", mission, request, step=step)
    mission_events.publish(background_tasks, mission.organization_id, "mission.wizard_saved", mission.id, {"step": step, "status": state.status})
    if state.replanned:
        mission_events.publish(background_tasks, mission.organization_id, "mission.plan_updated", mission.id, {"plan_id": str(state.plan_id), "trigger": "parameters_changed"})
    return state


# -------------------------------------------------------------------- CBBA


@router.post("/{mission_id}/cbba/allocate", response_model=PlanOut)
@router.post("/{mission_id}/plan", response_model=PlanOut, include_in_schema=False)
def allocate(
    mission_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: AllocateRequest | None = None,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> PlanOut:
    mission = _mission(db, mission_id, current_user)
    service = MissionPlanningService(db)
    plan = service.plan(mission, current_user, algorithm=payload.algorithm if payload else None, ip_address=_ip(request))
    _audit(db, current_user, "mission_cbba_allocated", mission, request, plan_id=str(plan.id), converged=plan.converged)
    mission_events.publish(background_tasks, mission.organization_id, "mission.plan_updated", mission.id, {"plan_id": str(plan.id), "trigger": "allocate", "status": mission.status})
    return service.plan_out(mission, plan)


@router.get("/{mission_id}/cbba", response_model=PlanOut)
def get_plan(mission_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> PlanOut:
    mission = _mission(db, mission_id, current_user)
    service = MissionPlanningService(db)
    plan = service.current_plan(mission)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission has not been allocated yet")
    return service.plan_out(mission, plan)


def _reallocate(db, mission, user, request, background_tasks, add, remove) -> PlanOut:
    service = MissionPlanningService(db)
    plan = service.reallocate(mission, user, add, remove, _ip(request))
    _audit(
        db, user, "mission_cbba_reallocated", mission, request,
        plan_id=str(plan.id), added=[str(d) for d in add], removed=[str(d) for d in remove],
    )
    data = {"plan_id": str(plan.id), "added": [str(d) for d in add], "removed": [str(d) for d in remove], "status": mission.status}
    mission_events.publish(background_tasks, mission.organization_id, "mission.drones_changed", mission.id, data)
    mission_events.publish(background_tasks, mission.organization_id, "mission.plan_updated", mission.id, {**data, "trigger": "reallocate"})
    return service.plan_out(mission, plan)


@router.post("/{mission_id}/cbba/reallocate", response_model=PlanOut)
def reallocate(
    mission_id: uuid.UUID,
    payload: ReallocateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> PlanOut:
    mission = _mission(db, mission_id, current_user)
    return _reallocate(db, mission, current_user, request, background_tasks, payload.add_drone_ids, payload.remove_drone_ids)


@router.post("/{mission_id}/drones", response_model=PlanOut)
def add_mission_drone(
    mission_id: uuid.UUID,
    payload: MissionDroneAdd,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> PlanOut:
    mission = _mission(db, mission_id, current_user)
    return _reallocate(db, mission, current_user, request, background_tasks, [payload.drone_id], [])


@router.delete("/{mission_id}/drones/{drone_id}", response_model=PlanOut)
def remove_mission_drone(
    mission_id: uuid.UUID,
    drone_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> PlanOut:
    mission = _mission(db, mission_id, current_user)
    return _reallocate(db, mission, current_user, request, background_tasks, [], [drone_id])


# ------------------------------------------------------ validation / state


@router.post("/{mission_id}/validate")
def validate_mission(
    mission_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> dict:
    mission = _mission(db, mission_id, current_user)
    if mission.status not in ("planning", "validating"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cannot validate a mission in {mission.status.upper()} state")
    report = MissionPlanningService(db).validate(mission, current_user, transition=True, ip_address=_ip(request))
    _audit(db, current_user, "mission_validated", mission, request, passed=report["passed"], nfz_conflict=report["nfz_conflict"])
    mission_events.publish(background_tasks, mission.organization_id, "mission.validated", mission.id, {"passed": report["passed"], "status": mission.status})
    return {**report, "status": mission.status}


@router.get("/{mission_id}/readiness")
def mission_readiness(mission_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    mission = _mission(db, mission_id, current_user)
    checks = MissionPlanningService(db).readiness_checks(mission)
    return {"ready": not any(c["severity"] == "error" for c in checks), "checks": checks}


@router.post("/{mission_id}/transition")
def transition_mission(
    mission_id: uuid.UUID,
    payload: TransitionRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> dict:
    mission = _mission(db, mission_id, current_user)
    previous = mission.status
    MissionStateService(db).transition(mission, payload.target_state, current_user, payload.reason, _ip(request))
    mission_events.publish(
        background_tasks, mission.organization_id, "mission.state_changed", mission.id, {"previous": previous, "status": mission.status}
    )
    from app.schemas.mission import MissionOut

    return MissionOut.from_model(mission, MissionService(db).area_sq_meters(mission)).model_dump(mode="json")


@router.get("/{mission_id}/state-history", response_model=list[StateHistoryOut])
def state_history(mission_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[StateHistoryOut]:
    mission = _mission(db, mission_id, current_user)
    rows = db.execute(
        select(MissionStateHistory).where(MissionStateHistory.mission_id == mission.id).order_by(MissionStateHistory.created_at, MissionStateHistory.id)
    ).scalars().all()
    return [StateHistoryOut.model_validate(r) for r in rows]
