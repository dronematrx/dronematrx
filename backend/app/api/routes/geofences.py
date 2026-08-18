import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_mission_write
from app.db.session import get_db
from app.models.user import User
from app.repositories.audit_repository import AuditRepository
from app.schemas.geofence import GeofenceCreate, GeofenceOut, GeofenceUpdate
from app.services.geofence_service import GeofenceNotFoundError, GeofenceService

router = APIRouter(prefix="/geofences", tags=["geofences"])


@router.get("", response_model=list[GeofenceOut])
def list_geofences(
    type: str | None = Query(None, description="Filter by 'geofence' or 'nfz'"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[GeofenceOut]:
    geofences = GeofenceService(db).list_geofences(current_user.organization_id, type_filter=type)
    return [GeofenceOut.from_model(g) for g in geofences]


@router.post("", response_model=GeofenceOut, status_code=status.HTTP_201_CREATED)
def create_geofence(
    payload: GeofenceCreate,
    request: Request,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> GeofenceOut:
    try:
        geofence = GeofenceService(db).create_geofence(current_user.organization_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    AuditRepository(db).record(
        user_id=current_user.id,
        action="geofence_created",
        resource="geofence",
        resource_id=str(geofence.id),
        ip_address=request.client.host if request.client else None,
        metadata={"name": geofence.name, "type": geofence.type},
    )
    return GeofenceOut.from_model(geofence)


@router.patch("/{geofence_id}", response_model=GeofenceOut)
def update_geofence(
    geofence_id: uuid.UUID,
    payload: GeofenceUpdate,
    request: Request,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> GeofenceOut:
    try:
        geofence = GeofenceService(db).update_geofence(geofence_id, current_user.organization_id, payload)
    except GeofenceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geofence not found")

    AuditRepository(db).record(
        user_id=current_user.id,
        action="geofence_updated",
        resource="geofence",
        resource_id=str(geofence_id),
        ip_address=request.client.host if request.client else None,
    )
    return GeofenceOut.from_model(geofence)


@router.delete("/{geofence_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_geofence(
    geofence_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> None:
    try:
        GeofenceService(db).delete_geofence(geofence_id, current_user.organization_id)
    except GeofenceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geofence not found")

    AuditRepository(db).record(
        user_id=current_user.id,
        action="geofence_deleted",
        resource="geofence",
        resource_id=str(geofence_id),
        ip_address=request.client.host if request.client else None,
    )
