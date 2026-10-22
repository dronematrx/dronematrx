"""CBBA bundle -> PX4 mission items.

Every task in a drone's bundle is expanded to its full flown geometry
(a coverage lane = 2 points, a search cell = its creeping-line sweep, an
inspection target / waypoint = 1 point with optional hover), oriented the
way CBBA chose. The mission is:

    0      NAV_TAKEOFF at the spawn point, flight altitude (relative to home)
    1      DO_CHANGE_SPEED (ground speed = flight speed)
    2..k   NAV_WAYPOINT for every task vertex
    k+1    NAV_RETURN_TO_LAUNCH (rth_on_complete) or NAV_LAND

`seq_map[seq]` says which task a waypoint belongs to and whether it is the
task's last vertex: a task is completed only when PX4 reports
MISSION_ITEM_REACHED for that exit waypoint -- never because it was planned.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_NAV_RETURN_TO_LAUNCH = 20
MAV_CMD_NAV_LAND = 21
MAV_CMD_NAV_TAKEOFF = 22
MAV_CMD_DO_CHANGE_SPEED = 178
MAV_FRAME_MISSION = 2  # non-positional items (DO_*, RTL) must use the MISSION frame for PX4
ACCEPTANCE_RADIUS_M = 3.0


@dataclass
class TaskGeometry:
    key: str
    task_type: str
    coords: list[tuple[float, float]]  # (lon, lat)
    hover_s: float = 0.0


@dataclass
class BuiltMission:
    items: list[dict]
    seq_map: dict[int, dict] = field(default_factory=dict)
    planned_route: list[list[float]] = field(default_factory=list)  # [lon, lat] incl. launch
    task_keys: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"items": self.items, "seq_map": {str(k): v for k, v in self.seq_map.items()}, "planned_route": self.planned_route, "task_keys": self.task_keys}


def build_mission(
    start: tuple[float, float],
    path: list[tuple[str, int]],  # (task_key, orientation) in CBBA order
    geometries: dict[str, TaskGeometry],
    altitude_m: float,
    speed_mps: float,
    rth_on_complete: bool = True,
    stop_and_turn: bool = False,
    airborne: bool = False,
) -> BuiltMission:
    """`airborne=True` builds a replacement mission for a drone already in the
    air (in-flight reallocation): no takeoff item."""
    items: list[dict] = []
    seq_map: dict[int, dict] = {}
    route: list[list[float]] = [[start[0], start[1]]]
    if not airborne:
        items.append({"command": MAV_CMD_NAV_TAKEOFF, "lat": start[1], "lon": start[0], "alt": altitude_m, "param1": 0})
        seq_map[0] = {"role": "takeoff"}
    items.append({"command": MAV_CMD_DO_CHANGE_SPEED, "param1": 1, "param2": speed_mps, "param3": -1, "lat": 0, "lon": 0, "alt": 0, "frame": MAV_FRAME_MISSION})
    keys: list[str] = []
    for key, orientation in path:
        geom = geometries[key]
        coords = list(geom.coords)
        if orientation == 1:
            coords.reverse()
        keys.append(key)
        for n, (lon, lat) in enumerate(coords):
            last = n == len(coords) - 1
            hold = geom.hover_s if (last and geom.hover_s) else (1.0 if stop_and_turn else 0.0)
            seq = len(items)
            items.append({"command": MAV_CMD_NAV_WAYPOINT, "lat": lat, "lon": lon, "alt": altitude_m, "param1": hold, "param2": ACCEPTANCE_RADIUS_M})
            seq_map[seq] = {"role": "exit" if last else ("entry" if n == 0 else "via"), "task_key": key}
            route.append([lon, lat])
    seq = len(items)
    if rth_on_complete:
        items.append({"command": MAV_CMD_NAV_RETURN_TO_LAUNCH, "lat": 0, "lon": 0, "alt": 0, "frame": MAV_FRAME_MISSION})
        seq_map[seq] = {"role": "rtl"}
        route.append([start[0], start[1]])
    else:
        last = route[-1]
        items.append({"command": MAV_CMD_NAV_LAND, "lat": last[1], "lon": last[0], "alt": 0})
        seq_map[seq] = {"role": "land"}
    return BuiltMission(items=items, seq_map=seq_map, planned_route=route, task_keys=keys)


def task_geometries_from_rows(rows) -> dict[str, TaskGeometry]:
    """MissionTask ORM rows (geometry as GeoJSON via geography_to_geojson) -> TaskGeometry."""
    from app.core.geo import geography_to_geojson

    out: dict[str, TaskGeometry] = {}
    for t in rows:
        gj = geography_to_geojson(t.geometry) or {}
        if gj.get("type") == "Point":
            coords = [tuple(gj["coordinates"][:2])]
        else:
            coords = [tuple(c[:2]) for c in gj.get("coordinates", [])]
        out[t.task_key] = TaskGeometry(t.task_key, t.task_type, coords, hover_s=t.hover_time_s or 0.0)
    return out
