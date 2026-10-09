"""The only place ORCA touches geography: WGS84 lat/lon <-> local metres.

Collision maths never runs on raw degrees (a degree of longitude is ~102 km
here, a degree of latitude ~111 km). Positions are projected onto a local
east/north tangent plane around the operations origin (Drone MatrX HQ by
default); errors are far below a centimetre over the few-km flight area.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.coordination.geometry import LocalProjection

HQ_LAT, HQ_LON = 23.2156, 72.6369


@dataclass(frozen=True)
class GeoFrame:
    origin_lat: float = HQ_LAT
    origin_lon: float = HQ_LON

    @property
    def _projection(self) -> LocalProjection:
        return LocalProjection(origin_lon=self.origin_lon, origin_lat=self.origin_lat)

    def to_local(self, lat: float, lon: float) -> tuple[float, float]:
        return self._projection.to_local(lon, lat)

    def to_geo(self, x: float, y: float) -> tuple[float, float]:
        lon, lat = self._projection.to_lonlat(x, y)
        return lat, lon

    def metres_per_degree(self, lat: float) -> tuple[float, float]:
        """(metres per degree latitude, metres per degree longitude) at `lat`."""
        x1, _ = self._projection.to_local(self.origin_lon + 1.0, self.origin_lat)
        _, y1 = self._projection.to_local(self.origin_lon, self.origin_lat + 1.0)
        return y1, x1
