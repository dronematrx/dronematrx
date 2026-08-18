import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.maintenance import MaintenanceRecordOut
from app.schemas.payload import PayloadOut


class DroneBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    vehicle_type: str = "multirotor"
    model: str
    firmware: str = "PX4 v1.14"
    uin: str | None = None


class DroneCreate(DroneBase):
    status: str = "offline"
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    altitude: float = 0
    heading: float = 0
    battery: float = Field(100, ge=0, le=100)


class DroneUpdate(BaseModel):
    name: str | None = None
    vehicle_type: str | None = None
    model: str | None = None
    firmware: str | None = None
    uin: str | None = None
    status: str | None = None
    battery: float | None = Field(None, ge=0, le=100)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    altitude: float | None = None
    heading: float | None = None
    speed: float | None = None
    gps_status: str | None = None
    flight_mode: str | None = None
    armed: bool | None = None
    link_quality: float | None = None
    total_flight_hours: float | None = None
    battery_cycles: int | None = None
    active: bool | None = None


class DroneOut(DroneBase):
    id: uuid.UUID
    organization_id: uuid.UUID
    status: str
    battery: float
    latitude: float | None
    longitude: float | None
    altitude: float
    heading: float
    speed: float
    gps_status: str
    flight_mode: str
    armed: bool
    link_quality: float
    total_flight_hours: float
    battery_cycles: int
    last_telemetry_at: datetime | None
    active: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, drone) -> "DroneOut":
        from app.core.geo import geography_to_geojson

        lat = lon = None
        geojson = geography_to_geojson(drone.location)
        if geojson is not None:
            lon, lat = geojson["coordinates"][0], geojson["coordinates"][1]

        return cls(
            id=drone.id,
            organization_id=drone.organization_id,
            name=drone.name,
            vehicle_type=drone.vehicle_type,
            model=drone.model,
            firmware=drone.firmware,
            uin=drone.uin,
            status=drone.status,
            battery=drone.battery,
            latitude=lat,
            longitude=lon,
            altitude=drone.altitude,
            heading=drone.heading,
            speed=drone.speed,
            gps_status=drone.gps_status,
            flight_mode=drone.flight_mode,
            armed=drone.armed,
            link_quality=drone.link_quality,
            total_flight_hours=drone.total_flight_hours,
            battery_cycles=drone.battery_cycles,
            last_telemetry_at=drone.last_telemetry_at,
            active=drone.active,
            created_at=drone.created_at,
            updated_at=drone.updated_at,
        )


class DroneDetailOut(DroneOut):
    payloads: list[PayloadOut] = []
    maintenance_records: list[MaintenanceRecordOut] = []

    @classmethod
    def from_model(cls, drone) -> "DroneDetailOut":
        base = DroneOut.from_model(drone)
        return cls(
            **base.model_dump(),
            payloads=[PayloadOut.model_validate(p) for p in drone.payloads],
            maintenance_records=[MaintenanceRecordOut.model_validate(m) for m in drone.maintenance_records],
        )


class DroneListResponse(BaseModel):
    items: list[DroneOut]
    total: int
