"""Schemas for the 6-step mission planning wizard, CBBA plans, validation
reports, and mission state transitions."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.mission import VALID_TEMPLATES
from app.models.payload import VALID_PAYLOAD_TYPES
from app.schemas.common import GeoJSONPolygon

TemplateName = Literal["survey", "search", "inspection", "mapping", "custom"]
SensorType = Literal["camera", "thermal", "lidar", "multispectral", "sprayer", "custom"]
WIZARD_STEPS = {1: "template", 2: "aoi", 3: "flight", 4: "sensors", 5: "constraints", 6: "validate"}
REQUIRED_STEPS = (1, 2, 3, 4, 5)

assert set(TemplateName.__args__) == set(VALID_TEMPLATES)  # type: ignore[attr-defined]
assert set(SensorType.__args__) <= set(VALID_PAYLOAD_TYPES)  # type: ignore[attr-defined]


# ------------------------------------------------------------------ steps


class TemplateStep(BaseModel):
    template: TemplateName
    name: str | None = Field(None, min_length=1, max_length=255)


class AOIStep(BaseModel):
    source: Literal["drawn", "rectangle", "existing", "copied"] = "drawn"
    geometry: dict[str, Any] | None = None
    copy_from_mission_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _check(self) -> "AOIStep":
        if self.source in ("drawn", "rectangle"):
            if self.geometry is None:
                raise ValueError("geometry is required for a drawn AOI")
            GeoJSONPolygon.model_validate(self.geometry)
            ring = self.geometry["coordinates"][0] if self.geometry.get("coordinates") else []
            if len(ring) < 4:
                raise ValueError("AOI polygon needs at least 3 distinct vertices")
        if self.source == "copied" and self.copy_from_mission_id is None:
            raise ValueError("copy_from_mission_id is required when copying an AOI")
        return self


class FlightParams(BaseModel):
    altitude_m: float = Field(60, ge=10, le=120, description="DGCA limit: 120 m AGL")
    altitude_ref: Literal["AGL", "MSL"] = "AGL"
    speed_mps: float = Field(10, ge=1, le=25)
    overlap_pct: float = Field(70, ge=0, le=90)
    pattern: Literal["lawnmower", "crosshatch"] = "lawnmower"
    heading_deg: float | None = Field(None, ge=0, lt=360, description="Lane bearing lock; null = AOI long axis")
    rth_on_complete: bool = True
    rth_battery_pct: float = Field(25, ge=10, le=60, description="Battery reserve that triggers return-to-home")
    priority: int = Field(2, ge=1, le=5)


class SensorsStep(BaseModel):
    required_sensors: list[SensorType] = Field(..., min_length=1, max_length=3)
    drone_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=16)
    payload_assignments: dict[str, SensorType | None] = Field(default_factory=dict)

    @field_validator("required_sensors")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    @field_validator("drone_ids")
    @classmethod
    def _unique(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(v)) != len(v):
            raise ValueError("drone_ids must be unique")
        return v


class InspectionPoint(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    required_sensor: SensorType | None = None
    priority: int = Field(3, ge=1, le=5)
    hover_time_s: float = Field(60, ge=0, le=900)
    label: str | None = Field(None, max_length=60)


class ConstraintsStep(BaseModel):
    min_battery_pct: float = Field(40, ge=10, le=100, description="Minimum battery to launch")
    max_flight_time_min: float | None = Field(None, ge=1, le=180)
    max_range_km: float | None = Field(None, ge=0.1, le=100)
    max_tasks_per_drone: int = Field(15, ge=1, le=60)
    geofence_ids: list[uuid.UUID] = Field(default_factory=list, description="Inclusion/exclusion zones to enforce (NFZs always are)")
    link_loss_policy: Literal["RTH", "HOLD", "CONTINUE", "LAND"] = "RTH"
    accept_unassigned: bool = False
    inspection_points: list[InspectionPoint] = Field(default_factory=list, max_length=20)
    algorithm: str = "cbba"


STEP_SCHEMAS: dict[int, type[BaseModel]] = {
    1: TemplateStep,
    2: AOIStep,
    3: FlightParams,
    4: SensorsStep,
    5: ConstraintsStep,
}


class WizardStepSave(BaseModel):
    data: dict[str, Any]


class WizardState(BaseModel):
    mission_id: uuid.UUID
    name: str
    status: str
    template: str | None
    wizard_step: int
    completed_steps: list[int]
    config: dict[str, Any]
    aoi_geometry: dict[str, Any] | None
    aoi_area_sq_meters: float | None
    editable: bool
    plan_id: uuid.UUID | None = None
    plan_stale: bool = False
    validation: dict[str, Any] | None = None
    replanned: bool = False


class TemplateOut(BaseModel):
    key: str
    label: str
    description: str
    mission_type: str
    flight: dict[str, Any]
    required_sensors: list[str]


# ------------------------------------------------------------------ plans


class PlanTaskOut(BaseModel):
    task_key: str
    task_type: str
    source: str
    geometry: dict[str, Any]
    priority: int
    required_capability: str | None
    length_m: float
    status: str
    assigned_drone_id: uuid.UUID | None = None
    unassigned_reason: str | None = None


class BundleTaskOut(BaseModel):
    task_key: str
    order: int
    eta_seconds: float
    bid: float
    energy_pct: float


class BundleOut(BaseModel):
    drone_id: uuid.UUID | None
    drone_name: str
    color_index: int
    task_count: int
    path: list[str]
    waypoints: list[list[float]]
    distance_m: float
    eta_seconds: float
    energy_pct: float
    battery_start_pct: float
    battery_end_pct: float
    reserve_pct: float
    battery_warning: bool
    coverage_pct: float
    tasks: list[BundleTaskOut]


class PlanOut(BaseModel):
    id: uuid.UUID
    mission_id: uuid.UUID
    algorithm: str
    trigger: str
    converged: bool
    timed_out: bool
    iterations: int
    consensus_rounds: int
    messages_exchanged: int
    convergence_time_ms: float
    drone_count: int
    task_count: int
    assigned_count: int
    unassigned_count: int
    total_distance_m: float
    estimated_duration_s: float
    estimated_energy_wh: float
    coverage_pct: float
    max_coverage_pct: float
    average_bid: float
    stale: bool = False
    params: dict[str, Any]
    diagnostics: dict[str, Any]
    bundles: list[BundleOut]
    tasks: list[PlanTaskOut]
    created_at: datetime


class AllocateRequest(BaseModel):
    algorithm: str | None = None


class ReallocateRequest(BaseModel):
    add_drone_ids: list[uuid.UUID] = Field(default_factory=list)
    remove_drone_ids: list[uuid.UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _non_empty(self) -> "ReallocateRequest":
        if not self.add_drone_ids and not self.remove_drone_ids:
            raise ValueError("Provide at least one drone to add or remove")
        if set(self.add_drone_ids) & set(self.remove_drone_ids):
            raise ValueError("A drone cannot be both added and removed")
        return self


class MissionDroneAdd(BaseModel):
    drone_id: uuid.UUID


# ------------------------------------------------------- validation/state


class ValidationCheck(BaseModel):
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class SpatialConflict(BaseModel):
    kind: Literal["nfz", "exclusion", "inclusion", "altitude_ceiling"]
    scope: Literal["aoi", "route", "launch"]
    geofence_id: uuid.UUID
    geofence_name: str
    drone_id: uuid.UUID | None = None
    intersection: dict[str, Any] | None = None


class ValidationReport(BaseModel):
    passed: bool
    checked_at: datetime
    plan_id: uuid.UUID | None
    checks: list[ValidationCheck]
    conflicts: list[SpatialConflict]
    nfz_conflict: bool


class TransitionRequest(BaseModel):
    target_state: Literal["draft", "planning", "validating", "armed", "active", "completed", "aborted"]
    reason: str | None = Field(None, max_length=500)


class StateHistoryOut(BaseModel):
    id: uuid.UUID
    mission_id: uuid.UUID
    previous_state: str
    new_state: str
    user_id: uuid.UUID | None
    reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
