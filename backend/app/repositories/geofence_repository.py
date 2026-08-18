from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.geofence import Geofence


class GeofenceRepository:
    def __init__(self, db: Session):
        self.db = db

    def list(self, organization_id: uuid.UUID, type_filter: str | None = None) -> list[Geofence]:
        stmt = select(Geofence).where(Geofence.organization_id == organization_id)
        if type_filter:
            stmt = stmt.where(Geofence.type == type_filter)
        stmt = stmt.order_by(Geofence.created_at.desc())
        return list(self.db.execute(stmt).scalars().all())

    def get(self, geofence_id: uuid.UUID, organization_id: uuid.UUID) -> Geofence | None:
        stmt = select(Geofence).where(Geofence.id == geofence_id, Geofence.organization_id == organization_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def create(self, geofence: Geofence) -> Geofence:
        self.db.add(geofence)
        self.db.commit()
        self.db.refresh(geofence)
        return geofence

    def update(self, geofence: Geofence) -> Geofence:
        self.db.commit()
        self.db.refresh(geofence)
        return geofence

    def delete(self, geofence: Geofence) -> None:
        self.db.delete(geofence)
        self.db.commit()

    def intersecting(self, organization_id: uuid.UUID, point_geography) -> list[Geofence]:
        """Spatial query: geofences/NFZs whose polygon covers a given point (e.g. a drone position)."""
        from sqlalchemy import func

        stmt = select(Geofence).where(
            Geofence.organization_id == organization_id,
            Geofence.active.is_(True),
            func.ST_Covers(Geofence.geometry, point_geography),
        )
        return list(self.db.execute(stmt).scalars().all())
