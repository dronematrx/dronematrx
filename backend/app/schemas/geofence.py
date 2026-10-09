import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.models.geofence import LEGACY_TYPE_ALIASES, VALID_GEOFENCE_TYPES
from app.schemas.common import GeoJSONPolygon


def normalize_geofence_type(value: str) -> str:
    value = LEGACY_TYPE_ALIASES.get(value.lower(), value.lower())
    if value not in VALID_GEOFENCE_TYPES:
        raise ValueError(f"type must be one of {VALID_GEOFENCE_TYPES}")
    return value


class GeofenceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    type: str = "inclusion"  # inclusion | exclusion | nfz  ("geofence" accepted as inclusion)
    altitude_min: float = Field(0, ge=0)
    altitude_max: float = Field(120, gt=0)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        return normalize_geofence_type(v)


class GeofenceCreate(GeofenceBase):
    geometry: dict[str, Any]
    active: bool = True

    @field_validator("geometry")
    @classmethod
    def validate_geometry(cls, v: dict[str, Any]) -> dict[str, Any]:
        GeoJSONPolygon.model_validate(v)
        return v


class GeofenceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
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

    @field_validator("type")
    @classmethod
    def _type(cls, v: str | None) -> str | None:
        return normalize_geofence_type(v) if v is not None else v


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


class GeofenceImportRequest(BaseModel):
    format: Literal["geojson", "kml"]
    content: str | dict[str, Any]
    default_type: str = "inclusion"

    @field_validator("default_type")
    @classmethod
    def _type(cls, v: str) -> str:
        return normalize_geofence_type(v)


class GeofenceImportSkipped(BaseModel):
    index: int
    name: str | None
    reason: str


class GeofenceImportResult(BaseModel):
    created: list[GeofenceOut]
    skipped: list[GeofenceImportSkipped]
