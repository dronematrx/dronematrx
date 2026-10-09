"""Reusable PostGIS spatial checks for mission safety.

All checks run in the database on geography columns, so they are the
authoritative answer regardless of what the frontend shows:

  * AOI vs NFZ / exclusion zones           (ST_Intersects)
  * planned route (incl. waypoints) vs NFZ / exclusion  (ST_Intersects)
  * launch point inside an NFZ / exclusion (ST_Covers)
  * route/AOI containment in inclusion zones (ST_Covers over the union)
  * inclusion-zone altitude ceilings below the planned altitude
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from shapely.geometry import shape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.geofence import Geofence


@dataclass(frozen=True)
class Conflict:
    kind: str  # nfz | exclusion | inclusion | altitude_ceiling
    scope: str  # aoi | route | launch
    geofence_id: uuid.UUID
    geofence_name: str
    drone_id: uuid.UUID | None = None
    intersection: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "scope": self.scope,
            "geofence_id": str(self.geofence_id),
            "geofence_name": self.geofence_name,
            "drone_id": str(self.drone_id) if self.drone_id else None,
            "intersection": self.intersection,
        }


def _wkt(geojson: dict[str, Any]) -> str:
    return shape(geojson).wkt


class SpatialValidator:
    def __init__(self, db: Session, organization_id: uuid.UUID):
        self.db = db
        self.organization_id = organization_id

    def _zones(self, types: Iterable[str], geofence_ids: Iterable[uuid.UUID] | None = None):
        stmt = select(Geofence).where(
            Geofence.organization_id == self.organization_id,
            Geofence.active.is_(True),
            Geofence.type.in_(list(types)),
        )
        if geofence_ids is not None:
            ids = list(geofence_ids)
            if not ids:
                return None
            stmt = stmt.where(Geofence.id.in_(ids))
        return stmt

    def intersecting(
        self,
        geojson: dict[str, Any],
        types: Iterable[str],
        geofence_ids: Iterable[uuid.UUID] | None = None,
    ) -> list[tuple[Geofence, dict[str, Any] | None]]:
        """Zones of the given types whose polygon intersects `geojson`, with the
        intersection geometry (for map overlays)."""
        stmt = self._zones(types, geofence_ids)
        if stmt is None:
            return []
        target = func.ST_GeogFromText(_wkt(geojson))
        rows = self.db.execute(
            stmt.add_columns(func.ST_AsGeoJSON(func.ST_Intersection(Geofence.geometry, target)))
            .where(func.ST_Intersects(Geofence.geometry, target))
            .order_by(Geofence.name)
        ).all()
        return [(row[0], json.loads(row[1]) if row[1] else None) for row in rows]

    def covering(self, geojson: dict[str, Any], types: Iterable[str], geofence_ids: Iterable[uuid.UUID] | None = None) -> list[Geofence]:
        """Zones whose polygon fully covers `geojson` (e.g. a launch point inside an NFZ)."""
        stmt = self._zones(types, geofence_ids)
        if stmt is None:
            return []
        target = func.ST_GeogFromText(_wkt(geojson))
        return list(self.db.execute(stmt.where(func.ST_Covers(Geofence.geometry, target)).order_by(Geofence.name)).scalars())

    def covered_by_union(self, geojson: dict[str, Any], geofence_ids: list[uuid.UUID]) -> bool:
        """Is `geojson` entirely inside the union of the given (inclusion) zones?"""
        if not geofence_ids:
            return True
        union = (
            select(func.ST_Union(func.Geometry(Geofence.geometry)))
            .where(Geofence.organization_id == self.organization_id, Geofence.id.in_(geofence_ids), Geofence.active.is_(True))
            .scalar_subquery()
        )
        covered = self.db.scalar(select(func.ST_Covers(union, func.ST_GeomFromText(_wkt(geojson), 4326))))
        return bool(covered)

    # -------------------------------------------------- mission-level checks

    def mission_conflicts(
        self,
        aoi: dict[str, Any] | None,
        routes: dict[uuid.UUID | None, dict[str, Any]],
        launch_points: dict[uuid.UUID, dict[str, Any]],
        enforced_zone_ids: list[uuid.UUID],
        altitude_m: float | None,
    ) -> list[Conflict]:
        """Every spatial conflict for a planned mission.

        NFZs are always enforced. Exclusion/inclusion zones are enforced when
        they are in `enforced_zone_ids` (selected in wizard Step 5).
        """
        conflicts: list[Conflict] = []
        selected = self.db.execute(
            select(Geofence).where(Geofence.organization_id == self.organization_id, Geofence.id.in_(enforced_zone_ids))
        ).scalars().all() if enforced_zone_ids else []
        exclusion_ids = [g.id for g in selected if g.type == "exclusion" and g.active]
        inclusion = [g for g in selected if g.type == "inclusion" and g.active]

        def forbidden(geojson: dict[str, Any], scope: str, drone_id: uuid.UUID | None = None) -> None:
            for zone, overlap in self.intersecting(geojson, ["nfz"]):
                conflicts.append(Conflict("nfz", scope, zone.id, zone.name, drone_id, overlap))
            for zone, overlap in self.intersecting(geojson, ["exclusion"], exclusion_ids):
                conflicts.append(Conflict("exclusion", scope, zone.id, zone.name, drone_id, overlap))

        if aoi is not None:
            forbidden(aoi, "aoi")
        for drone_id, route in routes.items():
            forbidden(route, "route", drone_id)
        for drone_id, point in launch_points.items():
            for zone in self.covering(point, ["nfz"]):
                conflicts.append(Conflict("nfz", "launch", zone.id, zone.name, drone_id, point))
            for zone in self.covering(point, ["exclusion"], exclusion_ids):
                conflicts.append(Conflict("exclusion", "launch", zone.id, zone.name, drone_id, point))

        if inclusion:
            inclusion_ids = [g.id for g in inclusion]
            anchor = inclusion[0]
            if aoi is not None and not self.covered_by_union(aoi, inclusion_ids):
                conflicts.append(Conflict("inclusion", "aoi", anchor.id, anchor.name))
            for drone_id, route in routes.items():
                if not self.covered_by_union(route, inclusion_ids):
                    conflicts.append(Conflict("inclusion", "route", anchor.id, anchor.name, drone_id))
            if altitude_m is not None:
                for zone in inclusion:
                    if zone.altitude_max is not None and zone.altitude_max < altitude_m:
                        conflicts.append(Conflict("altitude_ceiling", "aoi", zone.id, zone.name))
        return conflicts
