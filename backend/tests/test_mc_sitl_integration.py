"""Sprint 6 on the real simulator: SITL bench flight controllers (PX4 v1.15.4
in Gazebo), cameras into the media relay, and the detector.

Marked `gazebo`; reported NOT RUN when simhost is unavailable. The bench is
created with publish=False, so nothing reaches the shared NATS bus.
"""
from __future__ import annotations

import math
import time
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.services.simulation.dem import FlatSource
from app.services.simulation.geo import EnuFrame
from app.services.simulation.simhost import SimhostError
from app.services.simulation.world import ObstacleSpec, SpawnRequest, WorldGenerator, cleanup_workspace
import json
from pathlib import Path

from tests.test_sim_gazebo_integration import CLIENT, WORKSPACE, _REASON, wait

RESULTS = Path(__file__).resolve().parents[2] / "docs" / "sprint6" / "performance" / "sitl_integration.json"


def record(name: str, data: dict) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    existing[name] = {**data, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    RESULTS.write_text(json.dumps(existing, indent=2))

pytestmark = [pytest.mark.gazebo] + ([pytest.mark.skip(reason=f"NOT RUN: simulator unavailable ({_REASON})")] if _REASON else [])

LAT0, LON0 = 23.2400, 72.6500
MARKER_XY = (0.0, 25.0)


def relay_ready(path: str) -> bool:
    s = get_settings()
    try:
        r = httpx.get(f"{s.MEDIAMTX_API_URL}/v3/paths/get/{path}", auth=(s.MEDIAMTX_USER, s.MEDIAMTX_PASS), timeout=3)
    except httpx.HTTPError:
        return False
    return r.status_code == 200 and bool(r.json().get("ready"))


@pytest.fixture(scope="module")
def bench():
    sid = f"bench-{uuid.uuid4().hex[:6]}"
    tag = sid[6:]
    f = EnuFrame(LAT0, LON0)
    half = 160.0
    ring = [f.xy_to_lonlat(x, y) for x, y in ((-half, -half), (half, -half), (half, half), (-half, half), (-half, -half))]
    aoi = {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}
    spawns = []
    for n in range(2):
        lon, lat = f.xy_to_lonlat(-8.0 + 8.0 * n, -30.0)
        spawns.append(SpawnRequest(f"FC-{tag}-{n + 1:02d}", f"FC-{tag}-{n + 1:02d}", lat, lon))
    mlon, mlat = f.xy_to_lonlat(*MARKER_XY)
    world = WorldGenerator(WORKSPACE, uri_root="/workspace").generate(sid, "bench", aoi, spawns, FlatSource(0.0, reason="test bench"),
                                                                      obstacles=[ObstacleSpec("marker_1", mlat, mlon, (2.5, 2.5, 0.05))])
    body = {"session_id": sid, "world_sdf": f"/workspace/{sid}/world.sdf", "world_name": world.world_name, "origin": world.metadata["frame"]["origin"],
            "seed": 3, "bench": True, "camera": True, "publish": False, "params": {"ground_z": 0.0},
            "drones": [{"uin": s.uin, "drone_id": "", "name": s.name, "pose": s.pose_string()} for s in world.spawns]}
    t0 = time.perf_counter()
    CLIENT.create_session(body)
    startup = time.perf_counter() - t0
    yield {"sid": sid, "fcs": [s.uin for s in world.spawns], "marker": (mlat, mlon), "startup_s": round(startup, 1), "frame": f}
    try:
        CLIENT.stop(sid)
    except SimhostError:
        pass
    cleanup_workspace(WORKSPACE, sid)


def test_bench_discovery_identity_within_5s(bench):
    t0 = time.perf_counter()
    out = CLIENT.flight_controllers(bench["sid"])
    ms = (time.perf_counter() - t0) * 1000
    fcs = out["controllers"]
    assert [c["fc_id"] for c in fcs] == bench["fcs"]
    assert all(c["firmware"] == "PX4 v1.15.4" and c["heartbeat_age_s"] is not None and not c["bound"] for c in fcs)
    assert sorted(c["system_id"] for c in fcs) == [1, 2]
    assert ms < 5000
    record("mc_discovery", {"discovery_ms": round(ms, 1), "controllers": len(fcs), "bench_startup_s": bench["startup_s"]})


def test_bind_routes_and_prearm_health_is_real(bench):
    sid, fc = bench["sid"], bench["fcs"][0]
    info = CLIENT.bind_fc(sid, fc, "11111111-1111-1111-1111-111111111111", "ITEST-MC-01", "ITEST-MC-01")
    assert info["bound"] and info["drone_id"] == "11111111-1111-1111-1111-111111111111"
    h = CLIENT.prearm(sid, fc)
    assert h["sensors_health"] & 0x200000, "PX4 reports AHRS healthy"
    assert wait(lambda: CLIENT.prearm(sid, fc)["sensors_health"] & 0x10000000, 60), "PX4 pre-arm bit set once its checks pass"
    assert h["gps_fix"] >= 3 and h["home_set"] and h["landed_state"] == 1
    assert h["failsafe_params"]["NAV_DLL_ACT"] is not None and h["failsafe_params"]["COM_LOW_BAT_ACT"] == 3
    record("mc_prearm_health", {k: h[k] for k in ("sensors_present", "sensors_health", "gps_fix", "satellites", "battery_pct", "failsafe_params")})


def test_commands_acked_by_px4_and_flown(bench):
    sid, fc = bench["sid"], bench["fcs"][0]
    timings = {}
    r = CLIENT.command(sid, fc, "arm_once")
    assert r["result"] == 0, r
    timings["arm"] = r["timing"]
    assert wait(lambda: CLIENT.status(sid)["drones"][fc]["armed"], 10)
    r = CLIENT.command(sid, fc, "takeoff", {"altitude_m": 20})
    assert r["result"] == 0
    timings["takeoff"] = r["timing"]
    assert wait(lambda: (CLIENT.status(sid)["drones"][fc]["truth"] or {}).get("z", 0) > 15, 120)
    mlat, mlon = bench["marker"]
    r = CLIENT.command(sid, fc, "goto", {"latitude": mlat, "longitude": mlon, "altitude_m": 20})
    assert r["result"] == 0
    timings["goto"] = r["timing"]
    f = bench["frame"]

    def near():
        t = CLIENT.status(sid)["drones"][fc]["truth"]
        return t and math.dist(f.lonlat_to_xy(t["lon"], t["lat"]), MARKER_XY) < 3.0

    assert wait(near, 120, step=1.0)
    r = CLIENT.command(sid, fc, "hold")
    assert r["result"] == 0 and wait(lambda: CLIENT.status(sid)["drones"][fc]["mode"] == "HOLD", 10)
    timings["hold"] = r["timing"]
    for t in timings.values():
        assert t["recv_to_send_ms"] < 50 and t["send_to_ack_ms"] is not None
    record("mc_command_timing", timings)


def test_detector_geolocates_marker_from_camera(bench):
    sid, fc = bench["sid"], bench["fcs"][0]
    f = bench["frame"]
    det = wait(lambda: next((d for d in CLIENT.status(sid)["detections_recent"] if d["fc_id"] == fc and d.get("latitude")), None), 60, step=1.0)
    assert det, "no geolocated detection while hovering over the marker"
    err = math.dist(f.lonlat_to_xy(det["longitude"], det["latitude"]), MARKER_XY)
    assert det["label"] == "red_marker" and det["published"] and 0 < det["confidence"] <= 1
    assert err < 4.0, f"geolocation error {err:.2f} m"
    record("mc_detector", {"geolocation_error_m": round(err, 2), "confidence": det["confidence"], "camera_height_m": det["camera_height_m"]})


def test_sitl_cameras_reach_the_relay(bench):
    sid = bench["sid"]
    cams = CLIENT.status(sid)["cameras"]
    assert set(cams) == set(bench["fcs"])
    f0 = {u: c["frames"] for u, c in cams.items()}
    time.sleep(3)
    cams = CLIENT.status(sid)["cameras"]
    assert all(cams[u]["frames"] > f0[u] for u in cams), "camera frames keep arriving"
    for u in bench["fcs"]:
        assert wait(lambda u=u: relay_ready(f"sitl/{u}/hi") and relay_ready(f"sitl/{u}/lo"), 20), f"{u} not published"
    record("mc_cameras", {u: {"frames": c["frames"], "size": [c["width"], c["height"]]} for u, c in cams.items()})


def test_test_pattern_source_loss_and_recovery():
    CLIENT.start_test_sources(1)
    assert wait(lambda: relay_ready("test/01/hi") and relay_ready("test/01/lo"), 20)
    CLIENT.control_test_source(1, "stop")
    assert wait(lambda: not relay_ready("test/01/hi"), 10), "relay drops the path when the source stops"
    CLIENT.control_test_source(1, "start")
    assert wait(lambda: relay_ready("test/01/hi"), 20), "source republished"
