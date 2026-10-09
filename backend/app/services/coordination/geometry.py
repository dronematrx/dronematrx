"""Local tangent-plane projection (equirectangular about an origin).

Accurate to well under 0.1% over the few-kilometre extents of a mission,
which is all the allocator needs. Authoritative spatial checks (NFZ,
geofence containment) still run in PostGIS on geography types.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, Polygon, shape
from shapely.ops import transform

EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class LocalProjection:
    origin_lon: float
    origin_lat: float

    @property
    def _kx(self) -> float:
        return math.radians(1) * EARTH_RADIUS_M * math.cos(math.radians(self.origin_lat))

    @property
    def _ky(self) -> float:
        return math.radians(1) * EARTH_RADIUS_M

    def to_local(self, lon: float, lat: float) -> tuple[float, float]:
        return ((lon - self.origin_lon) * self._kx, (lat - self.origin_lat) * self._ky)

    def to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        return (self.origin_lon + x / self._kx, self.origin_lat + y / self._ky)

    def polygon_to_local(self, geojson: dict) -> Polygon:
        geom = shape(geojson)
        return transform(lambda xs, ys, zs=None: _apply(self.to_local, xs, ys), geom)

    def line_to_lonlat(self, line: LineString) -> list[list[float]]:
        return [list(self.to_lonlat(x, y)) for x, y in line.coords]

    @classmethod
    def for_polygon(cls, geojson: dict) -> "LocalProjection":
        centroid = shape(geojson).centroid
        return cls(origin_lon=centroid.x, origin_lat=centroid.y)


def _apply(fn, xs, ys):
    # shapely.ops.transform passes coordinate sequences (tuples) for each axis.
    pairs = [fn(x, y) for x, y in zip(xs, ys)]
    return tuple(p[0] for p in pairs), tuple(p[1] for p in pairs)
