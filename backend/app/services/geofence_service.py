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

    def import_zones(self, organization_id: uuid.UUID, zones, default_type: str):
        """Create a geofence per parsed zone. Returns (created, skipped[(index, name, reason)])."""
        from app.schemas.geofence import normalize_geofence_type

        created: list[Geofence] = []
        skipped: list[tuple[int, str | None, str]] = []
        for index, zone in enumerate(zones):
            if zone.error or zone.geometry is None:
                skipped.append((index, zone.name, zone.error or "missing geometry"))
                continue
            try:
                zone_type = normalize_geofence_type(zone.type) if zone.type else default_type
                ring = zone.geometry["coordinates"][0]
                if len(ring) < 4:
                    raise ValueError("Polygon needs at least 3 vertices")
                geometry = geojson_to_geography(zone.geometry)
            except (ValueError, KeyError, TypeError) as exc:
                skipped.append((index, zone.name, str(exc)))
                continue
            altitude_max = zone.altitude_max if zone.altitude_max and zone.altitude_max > 0 else 120
            geofence = Geofence(
                organization_id=organization_id,
                name=(zone.name or f"Imported zone {index + 1}")[:255],
                type=zone_type,
                geometry=geometry,
                altitude_min=max(zone.altitude_min or 0, 0),
                altitude_max=altitude_max,
                active=True,
            )
            self.db.add(geofence)
            created.append(geofence)
        self.db.commit()
        for geofence in created:
            self.db.refresh(geofence)
        return created, skipped

    def find_intersecting(self, organization_id: uuid.UUID, lat: float, lon: float) -> list[Geofence]:
        from app.core.geo import point_to_geojson

        point = geojson_to_geography(point_to_geojson(lon, lat))
        return self.repo.intersecting(organization_id, point)
