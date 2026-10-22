"""Sprint 5: every task type has its own planning behaviour (through the
real wizard + CBBA path, not just a different label)."""
import math

from shapely.geometry import LineString, Polygon, shape

from app.services.coordination.geometry import LocalProjection
from app.services.coordination.task_generation import CoverageSpec, generate_coverage_tasks, generate_search_cells
from tests.planning_support import AOI, BASE_LAT, BASE_LON, create_mission, fleet, headers_for, save_step  # noqa: F401

STEPS_BASE = {
    2: {"source": "drawn", "geometry": AOI},
    5: {"min_battery_pct": 30, "max_tasks_per_drone": 30},
}


def plan_for(client, headers, fleet, template, flight=None, sensors=("camera",), constraints=None, drones=4):
    mission = create_mission(client, headers, f"tasktype-{template}")
    save_step(client, headers, mission["id"], 1, {"template": template})
    save_step(client, headers, mission["id"], 2, STEPS_BASE[2])
    wiz = client.get(f"/missions/{mission['id']}/wizard", headers=headers).json()
    save_step(client, headers, mission["id"], 3, {**wiz["config"]["flight"], **(flight or {})})
    capable = [d for d in fleet if any(p.type in sensors for p in d.payloads)][:drones]
    save_step(client, headers, mission["id"], 4, {"required_sensors": list(sensors), "drone_ids": [str(d.id) for d in capable]})
    save_step(client, headers, mission["id"], 5, {**STEPS_BASE[5], **(constraints or {})})
    resp = client.post(f"/missions/{mission['id']}/cbba/allocate", headers=headers, json={})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_survey_is_lawnmower_lanes(client, admin_user, fleet):
    plan = plan_for(client, headers_for(client, admin_user), fleet, "survey")
    tasks = plan["tasks"]
    assert tasks and all(t["task_type"] == "coverage" and len(t["geometry"]["coordinates"]) == 2 for t in tasks)
    assert plan["converged"] and plan["coverage_pct"] > 90


def test_search_divides_aoi_into_cells(client, admin_user, fleet):
    plan = plan_for(client, headers_for(client, admin_user), fleet, "search", sensors=("thermal",))
    tasks = plan["tasks"]
    assert tasks and all(t["task_type"] == "search_cell" for t in tasks)
    assert all(t["task_key"].startswith("S") for t in tasks)
    # Cells are multi-leg creeping-line sweeps (edge cells clipped by the AOI
    # boundary may fit a single lane), not single straight lanes.
    multi = sum(len(t["geometry"]["coordinates"]) >= 4 for t in tasks)
    assert multi >= 0.6 * len(tasks), (multi, len(tasks))
    assert plan["diagnostics"]["coverage"]["mode"] == "search_cells"
    assert plan["converged"] and plan["coverage_pct"] > 85


def test_agriculture_uses_explicit_lane_spacing(client, admin_user, fleet):
    h = headers_for(client, admin_user)
    a = plan_for(client, h, fleet, "agriculture", flight={"lane_spacing_m": 15}, sensors=("multispectral",), drones=1)
    b = plan_for(client, h, fleet, "agriculture", flight={"lane_spacing_m": 30}, sensors=("multispectral",), drones=1)
    sa = a["diagnostics"]["coverage"]["passes"][0]["lane_spacing_m"]
    sb = b["diagnostics"]["coverage"]["passes"][0]["lane_spacing_m"]
    assert sa == 15.0 and sb == 30.0
    # Half the spacing -> roughly twice the planned lane length.
    la, lb = sum(t["length_m"] for t in a["tasks"]), sum(t["length_m"] for t in b["tasks"])
    assert 1.7 < la / lb < 2.3, (la, lb)


def test_inspection_routes_through_targets_only(client, admin_user, fleet):
    targets = [
        {"latitude": BASE_LAT + 0.001, "longitude": BASE_LON + 0.005, "hover_time_s": 20},
        {"latitude": BASE_LAT - 0.001, "longitude": BASE_LON + 0.007, "hover_time_s": 20},
        {"latitude": BASE_LAT + 0.0015, "longitude": BASE_LON + 0.008, "hover_time_s": 20},
    ]
    plan = plan_for(client, headers_for(client, admin_user), fleet, "inspection", constraints={"inspection_points": targets})
    assert [t["task_type"] for t in plan["tasks"]] == ["inspection"] * 3
    assert plan["assigned_count"] == 3
    # Route waypoints visit each target.
    visited = {tuple(round(c, 6) for c in wp) for b in plan["bundles"] for wp in b["waypoints"]}
    for t in targets:
        assert (round(t["longitude"], 6), round(t["latitude"], 6)) in visited


def test_custom_waypoint_mission(client, admin_user, fleet):
    wps = [{"latitude": BASE_LAT + 0.0005 * i, "longitude": BASE_LON + 0.004 + 0.0005 * i} for i in range(5)]
    plan = plan_for(client, headers_for(client, admin_user), fleet, "custom", constraints={"custom_waypoints": wps}, drones=2)
    assert sorted(t["task_key"] for t in plan["tasks"]) == [f"W{i:03d}" for i in range(1, 6)]
    assert all(t["task_type"] == "waypoint" for t in plan["tasks"])
    assert plan["assigned_count"] == 5


def test_search_cells_tile_the_aoi_algorithm_level():
    proj = LocalProjection.for_polygon(AOI)
    aoi = proj.polygon_to_local(AOI)
    spec = CoverageSpec(altitude_m=40, overlap_pct=30, sensor="thermal")
    cells = generate_search_cells(aoi, spec, cell_size_m=150)
    lanes = generate_coverage_tasks(aoi, spec)
    assert len(cells.tasks) != len(lanes.tasks)
    for t in cells.tasks:
        path = cells.segments[t.id]
        assert isinstance(path, LineString) and math.isclose(t.service_distance_m, path.length)
        assert aoi.buffer(1.0).contains(path)
    # Cells are disjoint: every task's sweep stays within its own ~150 m cell.
    for t in cells.tasks:
        minx, miny, maxx, maxy = cells.segments[t.id].bounds
        assert max(maxx - minx, maxy - miny) <= 150 * math.sqrt(2) + 1


def test_search_cells_cover_irregular_polygon():
    lshape = Polygon([(0, 0), (600, 0), (600, 200), (200, 200), (200, 600), (0, 600)])
    spec = CoverageSpec(altitude_m=40, overlap_pct=30, sensor="thermal")
    cells = generate_search_cells(lshape, spec, cell_size_m=120)
    union = shape({"type": "MultiLineString", "coordinates": [list(cells.segments[t.id].coords) for t in cells.tasks]})
    covered = union.buffer(cells.swath_m / 2).intersection(lshape).area / lshape.area
    assert covered > 0.9
