import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_mission_write
from app.db.session import get_db
from app.models.user import User
from app.repositories.audit_repository import AuditRepository
from app.schemas.mission import AOIOut, AOIUpsert, MissionCreate, MissionOut, MissionUpdate
from app.services.mission_service import MissionNotFoundError, MissionService

router = APIRouter(prefix="/missions", tags=["missions"])


@router.get("", response_model=list[MissionOut])
def list_missions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MissionOut]:
    service = MissionService(db)
    missions = service.list_missions(current_user.organization_id)
    return [MissionOut.from_model(m, service.area_sq_meters(m)) for m in missions]


@router.get("/{mission_id}", response_model=MissionOut)
def get_mission(
    mission_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MissionOut:
    service = MissionService(db)
    try:
        mission = service.get_mission(mission_id, current_user.organization_id)
    except MissionNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
    return MissionOut.from_model(mission, service.area_sq_meters(mission))


@router.post("", response_model=MissionOut, status_code=status.HTTP_201_CREATED)
def create_mission(
    payload: MissionCreate,
    request: Request,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> MissionOut:
    service = MissionService(db)
    try:
        mission = service.create_mission(current_user.organization_id, current_user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    AuditRepository(db).record(
        user_id=current_user.id,
        action="mission_created",
        resource="mission",
        resource_id=str(mission.id),
        ip_address=request.client.host if request.client else None,
        metadata={"name": mission.name},
    )
    return MissionOut.from_model(mission, service.area_sq_meters(mission))


@router.patch("/{mission_id}", response_model=MissionOut)
def update_mission(
    mission_id: uuid.UUID,
    payload: MissionUpdate,
    request: Request,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> MissionOut:
    service = MissionService(db)
    try:
        mission = service.update_mission(mission_id, current_user.organization_id, payload)
    except MissionNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")

    action = "mission_updated"
    if payload.status == "active":
        action = "mission_started"
    elif payload.status in ("completed", "aborted"):
        action = "mission_stopped"

    AuditRepository(db).record(
        user_id=current_user.id,
        action=action,
        resource="mission",
        resource_id=str(mission_id),
        ip_address=request.client.host if request.client else None,
        metadata=payload.model_dump(exclude_unset=True, mode="json"),
    )
    return MissionOut.from_model(mission, service.area_sq_meters(mission))


@router.delete("/{mission_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mission(
    mission_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> None:
    service = MissionService(db)
    try:
        service.delete_mission(mission_id, current_user.organization_id)
    except MissionNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")

    AuditRepository(db).record(
        user_id=current_user.id,
        action="mission_deleted",
        resource="mission",
        resource_id=str(mission_id),
        ip_address=request.client.host if request.client else None,
    )


@router.post("/{mission_id}/aoi", response_model=AOIOut)
def set_aoi(
    mission_id: uuid.UUID,
    payload: AOIUpsert,
    request: Request,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> AOIOut:
    service = MissionService(db)
    try:
        mission = service.set_aoi(mission_id, current_user.organization_id, payload.geometry)
    except MissionNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    AuditRepository(db).record(
        user_id=current_user.id,
        action="aoi_saved",
        resource="mission",
        resource_id=str(mission_id),
        ip_address=request.client.host if request.client else None,
    )

    vertex_count = len(payload.geometry.get("coordinates", [[]])[0]) if payload.geometry else None
    return AOIOut(
        mission_id=mission.id,
        geometry=payload.geometry,
        area_sq_meters=service.area_sq_meters(mission),
        vertex_count=vertex_count,
    )


@router.get("/{mission_id}/aoi", response_model=AOIOut)
def get_aoi(
    mission_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AOIOut:
    from app.core.geo import geography_to_geojson

    service = MissionService(db)
    try:
        mission = service.get_mission(mission_id, current_user.organization_id)
    except MissionNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")

    geometry = geography_to_geojson(mission.aoi_geometry)
    vertex_count = len(geometry["coordinates"][0]) if geometry else None
    return AOIOut(
        mission_id=mission.id,
        geometry=geometry,
        area_sq_meters=service.area_sq_meters(mission),
        vertex_count=vertex_count,
    )
