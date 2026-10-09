from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.mission import Mission


class MissionRepository:
    def __init__(self, db: Session):
        self.db = db

    def list(self, organization_id: uuid.UUID) -> list[Mission]:
        stmt = select(Mission).where(Mission.organization_id == organization_id).order_by(Mission.created_at.desc())
        return list(self.db.execute(stmt).scalars().all())

    def get(self, mission_id: uuid.UUID, organization_id: uuid.UUID) -> Mission | None:
        stmt = select(Mission).where(Mission.id == mission_id, Mission.organization_id == organization_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def create(self, mission: Mission) -> Mission:
        self.db.add(mission)
        self.db.commit()
        self.db.refresh(mission)
        return mission

    def update(self, mission: Mission) -> Mission:
        self.db.commit()
        self.db.refresh(mission)
        return mission

    def delete(self, mission: Mission) -> None:
        self.db.delete(mission)
        self.db.commit()

    def area_sq_meters(self, mission: Mission) -> float | None:
        if mission.aoi_geometry is None:
            return None
        from sqlalchemy import func

        # Measure the stored geography column (square metres on the spheroid).
        # Binding the in-memory element instead would be read as planar
        # geometry and return square *degrees*.
        self.db.flush()
        return self.db.scalar(select(func.ST_Area(Mission.aoi_geometry)).where(Mission.id == mission.id))
