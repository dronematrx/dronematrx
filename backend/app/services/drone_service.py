import uuid

from sqlalchemy.orm import Session

from app.core.geo import point_to_geojson
from app.models.drone import Drone
from app.repositories.drone_repository import DroneRepository
from app.schemas.drone import DroneCreate, DroneUpdate


class DroneNotFoundError(Exception):
    pass


class DuplicateUINError(Exception):
    pass


class DroneService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = DroneRepository(db)

    def list_drones(self, organization_id: uuid.UUID, include_inactive: bool = False) -> list[Drone]:
        return self.repo.list(organization_id, include_inactive=include_inactive)

    def get_drone(self, drone_id: uuid.UUID, organization_id: uuid.UUID) -> Drone:
        drone = self.repo.get(drone_id, organization_id)
        if drone is None:
            raise DroneNotFoundError(str(drone_id))
        return drone

    def get_drone_detail(self, drone_id: uuid.UUID, organization_id: uuid.UUID) -> Drone:
        drone = self.repo.get_with_relations(drone_id, organization_id)
        if drone is None:
            raise DroneNotFoundError(str(drone_id))
        return drone

    def create_drone(self, organization_id: uuid.UUID, payload: DroneCreate) -> Drone:
        if payload.uin and self.repo.get_by_uin(payload.uin) is not None:
            raise DuplicateUINError(payload.uin)

        from app.core.geo import geojson_to_geography

        location = None
        if payload.latitude is not None and payload.longitude is not None:
            location = geojson_to_geography(point_to_geojson(payload.longitude, payload.latitude))

        drone = Drone(
            organization_id=organization_id,
            name=payload.name,
            vehicle_type=payload.vehicle_type,
            model=payload.model,
            firmware=payload.firmware,
            uin=payload.uin,
            status=payload.status,
            battery=payload.battery,
            location=location,
            altitude=payload.altitude,
            heading=payload.heading,
        )
        return self.repo.create(drone)

    def update_drone(self, drone_id: uuid.UUID, organization_id: uuid.UUID, payload: DroneUpdate) -> Drone:
        drone = self.get_drone(drone_id, organization_id)

        if payload.uin and payload.uin != drone.uin:
            existing = self.repo.get_by_uin(payload.uin)
            if existing is not None and existing.id != drone.id:
                raise DuplicateUINError(payload.uin)

        data = payload.model_dump(exclude_unset=True, exclude={"latitude", "longitude"})
        for field, value in data.items():
            setattr(drone, field, value)

        if payload.latitude is not None and payload.longitude is not None:
            from app.core.geo import geojson_to_geography

            drone.location = geojson_to_geography(point_to_geojson(payload.longitude, payload.latitude))

        return self.repo.update(drone)

    def deactivate_drone(self, drone_id: uuid.UUID, organization_id: uuid.UUID) -> Drone:
        drone = self.get_drone(drone_id, organization_id)
        return self.repo.soft_delete(drone)

    def apply_telemetry(
        self,
        drone: Drone,
        *,
        latitude: float,
        longitude: float,
        altitude: float,
        heading: float,
        speed: float,
        battery: float,
        flight_mode: str,
        gps_status: str,
        armed: bool,
        link_quality: float,
        status: str,
        timestamp,
    ) -> Drone:
        from app.core.geo import geojson_to_geography

        drone.location = geojson_to_geography(point_to_geojson(longitude, latitude))
        drone.altitude = altitude
        drone.heading = heading
        drone.speed = speed
        drone.battery = battery
        drone.flight_mode = flight_mode
        drone.gps_status = gps_status
        drone.armed = armed
        drone.link_quality = link_quality
        drone.status = status
        drone.last_telemetry_at = timestamp
        self.db.commit()
        self.db.refresh(drone)
        return drone
