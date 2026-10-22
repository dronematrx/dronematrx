"""Sprint 5 integration tests against the REAL simulator (Gazebo Harmonic +
PX4 SITL in the `simhost` container). No mocks.

    docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d simhost
    pytest -m gazebo tests/test_sim_gazebo_integration.py -s

When the simulator is unreachable every test here is SKIPPED with the reason
"NOT RUN: ..." -- never reported as passed. Results (timings, errors) are
appended to docs/sprint5/performance/gazebo_integration.json.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path

import numpy as np
import pytest

from app.services.simulation.dem import FixtureSource, FlatSource
from app.services.simulation.geo import EnuFrame
from app.services.simulation.routes import TaskGeometry, build_mission
from app.services.simulation.simhost import SimhostClient, SimhostError
from app.services.simulation.world import SpawnRequest, WorldGenerator, cleanup_workspace

pytestmark = pytest.mark.gazebo

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "sim_workspace"
RESULTS = ROOT.parent / "docs" / "sprint5" / "performance" / "gazebo_integration.json"
CLIENT = SimhostClient("http://localhost:8100")

# Deterministic test site: open ground east of Gandhinagar.
LAT0, LON0 = 23.2400, 72.6500


def _available() -> str | None:
    try:
        h = CLIENT.health()
        return None if h.get("versions", {}).get("px4") not in (None, "unknown") else "simhost up but PX4 version unknown"
    except SimhostError as exc:
        return str(exc)


_REASON = _available()
if _REASON:
    pytestmark = [pytest.mark.gazebo, pytest.mark.skip(reason=f"NOT RUN: simulator unavailable ({_REASON})")]


def record(name: str, data: dict) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    existing[name] = {**data, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    RESULTS.write_text(json.dumps(existing, indent=2))


def square(lat0, lon0, half_m):
    f = EnuFrame(lat0, lon0)
    ring = [f.xy_to_lonlat(x, y) for x, y in [(-half_m, -half_m), (half_m, -half_m), (half_m, half_m), (-half_m, half_m), (-half_m, -half_m)]]
    return {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}


def make_world(sid, spawns_xy, source, half=150.0):
    aoi = square(LAT0, LON0, half)
    f = EnuFrame(LAT0, LON0)
    spawns = []
    for n, (x, y) in enumerate(spawns_xy):
        lon, lat = f.xy_to_lonlat(x, y)
        spawns.append(SpawnRequest(f"ITEST-{n + 1:02d}", f"ITEST-{n + 1:02d}", lat, lon))
    gen = WorldGenerator(WORKSPACE, uri_root="/workspace")
    return gen.generate(sid, "itest", aoi, spawns, source)


def start(sid, world, drones=None, **kw):
    body = {
        "session_id": sid, "world_sdf": f"/workspace/{sid}/world.sdf", "world_name": world.world_name,
        "origin": world.metadata["frame"]["origin"], "seed": 7,
        "drones": [{"uin": s.uin, "drone_id": str(uuid.uuid4()), "name": s.name, "pose": s.pose_string()} for s in (drones or world.spawns)],
        "publish": False,  # test drones must not pollute the shared NATS stream
        **kw,
    }
    t0 = time.perf_counter()
    status = CLIENT.create_session(body)
    return status, time.perf_counter() - t0


@pytest.fixture()
def sid():
    s = f"itest{uuid.uuid4().hex[:8]}"
    yield s
    try:
        CLIENT.stop(s)
    except SimhostError:
        pass
    cleanup_workspace(WORKSPACE, s)


def wait(cond, timeout, step=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return None


# ------------------------------------------------------------------ tests


def test_world_loads_terrain_orientation_and_spawn_geodesy(sid):
    """Generated world loads in Gazebo; drones rest on the heightmap at the
    expected height (proves heightmap orientation + elevation scale); the
    Gazebo spawn pose maps back to the requested lat/lon; PX4's reported
    position agrees with Gazebo ground truth."""
    f = EnuFrame(LAT0, LON0)

    def slope(lat, lon):  # rises 8 m per 100 m northwards
        e, n, _ = f.to_enu_np(lat, lon, 0.0)
        return 50.0 + 0.08 * n

    world = make_world(sid, [(0.0, -60.0), (0.0, 60.0)], FixtureSource(slope))
    status, startup = start(sid, world)
    assert status["state"] == "ready", status
    truth = {}
    for uin, d in status["drones"].items():
        assert d["ready"]
        truth[uin] = d["truth"]
    south, north = world.spawns
    # Rest height = terrain + model offset (same offset for both): the
    # difference must equal the terrain difference (9.6 m), sign included.
    dz = truth[north.uin]["z"] - truth[south.uin]["z"]
    assert abs(dz - 0.08 * 120.0) < 0.6, f"terrain dz {dz:.2f} m"
    for sp in world.spawns:
        t = truth[sp.uin]
        horiz = math.dist(f.lonlat_to_xy(sp.longitude, sp.latitude), f.lonlat_to_xy(t["lon"], t["lat"]))
        assert horiz < 0.5, f"spawn {sp.uin} off by {horiz:.2f} m"
    st = CLIENT.status(sid)
    errs = []
    for uin, d in st["drones"].items():
        rep, tr = d["reported"], d["truth"]
        errs.append(math.dist(f.lonlat_to_xy(rep["lon"], rep["lat"]), f.lonlat_to_xy(tr["lon"], tr["lat"])))
    assert max(errs) < 3.0, f"PX4 reported vs Gazebo truth {errs}"
    record("world_terrain_spawn", {"startup_s": round(startup, 2), "terrain_dz_m": round(dz, 3), "reported_vs_truth_m": [round(e, 2) for e in errs],
                                   "heightmap_px": world.metadata["terrain"]["heightmap_px"]})


def test_mission_execution_waypoints_and_landing(sid):
    world = make_world(sid, [(0.0, 0.0)], FlatSource(40.0))
    status, startup = start(sid, world, cruise_speed_mps=8.0)
    sp = world.spawns[0]
    f = EnuFrame(LAT0, LON0)
    geoms = {"L1": TaskGeometry("L1", "coverage", [f.xy_to_lonlat(-40, 20), f.xy_to_lonlat(40, 20)]),
             "L2": TaskGeometry("L2", "coverage", [f.xy_to_lonlat(40, 50), f.xy_to_lonlat(-40, 50)])}
    built = build_mission((sp.longitude, sp.latitude), [("L1", 0), ("L2", 0)], geoms, 20.0, 8.0, rth_on_complete=True)
    res = CLIENT.upload_missions(sid, {sp.uin: built.items}, start=True)
    assert res[sp.uin]["items"] == len(built.items)
    exits = [s for s, v in built.seq_map.items() if v.get("role") == "exit"]
    t0 = time.time()
    done = wait(lambda: set(exits) <= set(CLIENT.status(sid)["drones"][sp.uin]["reached"]), 240)
    flight_s = time.time() - t0
    assert done, CLIENT.status(sid)["drones"][sp.uin]
    landed = wait(lambda: not CLIENT.status(sid)["drones"][sp.uin]["armed"], 180)
    assert landed, "drone must RTL and disarm after the mission"
    record("mission_execution", {"startup_s": round(startup, 2), "waypoints": len(exits) * 2, "flight_wall_s": round(flight_s, 1)})


def test_crash_detection_and_cleanup(sid):
    world = make_world(sid, [(0, 0), (6, 0)], FlatSource(40.0))
    status, _ = start(sid, world)
    victim = world.spawns[1].uin
    CLIENT.failure(sid, "kill", victim, {})
    crashed = wait(lambda: next((i for i in CLIENT.status(sid)["instances"] if i["uin"] == victim and i["state"] == "crashed"), None), 10)
    assert crashed and crashed["returncode"] == -9
    alive = next(i for i in CLIENT.status(sid)["instances"] if i["uin"] != victim)
    assert alive["state"] == "running"
    out = CLIENT.stop(sid)
    assert out["leftover_processes"] == []
    procs = [p for p in CLIENT.processes()["px4"] if p["session"] == sid]
    assert procs == [], procs
    record("crash_cleanup", {"crash_returncode": crashed["returncode"], "leftover": len(procs)})


def test_airborne_crash_falls_and_world_survives(sid):
    """A PX4 process killed in flight: Gazebo's motor model would otherwise hold
    the last thrust and the drone flies off until the physics engine aborts
    (seen in an E2E run: x = 5.9e16 m, then ODE AABB assert). Expect it to
    fall to the ground, stay there, and the world to keep stepping."""
    world = make_world(sid, [(0, 0)], FlatSource(40.0))
    start(sid, world)
    uin = world.spawns[0].uin
    CLIENT.command(sid, uin, "arm")
    CLIENT.command(sid, uin, "takeoff", {"altitude_m": 15})
    assert wait(lambda: (CLIENT.status(sid)["drones"][uin]["truth"] or {}).get("z", 0) > 12, 90)
    ground_z = world.spawns[0].pad_top_z
    CLIENT.failure(sid, "kill", uin, {})

    def truth():
        return CLIENT.status(sid)["drones"][uin]["truth"]

    assert wait(lambda: truth()["z"] < ground_z + 1.0, 30), f"drone did not fall: {truth()}"
    t0 = CLIENT.status(sid)["stats"]["sim_time_s"]
    time.sleep(10)
    st = CLIENT.status(sid)
    t = st["drones"][uin]["truth"]
    assert st["state"] != "failed", st.get("error")
    assert st["stats"]["sim_time_s"] > t0 + 5, "Gazebo stopped stepping"
    assert t["z"] < ground_z + 1.0 and math.hypot(t["x"], t["y"]) < 60, f"drone left the crash site: {t}"
    record("airborne_crash", {"ground_z": round(ground_z, 2), "rest": {k: t[k] for k in ("x", "y", "z")},
                              "sim_time_advanced_s": round(st["stats"]["sim_time_s"] - t0, 1)})


def test_fleet_size_limit_refused(sid):
    world = make_world(sid, [(0, 0)], FlatSource(40.0))
    cap = CLIENT.health()["capacity"]["max_instances"]
    drones = [{"uin": f"OVER-{i}", "drone_id": str(uuid.uuid4()), "name": f"OVER-{i}", "pose": f"{i * 4},0,0.2,0,0,0"} for i in range(cap + 1)]
    with pytest.raises(SimhostError) as e:
        CLIENT.create_session({"session_id": sid, "world_sdf": f"/workspace/{sid}/world.sdf", "world_name": world.world_name,
                               "origin": world.metadata["frame"]["origin"], "drones": drones})
    assert "limit" in str(e.value) or e.value.status in (409, 422)


def test_pause_resume_and_speed(sid):
    world = make_world(sid, [(0, 0)], FlatSource(40.0))
    start(sid, world)
    CLIENT.pause(sid)
    # World stats arrive at a few Hz: sample t1 only once they report the pause,
    # otherwise t1 can predate the last tick before the pause took effect.
    assert wait(lambda: CLIENT.status(sid)["stats"]["paused"], 5, step=0.1)
    time.sleep(0.5)
    t1 = CLIENT.status(sid)["stats"]["sim_time_s"]
    time.sleep(3)
    t2 = CLIENT.status(sid)["stats"]["sim_time_s"]
    assert abs(t2 - t1) < 0.05, "sim time must not advance while paused"
    CLIENT.resume(sid)
    time.sleep(3)
    assert CLIENT.status(sid)["stats"]["sim_time_s"] > t2 + 1.0
    measured = {}
    for s in (1.0, 2.0, 5.0, 10.0):
        CLIENT.speed(sid, s)
        time.sleep(1.0)
        a = CLIENT.status(sid)["stats"]["sim_time_s"]
        w0 = time.time()
        time.sleep(5.0)
        b = CLIENT.status(sid)["stats"]["sim_time_s"]
        measured[f"{s:g}x"] = round((b - a) / (time.time() - w0), 2)
    record("speed_rtf", {"requested_vs_achieved_rtf": measured})
    assert measured["1x"] > 0.8 and measured["2x"] > measured["1x"] * 1.4, measured


def test_obstacle_spawn_location(sid):
    world = make_world(sid, [(0, 0)], FlatSource(40.0))
    start(sid, world)
    f = EnuFrame(LAT0, LON0)
    lon, lat = f.xy_to_lonlat(55.0, -32.0)
    res = CLIENT.failure(sid, "obstacle", None, {"latitude": lat, "longitude": lon, "name": "itest_box", "size_m": [5, 5, 20]})
    ob = res["obstacle"]
    assert ob["exists_in_gazebo"] is True
    err = math.dist(f.lonlat_to_xy(lon, lat), f.lonlat_to_xy(ob["gazebo_latlon"][1], ob["gazebo_latlon"][0]))
    assert err < 0.05, f"obstacle placed {err:.3f} m from the clicked point"
    record("obstacle", {"position_error_m": round(err, 4)})


def test_wind_gust_moves_hovering_drone(sid):
    world = make_world(sid, [(0, 0)], FlatSource(40.0))
    start(sid, world)
    uin = world.spawns[0].uin
    CLIENT.command(sid, uin, "arm")
    CLIENT.command(sid, uin, "takeoff", {"altitude_m": 15})
    assert wait(lambda: (CLIENT.status(sid)["drones"][uin]["truth"] or {}).get("z", 0) > 12, 90)
    time.sleep(8)
    before = CLIENT.status(sid)["drones"][uin]["truth"]
    CLIENT.failure(sid, "wind", uin, {"speed_mps": 20, "from_deg": 270, "duration_s": 3})  # from west -> pushes east
    time.sleep(1.5)
    peak = CLIENT.status(sid)["drones"][uin]["truth"]
    dx, dy = peak["x"] - before["x"], peak["y"] - before["y"]
    record("wind_gust", {"displacement_east_m": round(dx, 3), "displacement_north_m": round(dy, 3)})
    assert dx > 0.2, f"gust from the west must push the drone east (dx={dx:.2f} m)"


def test_link_loss_triggers_px4_failsafe(sid):
    world = make_world(sid, [(0, 0)], FlatSource(40.0))
    start(sid, world, link_loss_policy="LAND")
    uin = world.spawns[0].uin
    CLIENT.command(sid, uin, "arm")
    CLIENT.command(sid, uin, "takeoff", {"altitude_m": 15})
    assert wait(lambda: (CLIENT.status(sid)["drones"][uin]["truth"] or {}).get("z", 0) > 12, 90)
    CLIENT.command(sid, uin, "hold")
    CLIENT.failure(sid, "link", uin, {"down": True})
    mode = wait(lambda: CLIENT.status(sid)["drones"][uin]["mode"] in ("LAND", "RTL") and CLIENT.status(sid)["drones"][uin]["mode"], 40)
    CLIENT.failure(sid, "link", uin, {"down": False})
    record("link_loss_failsafe", {"px4_mode_after_loss": mode})
    assert mode == "LAND", f"PX4 should execute NAV_DLL_ACT=Land, got {mode}"


def test_gps_drift_separates_reported_from_truth(sid):
    world = make_world(sid, [(0, 0)], FlatSource(40.0))
    start(sid, world)
    uin = world.spawns[0].uin
    f = EnuFrame(LAT0, LON0)

    def err():
        d = CLIENT.status(sid)["drones"][uin]
        return math.dist(f.lonlat_to_xy(d["reported"]["lon"], d["reported"]["lat"]), f.lonlat_to_xy(d["truth"]["lon"], d["truth"]["lat"]))

    base = err()
    res = CLIENT.failure(sid, "gps_drift", uin, {"enabled": True})
    # EKF2 first rejects the jump at its innovation gate and only resets onto the
    # biased GPS after its fusion timeout (measured 12-14 s sim), so wait for it.
    t_inject = res["sim_time_s"]
    wait(lambda: err() > base + 2.0, 60, step=1.0)
    drift = err()
    record("gps_drift", {"baseline_error_m": round(base, 2), "error_after_injection_m": round(drift, 2),
                         "px4_reaction_sim_s": round(CLIENT.status(sid)["stats"]["sim_time_s"] - t_inject, 1), "inject": res})
    assert drift > base + 2.0, f"reported position should diverge from truth ({base:.2f} -> {drift:.2f} m)"


def _fly_once(speed: float) -> dict:
    sid = f"itest{uuid.uuid4().hex[:8]}"
    try:
        world = make_world(sid, [(0.0, 0.0)], FlatSource(40.0))
        start(sid, world, cruise_speed_mps=8.0, seed=11)
        sp = world.spawns[0]
        f = EnuFrame(LAT0, LON0)
        geoms = {"L1": TaskGeometry("L1", "coverage", [f.xy_to_lonlat(-60, 30), f.xy_to_lonlat(60, 30)]),
                 "L2": TaskGeometry("L2", "coverage", [f.xy_to_lonlat(60, 70), f.xy_to_lonlat(-60, 70)])}
        built = build_mission((sp.longitude, sp.latitude), [("L1", 0), ("L2", 0)], geoms, 20.0, 8.0, rth_on_complete=True)
        CLIENT.speed(sid, speed)
        b0 = None
        CLIENT.upload_missions(sid, {sp.uin: built.items}, start=True)
        t_armed = None
        t0 = time.time()
        while time.time() - t0 < 600:
            st = CLIENT.status(sid)
            d = st["drones"][sp.uin]
            if d["armed"] and t_armed is None:
                t_armed = st["stats"]["sim_time_s"]
            if t_armed is not None and not d["armed"]:
                break
            time.sleep(0.5)
        st = CLIENT.status(sid)
        d = st["drones"][sp.uin]
        exits = sorted(s for s, v in built.seq_map.items() if v.get("role") == "exit")
        truth = d["truth"]
        land_err = math.dist(f.lonlat_to_xy(truth["lon"], truth["lat"]), f.lonlat_to_xy(sp.longitude, sp.latitude))
        return {"speed": speed, "reached_all": set(exits) <= set(d["reached"]), "flight_sim_s": st["stats"]["sim_time_s"] - (t_armed or 0),
                "landing_error_m": round(land_err, 2), "achieved_rtf": st.get("achieved_rtf"), "wall_s": round(time.time() - t0, 1)}
    finally:
        try:
            CLIENT.stop(sid)
        except SimhostError:
            pass
        cleanup_workspace(WORKSPACE, sid)


def test_speed_1x_vs_10x_equivalence():
    """Same mission at 1x and 10x requested speed. Tolerances (PX4 + physics
    are not bit-for-bit deterministic across real-time factors): all task
    waypoints reached at both speeds; landing within 2 m of launch at both;
    simulated flight time within 10 %."""
    one = _fly_once(1.0)
    ten = _fly_once(10.0)
    rel = abs(one["flight_sim_s"] - ten["flight_sim_s"]) / one["flight_sim_s"]
    record("speed_equivalence", {"1x": one, "10x": ten, "flight_time_rel_diff": round(rel, 4)})
    assert one["reached_all"] and ten["reached_all"]
    assert one["landing_error_m"] < 2.0 and ten["landing_error_m"] < 2.0
    assert rel < 0.10, (one, ten)
    assert ten["wall_s"] < one["wall_s"], "10x must finish faster in wall time"


def test_forced_low_battery_px4_response(sid):
    """Battery injection through PX4's own battery simulator: the state of
    charge falls and PX4's low-battery failsafe (COM_LOW_BAT_ACT) takes over."""
    world = make_world(sid, [(0, 0)], FlatSource(40.0))
    start(sid, world)
    uin = world.spawns[0].uin
    CLIENT.command(sid, uin, "arm")
    CLIENT.command(sid, uin, "takeoff", {"altitude_m": 15})
    assert wait(lambda: (CLIENT.status(sid)["drones"][uin]["truth"] or {}).get("z", 0) > 12, 90)
    CLIENT.command(sid, uin, "hold")
    # 4 % is below PX4's critical (7 %) and emergency (5 %) thresholds: COM_LOW_BAT_ACT=3 -> RTL/land.
    res = CLIENT.failure(sid, "battery", uin, {"target_pct": 4, "drain_s": 15})
    modes, battery = [], []

    def low():
        d = CLIENT.status(sid)["drones"][uin]
        modes.append(d["mode"])
        battery.append(d.get("battery_pct"))
        return d["mode"] in ("RTL", "LAND") and d["mode"]

    mode = wait(low, 90, step=1.0)
    record("battery_low", {"px4_mode": mode, "params": {k: v for k, v in res.items() if k.startswith("SIM_")}, "modes_seen": sorted(set(modes)), "battery_samples": battery[::5] + battery[-1:]})
    assert mode in ("RTL", "LAND"), f"PX4 should leave HOLD on critical battery, saw {sorted(set(modes))}, battery {battery[::5]}"
