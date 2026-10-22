"""Simulation drones and launch positions.

Simulation drones are ordinary rows of `drones` with simulated = true
(SIM-01 ... SIM-16 per organization). Their ids flow unchanged through the
mission wizard (step 4), CBBA, the SITL manager (DM_SIM_UIN), Gazebo
(model x500_<index>) and the Leaflet map.

Launch positions are computed from the AOI before planning, written to
drone.location, and therefore become the CBBA agents' start positions --
spawn poses come from the same planning context that assigned the tasks.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from shapely.geometry import Point, Polygon
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.geo import geography_to_geojson, geojson_to_geography, point_to_geojson
from app.models import Drone, Organization, Payload
from app.services.simulation.geo import frame_for_polygon

SIM_FLEET_SIZE = 16
SIM_PAYLOADS = ("camera", "thermal", "multispectral", "sprayer")
LAUNCH_INSET_M = 15.0
LAUNCH_SPACING_M = 6.0
PER_ROW = 4


class LaunchAreaError(Exception):
    pass


def sim_uin(org: Organization, n: int) -> str:
    return f"SIM-{str(org.id)[:8]}-{n:02d}"


def ensure_sim_fleet(db: Session, org: Organization) -> list[Drone]:
    """Idempotently provision the organization's simulation drones."""
    existing = {d.name: d for d in db.execute(select(Drone).where(Drone.organization_id == org.id, Drone.simulated.is_(True))).scalars()}
    created = False
    for n in range(1, SIM_FLEET_SIZE + 1):
        name = f"SIM-{n:02d}"
        if name in existing:
            continue
        drone = Drone(
            organization_id=org.id,
            name=name,
            uin=sim_uin(org, n),
            vehicle_type="quadrotor",
            model="PX4 x500 (SITL)",
            firmware="PX4 v1.15 SITL",
            status="offline",
            battery=100.0,
            gps_status="NO_FIX",
            flight_mode="STANDBY",
            max_speed=12.0,
            endurance_minutes=25.0,
            simulated=True,
        )
        db.add(drone)
        db.flush()
        for ptype in SIM_PAYLOADS:
            db.add(Payload(drone_id=drone.id, type=ptype, name=f"Sim {ptype}", status="calibrated", calibrated_at=datetime.now(timezone.utc)))
        existing[name] = drone
        created = True
    if created:
        db.commit()
    return sorted(existing.values(), key=lambda d: d.name)


def launch_positions(aoi: dict, count: int, blocked: list[Polygon] | None = None) -> list[tuple[float, float]]:
    """`count` (lon, lat) launch points inside the AOI, inset from its edge,
    on a LAUNCH_SPACING_M grid anchored at the AOI's south-west interior,
    avoiding `blocked` (local-frame) polygons such as NFZs."""
    if count < 1:
        raise LaunchAreaError("At least one drone is required")
    frame = frame_for_polygon(aoi)
    local = Polygon([frame.lonlat_to_xy(lon, lat) for lon, lat in aoi["coordinates"][0]])
    area = local.buffer(-LAUNCH_INSET_M)
    if area.is_empty:
        area = local.buffer(-2.0)
    if area.is_empty:
        raise LaunchAreaError("AOI is too small to place launch positions")
    minx, miny, _, _ = area.bounds
    anchor = area.exterior.interpolate(area.exterior.project(Point(minx, miny))) if hasattr(area, "exterior") else area.representative_point()
    blocked = blocked or []
    out: list[tuple[float, float]] = []
    # Walk an expanding grid around the anchor; keep cells inside the launch area.
    radius = 0
    seen: set[tuple[int, int]] = set()
    while len(out) < count and radius < 200:
        for j in range(0, radius + 1):
            for i in range(0, radius + 1):
                if (i, j) in seen or max(i, j) != radius:
                    continue
                seen.add((i, j))
                x, y = anchor.x + i * LAUNCH_SPACING_M, anchor.y + j * LAUNCH_SPACING_M
                p = Point(x, y)
                if area.contains(p) and not any(b.contains(p) for b in blocked):
                    out.append((x, y))
                    if len(out) == count:
                        break
            if len(out) == count:
                break
        radius += 1
    if len(out) < count:
        raise LaunchAreaError(f"Only {len(out)} valid launch positions fit in the AOI for {count} drones")
    out.sort(key=lambda p: (round(p[1], 3), round(p[0], 3)))
    return [frame.xy_to_lonlat(x, y) for x, y in out]


def stage_drones(db: Session, drones: list[Drone], positions: list[tuple[float, float]]) -> None:
    """Put simulation drones on their launch positions (pre-spawn)."""
    for drone, (lon, lat) in zip(drones, positions):
        drone.location = geojson_to_geography(point_to_geojson(lon, lat))
        drone.battery = 100.0
        drone.altitude = 0.0
        drone.speed = 0.0
        drone.armed = False
        drone.status = "offline"
        drone.gps_status = "NO_FIX"
        drone.flight_mode = "SIM_STAGED"
    db.commit()


def drone_lonlat(drone: Drone) -> tuple[float, float] | None:
    gj = geography_to_geojson(drone.location)
    return (gj["coordinates"][0], gj["coordinates"][1]) if gj else None


def min_separation_m(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return math.inf
    from app.services.simulation.geo import EnuFrame

    f = EnuFrame(points[0][1], points[0][0])
    xy = [f.lonlat_to_xy(lon, lat) for lon, lat in points]
    return min(math.dist(a, b) for i, a in enumerate(xy) for b in xy[i + 1 :])
