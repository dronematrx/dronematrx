from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.drone import Drone


class DroneRepository:
    def __init__(self, db: Session):
        self.db = db

    def list(self, organization_id: uuid.UUID, include_inactive: bool = False) -> list[Drone]:
        stmt = select(Drone).where(Drone.organization_id == organization_id)
        if not include_inactive:
            stmt = stmt.where(Drone.active.is_(True))
        stmt = stmt.order_by(Drone.name)
        return list(self.db.execute(stmt).scalars().all())

    def get(self, drone_id: uuid.UUID, organization_id: uuid.UUID) -> Drone | None:
        stmt = select(Drone).where(Drone.id == drone_id, Drone.organization_id == organization_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_with_relations(self, drone_id: uuid.UUID, organization_id: uuid.UUID) -> Drone | None:
        stmt = (
            select(Drone)
            .options(joinedload(Drone.payloads), joinedload(Drone.maintenance_records))
            .where(Drone.id == drone_id, Drone.organization_id == organization_id)
        )
        return self.db.execute(stmt).unique().scalar_one_or_none()

    def get_by_uin(self, uin: str) -> Drone | None:
        stmt = select(Drone).where(Drone.uin == uin)
        return self.db.execute(stmt).scalar_one_or_none()

    def create(self, drone: Drone) -> Drone:
        self.db.add(drone)
        self.db.commit()
        self.db.refresh(drone)
        return drone

    def update(self, drone: Drone) -> Drone:
        self.db.commit()
        self.db.refresh(drone)
        return drone

    def soft_delete(self, drone: Drone) -> Drone:
        drone.active = False
        drone.status = "offline"
        self.db.commit()
        self.db.refresh(drone)
        return drone

    def find_within_geometry(self, organization_id: uuid.UUID, geography) -> list[Drone]:
        """Spatial query: drones whose current location falls inside a geography (e.g. an AOI).

        ST_Covers is used (rather than ST_Within) because it has a native
        geography-typed overload in PostGIS, so the comparison runs directly
        on the sphere without an implicit geometry cast.
        """
        from sqlalchemy import func

        stmt = select(Drone).where(
            Drone.organization_id == organization_id,
            Drone.location.isnot(None),
            func.ST_Covers(geography, Drone.location),
        )
        return list(self.db.execute(stmt).scalars().all())
