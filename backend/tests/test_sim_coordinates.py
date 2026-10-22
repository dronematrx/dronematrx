"""Sprint 5: geographic <-> Gazebo coordinate transform (no DB, no simulator)."""
import math

import pytest

from app.services.simulation.geo import EnuFrame, ecef_to_geodetic, frame_for_polygon, geodetic_to_ecef, polygon_centroid_lonlat

# (name, lat, lon) -- several continents, both hemispheres, near a pole, the
# equator, a UTM zone boundary (72 E: zones 42/43) and the antimeridian.
LOCATIONS = [
    ("gandhinagar", 23.2156, 72.6369),
    ("utm_boundary_72E", 23.0000, 72.0000),
    ("london", 51.5007, -0.1246),
    ("sydney", -33.8568, 151.2153),
    ("quito_equator", -0.1807, -78.4678),
    ("reykjavik", 64.1466, -21.9426),
    ("ushuaia", -54.8019, -68.3030),
    ("fiji_antimeridian", -16.8, 179.999),
    ("svalbard", 78.2232, 15.6267),
]

# Geodesic distance reference (Vincenty inverse on WGS84).


def vincenty(lat1, lon1, lat2, lon2):
    a, f = 6378137.0, 1 / 298.257223563
    b = a * (1 - f)
    L = math.radians(lon2 - lon1)
    U1, U2 = math.atan((1 - f) * math.tan(math.radians(lat1))), math.atan((1 - f) * math.tan(math.radians(lat2)))
    sU1, cU1, sU2, cU2 = math.sin(U1), math.cos(U1), math.sin(U2), math.cos(U2)
    lam = L
    for _ in range(200):
        sl, cl = math.sin(lam), math.cos(lam)
        ss = math.hypot(cU2 * sl, cU1 * sU2 - sU1 * cU2 * cl)
        if ss == 0:
            return 0.0
        cs = sU1 * sU2 + cU1 * cU2 * cl
        sigma = math.atan2(ss, cs)
        sa = cU1 * cU2 * sl / ss
        c2a = 1 - sa * sa
        c2sm = cs - 2 * sU1 * sU2 / c2a if c2a else 0.0
        C = f / 16 * c2a * (4 + f * (4 - 3 * c2a))
        prev, lam = lam, L + (1 - C) * f * sa * (sigma + C * ss * (c2sm + C * cs * (-1 + 2 * c2sm * c2sm)))
        if abs(lam - prev) < 1e-12:
            break
    u2 = c2a * (a * a - b * b) / (b * b)
    A_ = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    B_ = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    ds = B_ * ss * (c2sm + B_ / 4 * (cs * (-1 + 2 * c2sm * c2sm) - B_ / 6 * c2sm * (-3 + 4 * ss * ss) * (-3 + 4 * c2sm * c2sm)))
    return b * A_ * (sigma - ds)


@pytest.mark.parametrize("name,lat,lon", LOCATIONS)
def test_ecef_round_trip(name, lat, lon):
    for h in (-50.0, 0.0, 120.0, 4000.0):
        lat2, lon2, h2 = ecef_to_geodetic(*geodetic_to_ecef(lat, lon, h))
        assert abs(lat2 - lat) < 1e-10 and abs(((lon2 - lon + 180) % 360) - 180) < 1e-10 and abs(h2 - h) < 1e-4


@pytest.mark.parametrize("name,lat,lon", LOCATIONS)
def test_enu_round_trip_and_origin(name, lat, lon):
    frame = EnuFrame(lat, lon, 50.0)
    assert all(abs(v) < 1e-6 for v in frame.to_enu(lat, lon, 50.0)), "origin must map to (0,0,0)"
    for e, n, u in [(0, 0, 0), (500, 0, 0), (0, -500, 10), (-731.2, 412.9, 60), (2500, 2500, 120)]:
        lat2, lon2, h2 = frame.to_geodetic(e, n, u)
        e2, n2, u2 = frame.to_enu(lat2, lon2, h2)
        # Round trip error: < 1 mm.
        assert math.dist((e, n, u), (e2, n2, u2)) < 1e-3, (name, e, n, u, e2, n2, u2)


@pytest.mark.parametrize("name,lat,lon", LOCATIONS)
def test_enu_axes_and_scale(name, lat, lon):
    frame = EnuFrame(lat, lon, 0.0)
    # 1 km due north / due east must land ~1 km away in the right direction.
    lat_n, lon_n, _ = frame.to_geodetic(0, 1000, 0)
    assert lat_n > lat and abs(vincenty(lat, lon, lat_n, lon_n) - 1000) < 0.2
    lat_e, lon_e, _ = frame.to_geodetic(1000, 0, 0)
    d_lon = ((lon_e - lon + 180) % 360) - 180
    assert d_lon > 0 and abs(vincenty(lat, lon, lat_e, lon_e) - 1000) < 0.2
    # Horizontal ENU distance of a geodetic point matches the geodesic within 0.05 %.
    lat_p, lon_p, _ = frame.to_geodetic(-640.0, 870.0, 0)
    e, n, _ = frame.to_enu(lat_p, lon_p, 0.0)
    assert abs(math.hypot(e, n) - vincenty(lat, lon, lat_p, lon_p)) / math.hypot(e, n) < 5e-4


def test_antimeridian_crossing_frame():
    frame = EnuFrame(-16.8, 179.999)
    lat, lon, _ = frame.to_geodetic(400, 0, 0)  # crosses to the western hemisphere
    assert -180 <= lon < -179.99
    e, n, _ = frame.to_enu(lat, lon, 0.0)
    assert abs(e - 400) < 1e-3 and abs(n) < 1e-3


def test_polygon_centroid_across_antimeridian():
    ring = [[179.995, -16.80], [-179.995, -16.80], [-179.995, -16.79], [179.995, -16.79], [179.995, -16.80]]
    lon, lat = polygon_centroid_lonlat(ring)
    assert abs(abs(lon) - 180.0) < 1e-6 and abs(lat + 16.795) < 1e-6


def test_frame_for_polygon_origin_is_centroid():
    square = {"type": "Polygon", "coordinates": [[[72.630, 23.210], [72.640, 23.210], [72.640, 23.220], [72.630, 23.220], [72.630, 23.210]]]}
    frame = frame_for_polygon(square, h0=74.0)
    assert abs(frame.lon0 - 72.635) < 1e-6 and abs(frame.lat0 - 23.215) < 1e-5 and frame.h0 == 74.0
    # Corners are symmetric about the origin.
    xs = [frame.lonlat_to_xy(lon, lat) for lon, lat in square["coordinates"][0][:4]]
    assert abs(sum(x for x, _ in xs)) < 0.5 and abs(sum(y for _, y in xs)) < 0.5


def test_invalid_origin_rejected():
    with pytest.raises(ValueError):
        EnuFrame(95.0, 10.0)
