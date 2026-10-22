"""Geographic <-> simulation coordinate transform.

Leaflet speaks WGS84 latitude/longitude; Gazebo speaks metres in a local
Cartesian world frame. Lat/lon are never used directly as Gazebo X/Y.

Frame (documented in docs/sprint5/SIMULATION.md):

    origin   AOI centroid (lat0, lon0) at h0 = terrain elevation there (m, WGS84
             ellipsoid height as reported by the DEM; Gazebo z = 0 at the origin)
    axes     ENU -- +X east, +Y north, +Z up (Gazebo's default world frame and
             <spherical_coordinates><world_frame_orientation>ENU)
    units    metres, degrees

Forward:  (lat, lon, h) -> ECEF (WGS84) -> rotate into the tangent plane at
          the origin -> (x_east, y_north, z_up).
Inverse:  ENU -> ECEF -> geodetic (Bowring/Heikkinen closed form, then one
          Newton refinement), so forward(inverse(p)) round-trips to < 1 mm.

This is the same transform Gazebo applies for <spherical_coordinates> and
PX4 uses for its local NED frame (up to the NED/ENU axis swap), which is
what makes a pose in Gazebo land on the same spot on the Leaflet map.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# WGS84 ellipsoid
A = 6_378_137.0
F = 1 / 298.257223563
B = A * (1 - F)
E2 = F * (2 - F)
EP2 = E2 / (1 - E2)


def geodetic_to_ecef(lat_deg: float, lon_deg: float, h: float = 0.0) -> tuple[float, float, float]:
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    sl, cl = math.sin(lat), math.cos(lat)
    n = A / math.sqrt(1 - E2 * sl * sl)
    return ((n + h) * cl * math.cos(lon), (n + h) * cl * math.sin(lon), (n * (1 - E2) + h) * sl)


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    # Bowring's initial estimate, then refine to sub-millimetre.
    theta = math.atan2(z * A, p * B)
    lat = math.atan2(z + EP2 * B * math.sin(theta) ** 3, p - E2 * A * math.cos(theta) ** 3)
    for _ in range(3):
        sl = math.sin(lat)
        n = A / math.sqrt(1 - E2 * sl * sl)
        h = p / math.cos(lat) - n if abs(lat) < math.radians(89.9) else z / sl - n * (1 - E2)
        lat = math.atan2(z, p * (1 - E2 * n / (n + h)))
    sl = math.sin(lat)
    n = A / math.sqrt(1 - E2 * sl * sl)
    h = p / math.cos(lat) - n if abs(lat) < math.radians(89.9) else z / sl - n * (1 - E2)
    return math.degrees(lat), math.degrees(lon), h


@dataclass(frozen=True)
class EnuFrame:
    """Local east-north-up frame anchored at a geodetic origin."""

    lat0: float
    lon0: float
    h0: float = 0.0

    def __post_init__(self) -> None:
        if not -90 < self.lat0 < 90 or not -180 <= self.lon0 <= 180:
            raise ValueError(f"Invalid frame origin ({self.lat0}, {self.lon0})")

    @property
    def _origin_ecef(self) -> tuple[float, float, float]:
        return geodetic_to_ecef(self.lat0, self.lon0, self.h0)

    def _rot(self) -> tuple[float, float, float, float]:
        la, lo = math.radians(self.lat0), math.radians(self.lon0)
        return math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)

    def to_enu(self, lat: float, lon: float, h: float | None = None) -> tuple[float, float, float]:
        """(lat, lon[, h]) -> (x east, y north, z up) in metres. h defaults to h0."""
        x, y, z = geodetic_to_ecef(lat, lon, self.h0 if h is None else h)
        x0, y0, z0 = self._origin_ecef
        dx, dy, dz = x - x0, y - y0, z - z0
        sla, cla, slo, clo = self._rot()
        e = -slo * dx + clo * dy
        n = -sla * clo * dx - sla * slo * dy + cla * dz
        u = cla * clo * dx + cla * slo * dy + sla * dz
        return e, n, u

    def to_geodetic(self, e: float, n: float, u: float = 0.0) -> tuple[float, float, float]:
        """(x east, y north, z up) -> (lat, lon, h)."""
        sla, cla, slo, clo = self._rot()
        dx = -slo * e - sla * clo * n + cla * clo * u
        dy = clo * e - sla * slo * n + cla * slo * u
        dz = cla * n + sla * u
        x0, y0, z0 = self._origin_ecef
        lat, lon, h = ecef_to_geodetic(x0 + dx, y0 + dy, z0 + dz)
        if lon > 180:
            lon -= 360
        elif lon < -180:
            lon += 360
        return lat, lon, h

    def to_geodetic_np(self, e, n, u=0.0):
        """Vectorised ENU -> (lat, lon, h) for numpy arrays (terrain grids)."""
        import numpy as np

        e, n, u = np.asarray(e, float), np.asarray(n, float), np.asarray(u, float)
        sla, cla, slo, clo = self._rot()
        x0, y0, z0 = self._origin_ecef
        x = x0 - slo * e - sla * clo * n + cla * clo * u
        y = y0 + clo * e - sla * slo * n + cla * slo * u
        z = z0 + cla * n + sla * u
        lon = np.arctan2(y, x)
        p = np.hypot(x, y)
        lat = np.arctan2(z, p * (1 - E2))
        for _ in range(5):
            sl = np.sin(lat)
            nn = A / np.sqrt(1 - E2 * sl * sl)
            h = p / np.cos(lat) - nn
            lat = np.arctan2(z, p * (1 - E2 * nn / (nn + h)))
        sl = np.sin(lat)
        nn = A / np.sqrt(1 - E2 * sl * sl)
        h = p / np.cos(lat) - nn
        lon_deg = (np.degrees(lon) + 180.0) % 360.0 - 180.0
        return np.degrees(lat), lon_deg, h

    def to_enu_np(self, lat, lon, h=None):
        """Vectorised (lat, lon[, h]) -> (e, n, u)."""
        import numpy as np

        lat_r, lon_r = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
        h = np.full(np.shape(lat_r), self.h0) if h is None else np.asarray(h, float)
        sl, cl = np.sin(lat_r), np.cos(lat_r)
        nn = A / np.sqrt(1 - E2 * sl * sl)
        x = (nn + h) * cl * np.cos(lon_r)
        y = (nn + h) * cl * np.sin(lon_r)
        z = (nn * (1 - E2) + h) * sl
        x0, y0, z0 = self._origin_ecef
        dx, dy, dz = x - x0, y - y0, z - z0
        sla, cla, slo, clo = self._rot()
        return (-slo * dx + clo * dy, -sla * clo * dx - sla * slo * dy + cla * dz, cla * clo * dx + cla * slo * dy + sla * dz)

    def lonlat_to_xy(self, lon: float, lat: float) -> tuple[float, float]:
        """GeoJSON order helper: (lon, lat) -> (x, y) on the tangent plane at h0."""
        e, n, _ = self.to_enu(lat, lon)
        return e, n

    def xy_to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        lat, lon, _ = self.to_geodetic(x, y, 0.0)
        return lon, lat

    def as_dict(self) -> dict:
        return {
            "type": "ENU",
            "origin": {"latitude": self.lat0, "longitude": self.lon0, "height_m": self.h0},
            "axes": {"x": "east", "y": "north", "z": "up"},
            "units": "metres",
            "datum": "WGS84",
        }


def polygon_centroid_lonlat(ring: list[list[float]]) -> tuple[float, float]:
    """Area centroid of a lon/lat ring, computed in a provisional ENU frame
    (robust across the antimeridian, unlike averaging raw longitudes)."""
    lon_ref, lat_ref = ring[0][0], ring[0][1]
    frame = EnuFrame(lat_ref, lon_ref)
    pts = [frame.lonlat_to_xy(_unwrap(lon, lon_ref), lat) for lon, lat in ring]
    area = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(area) < 1e-9:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return frame.xy_to_lonlat(sum(xs) / len(xs), sum(ys) / len(ys))
    return frame.xy_to_lonlat(cx / (3 * area), cy / (3 * area))


def _unwrap(lon: float, ref: float) -> float:
    while lon - ref > 180:
        lon -= 360
    while lon - ref < -180:
        lon += 360
    return lon


def frame_for_polygon(geojson: dict, h0: float = 0.0) -> EnuFrame:
    lon, lat = polygon_centroid_lonlat(geojson["coordinates"][0])
    return EnuFrame(lat, lon, h0)
