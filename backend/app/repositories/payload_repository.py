import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.maintenance_record import MaintenanceRecord
from app.models.payload import Payload


class PayloadRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_for_drone(self, drone_id: uuid.UUID) -> list[Payload]:
        stmt = select(Payload).where(Payload.drone_id == drone_id).order_by(Payload.created_at)
        return list(self.db.execute(stmt).scalars().all())

    def create(self, payload: Payload) -> Payload:
        self.db.add(payload)
        self.db.commit()
        self.db.refresh(payload)
        return payload

    def get(self, payload_id: uuid.UUID, drone_id: uuid.UUID) -> Payload | None:
        stmt = select(Payload).where(Payload.id == payload_id, Payload.drone_id == drone_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def update(self, payload: Payload) -> Payload:
        self.db.commit()
        self.db.refresh(payload)
        return payload

    def delete(self, payload: Payload) -> None:
        self.db.delete(payload)
        self.db.commit()


class MaintenanceRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_for_drone(self, drone_id: uuid.UUID) -> list[MaintenanceRecord]:
        stmt = (
            select(MaintenanceRecord)
            .where(MaintenanceRecord.drone_id == drone_id)
            .order_by(MaintenanceRecord.service_date.desc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def create(self, record: MaintenanceRecord) -> MaintenanceRecord:
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record
