"""Local development seed data for Drone MatrX.

Run with:  python scripts/seed.py   (from backend/, inside the venv)

Creates one organization, one user per RBAC role, 8 realistic drones around
the Gandhinagar/Ahmedabad AgriTech pilot area, their payload + maintenance
records, 2 sample missions (one with a saved AOI), 2 inclusion geofences,
1 exclusion zone and 1 NFZ.

Sprint 3 additions (planned with the real CBBA planning service):
  * "Solar Farm Survey"          -- converges, passes validation (ready to arm)
  * "Lidar Corridor Inspection"  -- contains an infeasible lidar task (unassigned)
  * "Airport Approach Survey"    -- AOI crosses the airport NFZ (blocked)

Idempotent: safe to re-run, existing rows are left alone (matched by
slug/email/uin/name). Drone performance profiles are always re-applied.
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
from app.services.demo_fleet import BASE_LAT, BASE_LON, DRONE_SPECS, PERFORMANCE, reset_drone  # noqa: E402

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
            # Deterministic demo state: undo whatever a previous simulator run did.
            reset_drone(existing, spec, i, now)
            db.commit()
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
            max_speed=PERFORMANCE[spec["model"]][0],
            endurance_minutes=PERFORMANCE[spec["model"]][1],
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
        ("Warehouse Perimeter", "inclusion", BASE_LAT, BASE_LON, 0.003, 0, 120),
        ("South Field Boundary", "inclusion", BASE_LAT - 0.006, BASE_LON - 0.004, 0.004, 0, 90),
        ("Airport Approach NFZ", "nfz", BASE_LAT + 0.012, BASE_LON + 0.009, 0.007, 0, 500),
        ("Village Market Crowd Area", "exclusion", BASE_LAT + 0.004, BASE_LON - 0.012, 0.0015, 0, 120),
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


def _square(center_lat: float, center_lon: float, half: float) -> dict:
    return _polygon(center_lat, center_lon, half)


PLANNED_MISSIONS = [
    # name, AOI (lat, lon, half-size deg), template, required sensors, inspection points
    ("Solar Farm Survey", (BASE_LAT - 0.008, BASE_LON - 0.006, 0.002), "survey", ["camera"], []),
    (
        "Lidar Corridor Inspection",
        (BASE_LAT - 0.004, BASE_LON + 0.006, 0.0015),
        "inspection",
        ["camera"],
        [{"latitude": BASE_LAT - 0.004, "longitude": BASE_LON + 0.0065, "required_sensor": "lidar", "priority": 4,
          "hover_time_s": 90, "label": "Pylon 14 lidar scan"}],
    ),
    ("Airport Approach Survey", (BASE_LAT + 0.010, BASE_LON + 0.004, 0.002), "survey", ["camera"], []),
]


def seed_planned_missions(db, org: Organization, operator: User) -> None:
    """Drive the real wizard + CBBA + validation path for the demo missions."""
    from app.services.mission_planning_service import MissionPlanningService, PlanningError

    planning = MissionPlanningService(db)
    drone_ids = [str(d.id) for d in db.query(Drone).filter_by(organization_id=org.id).order_by(Drone.name).all()]
    for name, (lat, lon, half), template, sensors, points in PLANNED_MISSIONS:
        if db.query(Mission).filter_by(organization_id=org.id, name=name).one_or_none() is not None:
            continue
        mission = Mission(organization_id=org.id, name=name, mission_type="SURVEY", status="draft", created_by=operator.id)
        db.add(mission)
        db.commit()
        db.refresh(mission)
        try:
            planning.save_step(mission, 1, {"template": template, "name": name}, operator)
            planning.save_step(mission, 2, {"source": "drawn", "geometry": _square(lat, lon, half)}, operator)
            planning.save_step(mission, 3, {**mission.config["flight"], "altitude_m": 70, "overlap_pct": 60, "rth_battery_pct": 20}, operator)
            planning.save_step(mission, 4, {"required_sensors": sensors, "drone_ids": drone_ids}, operator)
            planning.save_step(mission, 5, {"min_battery_pct": 30, "max_tasks_per_drone": 25, "inspection_points": points}, operator)
            report = planning.validate(mission, operator)
            plan = planning.current_plan(mission)
            print(
                f"  - {name}: {plan.task_count if plan else 0} tasks, {plan.assigned_count if plan else 0} assigned, "
                f"converged={plan.converged if plan else False}, validation passed={report['passed']}"
                + (" (NFZ conflict)" if report["nfz_conflict"] else "")
            )
        except PlanningError as exc:
            print(f"  - {name}: planning skipped ({exc.code}: {exc.message})")


def main() -> None:
    db = SessionLocal()
    try:
        org = get_or_create_org(db)

        admin = get_or_create_user(db, org, "admin@dronematrx.com", "Lithika Saravanakumar", "ADMIN")
        operator = get_or_create_user(db, org, "operator@dronematrx.com", "Rudra Patel", "OPERATOR")
        get_or_create_user(db, org, "observer@dronematrx.com", "Meet Savlani", "OBSERVER")
        get_or_create_user(db, org, "analyst@dronematrx.com", "Animesh Kumar Aggarwal", "ANALYST")

        seed_drones(db, org)
        seed_missions(db, org, operator)
        seed_geofences(db, org)
        print("Planned missions (CBBA):")
        seed_planned_missions(db, org, operator)

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
