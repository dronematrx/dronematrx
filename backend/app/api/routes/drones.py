import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_fleet_write
from app.db.session import get_db
from app.models.maintenance_record import MaintenanceRecord
from app.models.payload import Payload
from app.models.user import User
from app.repositories.audit_repository import AuditRepository
from app.repositories.payload_repository import MaintenanceRepository, PayloadRepository
from app.schemas.drone import DroneCreate, DroneDetailOut, DroneListResponse, DroneOut, DroneUpdate
from app.schemas.maintenance import MaintenanceRecordCreate, MaintenanceRecordOut
from app.schemas.payload import PayloadCreate, PayloadOut
from app.schemas.telemetry import TelemetryUpdate
from app.services.drone_service import DroneNotFoundError, DroneService, DuplicateUINError

router = APIRouter(prefix="/drones", tags=["drones"])


@router.get("", response_model=DroneListResponse)
def list_drones(
    include_inactive: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DroneListResponse:
    drones = DroneService(db).list_drones(current_user.organization_id, include_inactive=include_inactive)
    items = [DroneOut.from_model(d) for d in drones]
    return DroneListResponse(items=items, total=len(items))


@router.get("/{drone_id}", response_model=DroneDetailOut)
def get_drone(
    drone_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DroneDetailOut:
    try:
        drone = DroneService(db).get_drone_detail(drone_id, current_user.organization_id)
    except DroneNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drone not found")
    return DroneDetailOut.from_model(drone)


@router.post("", response_model=DroneOut, status_code=status.HTTP_201_CREATED)
def create_drone(
    payload: DroneCreate,
    request: Request,
    current_user: User = Depends(require_fleet_write),
    db: Session = Depends(get_db),
) -> DroneOut:
    try:
        drone = DroneService(db).create_drone(current_user.organization_id, payload)
    except DuplicateUINError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A drone with this UIN already exists")

    AuditRepository(db).record(
        user_id=current_user.id,
        action="drone_created",
        resource="drone",
        resource_id=str(drone.id),
        ip_address=request.client.host if request.client else None,
        metadata={"name": drone.name},
    )
    return DroneOut.from_model(drone)


@router.patch("/{drone_id}", response_model=DroneOut)
def update_drone(
    drone_id: uuid.UUID,
    payload: DroneUpdate,
    request: Request,
    current_user: User = Depends(require_fleet_write),
    db: Session = Depends(get_db),
) -> DroneOut:
    try:
        drone = DroneService(db).update_drone(drone_id, current_user.organization_id, payload)
    except DroneNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drone not found")
    except DuplicateUINError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A drone with this UIN already exists")

    AuditRepository(db).record(
        user_id=current_user.id,
        action="drone_updated",
        resource="drone",
        resource_id=str(drone_id),
        ip_address=request.client.host if request.client else None,
        metadata=payload.model_dump(exclude_unset=True),
    )
    return DroneOut.from_model(drone)


@router.delete("/{drone_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_drone(
    drone_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_fleet_write),
    db: Session = Depends(get_db),
) -> None:
    """Soft-delete: marks the drone inactive/offline rather than removing the row,
    so mission history and audit trails stay intact."""
    try:
        DroneService(db).deactivate_drone(drone_id, current_user.organization_id)
    except DroneNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drone not found")

    AuditRepository(db).record(
        user_id=current_user.id,
        action="drone_deactivated",
        resource="drone",
        resource_id=str(drone_id),
        ip_address=request.client.host if request.client else None,
    )


@router.get("/{drone_id}/telemetry", response_model=TelemetryUpdate | None)
def get_drone_telemetry(
    drone_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.main import telemetry_manager

    try:
        DroneService(db).get_drone(drone_id, current_user.organization_id)
    except DroneNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drone not found")

    return telemetry_manager.latest.get(str(drone_id))


@router.get("/{drone_id}/payloads", response_model=list[PayloadOut])
def list_payloads(
    drone_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PayloadOut]:
    DroneService(db).get_drone(drone_id, current_user.organization_id)
    return [PayloadOut.model_validate(p) for p in PayloadRepository(db).list_for_drone(drone_id)]


@router.post("/{drone_id}/payloads", response_model=PayloadOut, status_code=status.HTTP_201_CREATED)
def add_payload(
    drone_id: uuid.UUID,
    payload: PayloadCreate,
    current_user: User = Depends(require_fleet_write),
    db: Session = Depends(get_db),
) -> PayloadOut:
    DroneService(db).get_drone(drone_id, current_user.organization_id)
    record = Payload(drone_id=drone_id, **payload.model_dump())
    return PayloadOut.model_validate(PayloadRepository(db).create(record))


@router.get("/{drone_id}/maintenance", response_model=list[MaintenanceRecordOut])
def list_maintenance(
    drone_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MaintenanceRecordOut]:
    DroneService(db).get_drone(drone_id, current_user.organization_id)
    return [MaintenanceRecordOut.model_validate(m) for m in MaintenanceRepository(db).list_for_drone(drone_id)]


@router.post("/{drone_id}/maintenance", response_model=MaintenanceRecordOut, status_code=status.HTTP_201_CREATED)
def add_maintenance_record(
    drone_id: uuid.UUID,
    payload: MaintenanceRecordCreate,
    current_user: User = Depends(require_fleet_write),
    db: Session = Depends(get_db),
) -> MaintenanceRecordOut:
    DroneService(db).get_drone(drone_id, current_user.organization_id)
    record = MaintenanceRecord(drone_id=drone_id, **payload.model_dump())
    return MaintenanceRecordOut.model_validate(MaintenanceRepository(db).create(record))
