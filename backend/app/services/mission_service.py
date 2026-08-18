import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.geo import geojson_to_geography
from app.models.mission import Mission
from app.repositories.mission_repository import MissionRepository
from app.schemas.mission import MissionCreate, MissionUpdate


class MissionNotFoundError(Exception):
    pass


class MissionService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = MissionRepository(db)

    def list_missions(self, organization_id: uuid.UUID) -> list[Mission]:
        return self.repo.list(organization_id)

    def get_mission(self, mission_id: uuid.UUID, organization_id: uuid.UUID) -> Mission:
        mission = self.repo.get(mission_id, organization_id)
        if mission is None:
            raise MissionNotFoundError(str(mission_id))
        return mission

    def create_mission(self, organization_id: uuid.UUID, created_by: uuid.UUID, payload: MissionCreate) -> Mission:
        aoi = geojson_to_geography(payload.aoi_geometry) if payload.aoi_geometry else None
        mission = Mission(
            organization_id=organization_id,
            name=payload.name,
            mission_type=payload.mission_type,
            status=payload.status,
            aoi_geometry=aoi,
            created_by=created_by,
        )
        return self.repo.create(mission)

    def update_mission(self, mission_id: uuid.UUID, organization_id: uuid.UUID, payload: MissionUpdate) -> Mission:
        mission = self.get_mission(mission_id, organization_id)
        updates = payload.model_dump(exclude_unset=True)

        new_status = updates.get("status")
        if new_status == "active" and mission.status != "active" and "start_time" not in updates:
            updates["start_time"] = datetime.now(timezone.utc)
        elif new_status in ("completed", "aborted") and "end_time" not in updates:
            updates["end_time"] = datetime.now(timezone.utc)

        for field, value in updates.items():
            setattr(mission, field, value)
        return self.repo.update(mission)

    def delete_mission(self, mission_id: uuid.UUID, organization_id: uuid.UUID) -> None:
        mission = self.get_mission(mission_id, organization_id)
        self.repo.delete(mission)

    def area_sq_meters(self, mission: Mission) -> float | None:
        return self.repo.area_sq_meters(mission)

    def set_aoi(self, mission_id: uuid.UUID, organization_id: uuid.UUID, geometry: dict[str, Any]) -> Mission:
        mission = self.get_mission(mission_id, organization_id)
        mission.aoi_geometry = geojson_to_geography(geometry)
        return self.repo.update(mission)
