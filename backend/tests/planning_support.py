"""Fixtures/helpers for Sprint 3 mission planning integration tests.

Geometry is built around the Gandhinagar HQ (23.2156 N, 72.6369 E). The
fleet launches from just west of the default AOI; every test that needs an
NFZ creates it explicitly so cases stay independent.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.geo import geojson_to_geography, point_to_geojson
from app.models import Drone, Geofence, Payload
from tests.conftest import auth_headers

BASE_LAT, BASE_LON = 23.2156, 72.6369


def square(center_lat: float, center_lon: float, half_deg: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [center_lon - half_deg, center_lat - half_deg],
            [center_lon + half_deg, center_lat - half_deg],
            [center_lon + half_deg, center_lat + half_deg],
            [center_lon - half_deg, center_lat + half_deg],
            [center_lon - half_deg, center_lat - half_deg],
        ]],
    }


def rect(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[[min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat]]],
    }


# ~550 m x 550 m AOI a little east of HQ.
AOI = square(BASE_LAT, BASE_LON + 0.006, 0.0025)

# (name, lat offset, lon offset, battery, payload types, status)
FLEET_SPEC = [
    ("QA-01", 0.0010, -0.0010, 92, ("camera",), "online"),
    ("QA-02", -0.0010, -0.0012, 88, ("camera", "thermal"), "active"),
    ("QA-03", 0.0020, 0.0000, 81, ("camera",), "online"),
    ("QA-04", -0.0020, 0.0005, 76, ("camera", "multispectral"), "active"),
    ("QA-05", 0.0000, -0.0020, 95, ("thermal",), "online"),
    ("QA-06", 0.0030, -0.0005, 69, ("camera",), "active"),
    ("QA-07", -0.0030, -0.0008, 84, ("camera",), "online"),
    ("QA-08", 0.0005, 0.0010, 73, ("camera", "thermal"), "warning"),
    ("QA-09", -0.0005, 0.0012, 90, ("camera",), "online"),  # not in the default roster: used for "add drone"
]


def make_drone(db, org, name, lat, lon, battery=90.0, payload_types=("camera",), status="online", **overrides) -> Drone:
    drone = Drone(
        organization_id=org.id,
        name=name,
        model="M30",
        firmware="PX4 v1.14",
        vehicle_type="quadrotor",
        status=status,
        battery=battery,
        location=geojson_to_geography(point_to_geojson(lon, lat)),
        altitude=0,
        gps_status="3D_FIX",
        max_speed=15,
        endurance_minutes=30,
        last_telemetry_at=datetime.now(timezone.utc),
        **overrides,
    )
    db.add(drone)
    db.flush()
    for ptype in payload_types:
        db.add(Payload(drone_id=drone.id, type=ptype, name=f"{name} {ptype}", status="calibrated", calibrated_at=datetime.now(timezone.utc)))
    db.commit()
    db.refresh(drone)
    return drone


@pytest.fixture()
def fleet(db, organization) -> list[Drone]:
    return [
        make_drone(db, organization, name, BASE_LAT + dlat, BASE_LON + dlon, battery, caps, status)
        for name, dlat, dlon, battery, caps, status in FLEET_SPEC
    ]


def make_zone(db, org, name: str, zone_type: str, geometry: dict, altitude_max: float = 120) -> Geofence:
    zone = Geofence(organization_id=org.id, name=name, type=zone_type, geometry=geojson_to_geography(geometry), altitude_max=altitude_max)
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone


def headers_for(client, user) -> dict:
    return auth_headers(client, user.email)


def create_mission(client, headers, name: str = "QA Mission") -> dict:
    resp = client.post("/missions", headers=headers, json={"name": name, "mission_type": "SURVEY"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def save_step(client, headers, mission_id: str, step: int, data: dict, expect: int = 200) -> dict:
    resp = client.put(f"/missions/{mission_id}/wizard/steps/{step}", headers=headers, json={"data": data})
    assert resp.status_code == expect, resp.text
    return resp.json()


def default_steps(fleet: list[Drone], aoi: dict = AOI, *, drones: int = 8, flight: dict | None = None,
                  sensors: dict | None = None, constraints: dict | None = None) -> dict[int, dict]:
    return {
        1: {"template": "survey", "name": "QA Survey"},
        2: {"source": "drawn", "geometry": aoi},
        3: {"altitude_m": 60, "speed_mps": 10, "overlap_pct": 60, "pattern": "lawnmower", "rth_battery_pct": 20, "priority": 2, **(flight or {})},
        4: {"required_sensors": ["camera"], "drone_ids": [str(d.id) for d in fleet[:drones]], **(sensors or {})},
        5: {"min_battery_pct": 30, "max_tasks_per_drone": 15, **(constraints or {})},
    }


def run_wizard(client, headers, fleet, **kwargs) -> str:
    """Create a mission and complete wizard steps 1-5. Returns the mission id."""
    mission = create_mission(client, headers)
    for step, data in default_steps(fleet, **kwargs).items():
        save_step(client, headers, mission["id"], step, data)
    return mission["id"]
