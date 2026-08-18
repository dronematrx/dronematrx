import uuid

from sqlalchemy.orm import Session

from app.core.geo import geojson_to_geography
from app.models.geofence import Geofence
from app.repositories.geofence_repository import GeofenceRepository
from app.schemas.geofence import GeofenceCreate, GeofenceUpdate


class GeofenceNotFoundError(Exception):
    pass


class GeofenceService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = GeofenceRepository(db)

    def list_geofences(self, organization_id: uuid.UUID, type_filter: str | None = None) -> list[Geofence]:
        return self.repo.list(organization_id, type_filter=type_filter)

    def get_geofence(self, geofence_id: uuid.UUID, organization_id: uuid.UUID) -> Geofence:
        geofence = self.repo.get(geofence_id, organization_id)
        if geofence is None:
            raise GeofenceNotFoundError(str(geofence_id))
        return geofence

    def create_geofence(self, organization_id: uuid.UUID, payload: GeofenceCreate) -> Geofence:
        geofence = Geofence(
            organization_id=organization_id,
            name=payload.name,
            type=payload.type,
            geometry=geojson_to_geography(payload.geometry),
            altitude_min=payload.altitude_min,
            altitude_max=payload.altitude_max,
            active=payload.active,
        )
        return self.repo.create(geofence)

    def update_geofence(self, geofence_id: uuid.UUID, organization_id: uuid.UUID, payload: GeofenceUpdate) -> Geofence:
        geofence = self.get_geofence(geofence_id, organization_id)
        data = payload.model_dump(exclude_unset=True, exclude={"geometry"})
        for field, value in data.items():
            setattr(geofence, field, value)
        if payload.geometry is not None:
            geofence.geometry = geojson_to_geography(payload.geometry)
        return self.repo.update(geofence)

    def delete_geofence(self, geofence_id: uuid.UUID, organization_id: uuid.UUID) -> None:
        geofence = self.get_geofence(geofence_id, organization_id)
        self.repo.delete(geofence)

    def find_intersecting(self, organization_id: uuid.UUID, lat: float, lon: float) -> list[Geofence]:
        from app.core.geo import point_to_geojson

        point = geojson_to_geography(point_to_geojson(lon, lat))
        return self.repo.intersecting(organization_id, point)
