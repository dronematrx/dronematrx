import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, field_validator

from app.schemas.common import GeoJSONPolygon


class MissionBase(BaseModel):
    name: str
    mission_type: str = "OTHER"


class MissionCreate(MissionBase):
    # New missions always start in DRAFT; every later state goes through
    # POST /missions/{id}/transition (the state machine).
    status: Literal["draft"] = "draft"
    aoi_geometry: dict[str, Any] | None = None

    @field_validator("aoi_geometry")
    @classmethod
    def validate_aoi(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return v
        GeoJSONPolygon.model_validate(v)  # raises if malformed
        return v


class MissionUpdate(BaseModel):
    name: str | None = None
    mission_type: str | None = None
    status: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None


class MissionOut(MissionBase):
    id: uuid.UUID
    organization_id: uuid.UUID
    status: str
    aoi_geometry: dict[str, Any] | None
    aoi_area_sq_meters: float | None = None
    start_time: datetime | None
    end_time: datetime | None
    template: str | None = None
    wizard_step: int = 1
    completed_steps: list[int] = []
    validation_passed: bool | None = None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, mission, area_sq_meters: float | None = None) -> "MissionOut":
        from app.core.geo import geography_to_geojson

        return cls(
            id=mission.id,
            organization_id=mission.organization_id,
            name=mission.name,
            mission_type=mission.mission_type,
            status=mission.status,
            aoi_geometry=geography_to_geojson(mission.aoi_geometry),
            aoi_area_sq_meters=area_sq_meters,
            start_time=mission.start_time,
            end_time=mission.end_time,
            template=mission.template,
            wizard_step=mission.wizard_step or 1,
            completed_steps=sorted((mission.config or {}).get("completed_steps", [])),
            validation_passed=(mission.validation_report or {}).get("passed") if mission.validation_report else None,
            created_by=mission.created_by,
            created_at=mission.created_at,
            updated_at=mission.updated_at,
        )


class AOIUpsert(BaseModel):
    geometry: dict[str, Any]

    @field_validator("geometry")
    @classmethod
    def validate_geometry(cls, v: dict[str, Any]) -> dict[str, Any]:
        GeoJSONPolygon.model_validate(v)
        return v


class AOIOut(BaseModel):
    mission_id: uuid.UUID
    geometry: dict[str, Any] | None
    area_sq_meters: float | None
    vertex_count: int | None
