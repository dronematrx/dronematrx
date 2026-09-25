"""Local development seed data for Drone MatrX.

Run with:  python scripts/seed.py   (from backend/, inside the venv)

Creates one organization, one user per RBAC role, 8 realistic drones around
the Gandhinagar/Ahmedabad AgriTech pilot area, their payload + maintenance
records, 2 sample missions (one with a saved AOI), 2 geofences and 1 NFZ.
Idempotent: safe to re-run, existing rows are left alone (matched by
slug/email/uin).
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.geo import geojson_to_geography, point_to_geojson  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Drone,
    Geofence,
    MaintenanceRecord,
    Mission,
    Organization,
    Payload,
    User,
)

SEED_PASSWORD = os.environ.get("SEED_PASSWORD", "DroneMatrx@2026")
ORG_SLUG = "drone-matrx-hq"
BASE_LAT, BASE_LON = 23.2156, 72.6369  # Gandhinagar, Gujarat -- project HQ / PoC site

now = datetime.now(timezone.utc)


def get_or_create_org(db) -> Organization:
    org = db.query(Organization).filter_by(slug=ORG_SLUG).one_or_none()
    if org:
        return org
    org = Organization(name="Drone MatrX HQ", slug=ORG_SLUG, subscription_tier="open_core")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def get_or_create_user(db, org: Organization, email: str, full_name: str, role: str) -> User:
    user = db.query(User).filter_by(email=email).one_or_none()
    if user:
        return user
    user = User(
        organization_id=org.id,
        email=email,
        password_hash=hash_password(SEED_PASSWORD),
        full_name=full_name,
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


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

MAINTENANCE_LOG = [
    ("routine", "A. Mehta", "40-hour rotor and ESC inspection, all nominal."),
    ("repair", "R. Patel", "Replaced cracked front-left propeller after hard landing."),
    ("upgrade", "U. Sharma", "Flashed PX4 v1.14.2, recalibrated compass and accelerometer."),
    ("inspection", "A. Mehta", "Pre-season airframe and payload mount inspection."),
]


def seed_drones(db, org: Organization) -> list[Drone]:
    drones = []
    for i, spec in enumerate(DRONE_SPECS):
        existing = db.query(Drone).filter_by(name=spec["name"], organization_id=org.id).one_or_none()
        if existing:
            drones.append(existing)
            continue

        location = None
        if spec["lat"] is not None and spec["lon"] is not None:
            location = geojson_to_geography(point_to_geojson(spec["lon"], spec["lat"]))

        drone = Drone(
            organization_id=org.id,
            name=spec["name"],
            uin=f"DRN-01-{210900 + i}",
            vehicle_type=spec["vehicle_type"],
            model=spec["model"],
            firmware=spec["firmware"],
            status=spec["status"],
            battery=spec["battery"],
            location=location,
            altitude=spec["alt"],
            heading=spec["heading"],
            speed=0,
            gps_status=spec["gps_status"],
            flight_mode=spec["flight_mode"],
            armed=spec["armed"],
            link_quality=spec["link_quality"],
            total_flight_hours=round(20 + i * 13.5, 1),
            battery_cycles=40 + i * 7,
            last_telemetry_at=now - timedelta(minutes=i),
            active=True,
        )
        db.add(drone)
        db.commit()
        db.refresh(drone)

        for ptype, pname, pstatus in spec["payloads"]:
            db.add(Payload(
                drone_id=drone.id,
                type=ptype,
                name=pname,
                status=pstatus,
                calibrated_at=now - timedelta(days=5 + i) if pstatus == "calibrated" else None,
            ))

        category, technician, notes = MAINTENANCE_LOG[i % len(MAINTENANCE_LOG)]
        db.add(MaintenanceRecord(
            drone_id=drone.id,
            service_date=now - timedelta(days=10 + i * 4),
            category=category,
            technician=technician,
            notes=notes,
            flight_hours_at_service=max(0.0, round(20 + i * 13.5 - 5, 1)),
            battery_cycles_at_service=max(0, 40 + i * 7 - 5),
        ))
        db.commit()
        drones.append(drone)
    return drones


def _polygon(center_lat: float, center_lon: float, half: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [center_lon - half, center_lat - half],
            [center_lon + half, center_lat - half],
            [center_lon + half, center_lat + half],
            [center_lon - half, center_lat + half],
            [center_lon - half, center_lat - half],
        ]],
    }


def seed_missions(db, org: Organization, commander) -> None:
    if db.query(Mission).filter_by(organization_id=org.id, name="Yumen Line 1 Recon").one_or_none() is None:
        db.add(Mission(
            organization_id=org.id,
            name="Yumen Line 1 Recon",
            mission_type="SURVEY",
            status="active",
            aoi_geometry=geojson_to_geography(_polygon(BASE_LAT, BASE_LON, 0.006)),
            start_time=now - timedelta(hours=1),
            created_by=commander.id,
        ))
    if db.query(Mission).filter_by(organization_id=org.id, name="Nilgiri Hills Green Zone").one_or_none() is None:
        db.add(Mission(
            organization_id=org.id,
            name="Nilgiri Hills Green Zone",
            mission_type="AGRI",
            status="draft",
            created_by=commander.id,
        ))
    db.commit()


def seed_geofences(db, org: Organization) -> None:
    specs = [
        ("Warehouse Perimeter", "geofence", BASE_LAT, BASE_LON, 0.003, 0, 120),
        ("South Field Boundary", "geofence", BASE_LAT - 0.006, BASE_LON - 0.004, 0.004, 0, 90),
        ("Airport Approach NFZ", "nfz", BASE_LAT + 0.012, BASE_LON + 0.009, 0.007, 0, 500),
    ]
    for name, gtype, lat, lon, half, alt_min, alt_max in specs:
        if db.query(Geofence).filter_by(organization_id=org.id, name=name).one_or_none() is not None:
            continue
        db.add(Geofence(
            organization_id=org.id,
            name=name,
            type=gtype,
            geometry=geojson_to_geography(_polygon(lat, lon, half)),
            altitude_min=alt_min,
            altitude_max=alt_max,
            active=True,
        ))
    db.commit()


def main() -> None:
    db = SessionLocal()
    try:
        org = get_or_create_org(db)

        admin = get_or_create_user(db, org, "admin@dronematrx.com", "Lithika Saravanakumar", "ADMIN")
        operator = get_or_create_user(db, org, "operator@dronematrx.com", "Rudra Patel", "OPERATOR")
        get_or_create_user(db, org, "observer@dronematrx.com", "Utkarsh Sharma", "OBSERVER")
        get_or_create_user(db, org, "analyst@dronematrx.com", "Animesh Kumar Aggarwal", "ANALYST")

        seed_drones(db, org)
        seed_missions(db, org, operator)
        seed_geofences(db, org)

        print(f"Seed complete for organization '{org.name}' ({org.id}).")
        print("Users (all share the SEED_PASSWORD value; default DroneMatrx@2026):")
        for u in db.query(User).filter_by(organization_id=org.id).all():
            print(f"  - {u.email:28s} role={u.role}")
        print(f"Drones: {db.query(Drone).filter_by(organization_id=org.id).count()}")
        print(f"Missions: {db.query(Mission).filter_by(organization_id=org.id).count()}")
        print(f"Geofences: {db.query(Geofence).filter_by(organization_id=org.id).count()}")
        _ = admin
    finally:
        db.close()


if __name__ == "__main__":
    main()
