"""The deterministic demo fleet (seed specs) and how to reset it.

Shared by scripts/seed.py and the ADMIN "reset simulated fleet" operation
(POST /orca/fleet/reset), so tests and demos can always start from the same
fleet state no matter how long the simulator has been running.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.geo import geojson_to_geography, point_to_geojson
from app.models.drone import Drone

BASE_LAT, BASE_LON = 23.2156, 72.6369  # Gandhinagar, Gujarat -- project HQ / PoC site

DRONE_SPECS = [
    dict(name="DMX-001", model="M30", firmware="PX4 v1.14.2", vehicle_type="quadrotor",
         status="online", battery=87, lat=BASE_LAT + 0.0021, lon=BASE_LON + 0.0032, alt=42, heading=118,
         flight_mode="LOITER", gps_status="3D_FIX", armed=False, link_quality=94,
         payloads=[("camera", "M30 Wide Camera", "calibrated")]),
    dict(name="DMX-002", model="M30T", firmware="PX4 v1.14.2", vehicle_type="quadrotor",
         status="active", battery=72, lat=BASE_LAT - 0.0015, lon=BASE_LON + 0.0011, alt=55, heading=260,
         flight_mode="MISSION", gps_status="3D_FIX", armed=True, link_quality=88,
         payloads=[("thermal", "M30T Thermal", "calibrated"), ("camera", "M30T Zoom Camera", "calibrated")]),
    dict(name="DMX-003", model="Matrice 350", firmware="PX4 v1.13.3", vehicle_type="hexarotor",
         status="warning", battery=34, lat=BASE_LAT + 0.0008, lon=BASE_LON - 0.0027, alt=38, heading=15,
         flight_mode="HOLD", gps_status="2D_FIX", armed=True, link_quality=51,
         payloads=[("multispectral", "RedEdge-MX", "due")]),
    dict(name="DMX-004", model="ArduCopter X8", firmware="ArduPilot 4.5.1", vehicle_type="octorotor",
         status="offline", battery=0, lat=None, lon=None, alt=0, heading=0,
         flight_mode="STANDBY", gps_status="NO_FIX", armed=False, link_quality=0,
         payloads=[("lidar", "Livox Avia", "uncalibrated")]),
    dict(name="DMX-005", model="M30", firmware="PX4 v1.14.2", vehicle_type="quadrotor",
         status="online", battery=95, lat=BASE_LAT + 0.0044, lon=BASE_LON - 0.0009, alt=0, heading=200,
         flight_mode="STANDBY", gps_status="3D_FIX", armed=False, link_quality=97,
         payloads=[("camera", "M30 Wide Camera", "calibrated")]),
    dict(name="DMX-006", model="Agras T40", firmware="ArduPilot 4.4.4", vehicle_type="octorotor",
         status="active", battery=61, lat=BASE_LAT - 0.0032, lon=BASE_LON - 0.0018, alt=6, heading=88,
         flight_mode="MISSION", gps_status="3D_FIX", armed=True, link_quality=79,
         payloads=[("sprayer", "T40 Spray Tank", "calibrated")]),
    dict(name="DMX-007", model="Matrice 350", firmware="PX4 v1.13.3", vehicle_type="hexarotor",
         status="critical", battery=11, lat=BASE_LAT + 0.0002, lon=BASE_LON + 0.0055, alt=61, heading=330,
         flight_mode="RTL", gps_status="3D_FIX", armed=True, link_quality=42,
         payloads=[("camera", "H20T Camera", "fault")]),
    dict(name="DMX-008", model="M30T", firmware="PX4 v1.14.0", vehicle_type="quadrotor",
         status="maintenance", battery=100, lat=BASE_LAT - 0.0005, lon=BASE_LON + 0.0041, alt=0, heading=0,
         flight_mode="STANDBY", gps_status="NO_FIX", armed=False, link_quality=0,
         payloads=[("thermal", "M30T Thermal", "uncalibrated")]),
]

# Airframe performance used by CBBA feasibility: (max speed m/s, endurance minutes).
PERFORMANCE = {
    "M30": (15.0, 38.0),
    "M30T": (15.0, 36.0),
    "Matrice 350": (17.0, 45.0),
    "ArduCopter X8": (12.0, 28.0),
    "Agras T40": (10.0, 18.0),
}



def reset_drone(drone: Drone, spec: dict, index: int, now: datetime | None = None) -> None:
    """Put an existing drone back to its seeded state (undo simulator/demo drift)."""
    now = now or datetime.now(timezone.utc)
    drone.max_speed, drone.endurance_minutes = PERFORMANCE[spec["model"]]
    drone.status = spec["status"]
    drone.battery = spec["battery"]
    drone.gps_status = spec["gps_status"]
    drone.flight_mode = spec["flight_mode"]
    drone.armed = spec["armed"]
    drone.link_quality = spec["link_quality"]
    drone.altitude = spec["alt"]
    drone.heading = spec["heading"]
    drone.speed = 0
    drone.active = True
    drone.location = geojson_to_geography(point_to_geojson(spec["lon"], spec["lat"])) if spec["lat"] is not None else None
    drone.last_telemetry_at = now - timedelta(minutes=index)


def reset_fleet(db, organization_id) -> int:
    """Reset every seeded drone of an organization. Returns how many were reset."""
    count = 0
    for index, spec in enumerate(DRONE_SPECS):
        drone = db.query(Drone).filter_by(name=spec["name"], organization_id=organization_id).one_or_none()
        if drone is not None:
            reset_drone(drone, spec, index)
            count += 1
    db.commit()
    return count
