import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

from app.schemas.common import GeoJSONPolygon


class GeofenceBase(BaseModel):
    name: str
    type: str = "geofence"  # "geofence" | "nfz"
    altitude_min: float = 0
    altitude_max: float = 120


class GeofenceCreate(GeofenceBase):
    geometry: dict[str, Any]
    active: bool = True

    @field_validator("geometry")
    @classmethod
    def validate_geometry(cls, v: dict[str, Any]) -> dict[str, Any]:
        GeoJSONPolygon.model_validate(v)
        return v


class GeofenceUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    geometry: dict[str, Any] | None = None
    altitude_min: float | None = None
    altitude_max: float | None = None
    active: bool | None = None

    @field_validator("geometry")
    @classmethod
    def validate_geometry(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            GeoJSONPolygon.model_validate(v)
        return v


class GeofenceOut(GeofenceBase):
    id: uuid.UUID
    organization_id: uuid.UUID
    geometry: dict[str, Any] | None
    active: bool
    created_at: datetime

    @classmethod
    def from_model(cls, geofence) -> "GeofenceOut":
        from app.core.geo import geography_to_geojson

        return cls(
            id=geofence.id,
            organization_id=geofence.organization_id,
            name=geofence.name,
            type=geofence.type,
            geometry=geography_to_geojson(geofence.geometry),
            altitude_min=geofence.altitude_min,
            altitude_max=geofence.altitude_max,
            active=geofence.active,
            created_at=geofence.created_at,
        )
