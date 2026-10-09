import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_mission_write
from app.db.session import get_db
from app.models.user import User
from app.repositories.audit_repository import AuditRepository
from app.schemas.geofence import (
    GeofenceCreate,
    GeofenceImportRequest,
    GeofenceImportResult,
    GeofenceImportSkipped,
    GeofenceOut,
    GeofenceUpdate,
    normalize_geofence_type,
)
from app.services.geofence_io import GeofenceImportError, parse_geojson, parse_kml, to_geojson, to_kml
from app.services.geofence_service import GeofenceNotFoundError, GeofenceService

router = APIRouter(prefix="/geofences", tags=["geofences"])


@router.get("", response_model=list[GeofenceOut])
def list_geofences(
    type: str | None = Query(None, description="Filter by 'inclusion', 'exclusion' or 'nfz' ('geofence' = inclusion)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[GeofenceOut]:
    type_filter = _type_param(type)
    geofences = GeofenceService(db).list_geofences(current_user.organization_id, type_filter=type_filter)
    return [GeofenceOut.from_model(g) for g in geofences]


@router.get("/export")
def export_geofences(
    format: Literal["geojson", "kml"] = Query("geojson"),
    type: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    geofences = GeofenceService(db).list_geofences(current_user.organization_id, type_filter=_type_param(type))
    if format == "kml":
        return Response(
            content=to_kml(geofences),
            media_type="application/vnd.google-earth.kml+xml",
            headers={"Content-Disposition": 'attachment; filename="dronematrx-geofences.kml"'},
        )
    import json

    return Response(
        content=json.dumps(to_geojson(geofences)),
        media_type="application/geo+json",
        headers={"Content-Disposition": 'attachment; filename="dronematrx-geofences.geojson"'},
    )


@router.post("/import", response_model=GeofenceImportResult, status_code=status.HTTP_201_CREATED)
def import_geofences(
    payload: GeofenceImportRequest,
    request: Request,
    current_user: User = Depends(require_mission_write),
    db: Session = Depends(get_db),
) -> GeofenceImportResult:
    try:
        if payload.format == "kml":
            if not isinstance(payload.content, str):
                raise GeofenceImportError("KML content must be a string")
            zones = parse_kml(payload.content)
        else:
            zones = parse_geojson(payload.content)
    except GeofenceImportError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    created, skipped = GeofenceService(db).import_zones(current_user.organization_id, zones, payload.default_type)
    if not created and skipped:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No valid zones imported: {skipped[0][2]}",
        )
    AuditRepository(db).record(
        user_id=current_user.id,
        action="geofences_imported",
        resource="geofence",
        resource_id=None,
        ip_address=request.client.host if request.client else None,
        metadata={"format": payload.format, "created": len(created), "skipped": len(skipped)},
    )
    return GeofenceImportResult(
        created=[GeofenceOut.from_model(g) for g in created],
        skipped=[GeofenceImportSkipped(index=i, name=n, reason=r) for i, n, r in skipped],
    )


@router.get("/{geofence_id}", response_model=GeofenceOut)
def get_geofence(
    geofence_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GeofenceOut:
    try:
        return GeofenceOut.from_model(GeofenceService(db).get_geofence(geofence_id, current_user.organization_id))
    except GeofenceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geofence not found")


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
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    AuditRepository(db).record(
        user_id=current_user.id,
        action="geofence_updated",
        resource="geofence",
        resource_id=str(geofence_id),
        ip_address=request.client.host if request.client else None,
        metadata=payload.model_dump(exclude_unset=True, exclude={"geometry"}),
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


def _type_param(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return normalize_geofence_type(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
