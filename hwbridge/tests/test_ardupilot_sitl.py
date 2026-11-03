"""The bridge against REAL ArduPilot firmware (ArduCopter SITL), not the
practice vehicle. This is what proves the bridge's MAVLink assumptions:
parameters, GUIDED arm/takeoff/goto, limits, pilot-override detection,
missions, reboot and system-id change.

Start SITL first (two instances on TCP 5760 / 5770, system ids 1 and 2):

    powershell -File sim/ardupilot_sitl/start.ps1
    backend\\venv\\Scripts\\python -m pytest hwbridge/tests/test_ardupilot_sitl.py -v

Skipped automatically when no ArduPilot answers on 127.0.0.1:5760.
"""
from __future__ import annotations

import socket
import subprocess
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from hwbridge import server

SITL = [("tcp:127.0.0.1:5760", "tcp5760-1"), ("tcp:127.0.0.1:5770", "tcp5770-2")]


def _sitl_up() -> bool:
    """The ArduPilot SITL container must be running: the practice vehicle also
    listens on 5760 and must never stand in for real firmware here."""
    try:
        out = subprocess.run(["docker", "ps", "--filter", "name=^dronematrx_ardupilot_sitl$", "--format", "{{.Names}}"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    if "dronematrx_ardupilot_sitl" not in out:
        return False
    try:
        with socket.create_connection(("127.0.0.1", 5760), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _sitl_up(), reason="ArduPilot SITL not running (powershell -File sim/ardupilot_sitl/start.ps1)")


def wait_for(fn, timeout=60.0, step=0.5):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(step)
    raise AssertionError(f"condition not met in {timeout:.0f} s (last: {last})")


@pytest.fixture(scope="module")
def bridge(tmp_path_factory):
    cfg = tmp_path_factory.mktemp("sitl") / "config.json"
    server.CONFIG.clear()
    server.CONFIG.update(server.load_config(str(cfg)))
    server.CONFIG["nats_url"] = "nats://127.0.0.1:1"
    server.state["links"] = []
    with TestClient(server.app) as client:
        client.headers["X-Bridge-Token"] = server.CONFIG["token"]
        for endpoint, _ in SITL:
            assert client.post("/links", json={"endpoint": endpoint, "baud": 0}).status_code == 200
        wait_for(lambda: {c["fc_id"] for c in client.get("/fc").json()["controllers"]} >= {fc for _, fc in SITL}, 60)
        yield client
    server.state["links"] = []


def fc(client, fc_id):
    return next(c for c in client.get("/fc").json()["controllers"] if c["fc_id"] == fc_id)


def test_identifies_real_arducopter(bridge):
    info = fc(bridge, "tcp5760-1")
    assert info["firmware"].startswith("ArduPilot v") and info["vehicle_type"] == 2  # MAV_TYPE_QUADROTOR


def test_reads_and_writes_parameters_with_confirmation(bridge):
    view = bridge.get("/fc/tcp5760-1/setup", params={"refresh": True}).json()
    p = view["params"]
    assert p["SYSID_THISMAV"] == 1 and p["FS_GCS_ENABLE"] == 1 and p["FENCE_ENABLE"] == 1
    assert p["FRAME_CLASS"] is not None and p["RC3_MIN"] is not None  # real firmware has every setup parameter
    old = p["RTL_ALT"]
    r = bridge.post("/fc/tcp5760-1/params", json={"values": {"RTL_ALT": 1600}})
    assert r.status_code == 200, r.text
    assert bridge.get("/fc/tcp5760-1/setup", params={"refresh": True}).json()["params"]["RTL_ALT"] == 1600
    bridge.post("/fc/tcp5760-1/params", json={"values": {"RTL_ALT": old}})
    r = bridge.post("/fc/tcp5760-1/params", json={"values": {"NOT_A_REAL_PARAM": 1}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PARAM_NOT_SET"


def test_level_calibration_and_motor_test_accepted(bridge):
    assert bridge.post("/fc/tcp5760-1/calibration/level").status_code == 200
    r = bridge.post("/fc/tcp5760-1/motor-test", json={"motor": 1, "throttle_pct": 5, "seconds": 1, "propellers_removed": True})
    assert r.status_code == 200 and r.json()["result"] == "ACCEPTED", r.text


def test_prearm_reports_home_and_gps(bridge):
    pre = wait_for(lambda: (p := bridge.get("/fc/tcp5760-1/prearm").json()) and p["home_set"] and (p["gps_fix"] or 0) >= 3 and p, 120)
    assert pre["failsafe_params"]["FS_THR_ENABLE"] == 1
    assert pre["landed_state"] == 1  # MAV_LANDED_STATE_ON_GROUND


def test_guided_flight_limits_and_rtl(bridge):
    fc_id = "tcp5760-1"
    assert bridge.post(f"/fc/{fc_id}/bind", json={"drone_id": str(uuid.uuid4()), "uin": "SITL-1", "name": "SITL-1"}).status_code == 200
    # No permission -> refused before anything is sent
    r = bridge.post(f"/fc/{fc_id}/command", json={"command": "arm"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "FLIGHT_NOT_AUTHORIZED"
    bridge.post(f"/fc/{fc_id}/authorize", json={"until_epoch": time.time() + 600, "by": "sitl-test"})
    # ArduPilot needs a few seconds after GPS lock (EKF) before it accepts arming
    wait_for(lambda: bridge.post(f"/fc/{fc_id}/command", json={"command": "arm"}).json().get("result") == 0, 90, step=3)
    assert fc(bridge, fc_id)["mode"] == "GUIDED"
    r = bridge.post(f"/fc/{fc_id}/command", json={"command": "takeoff", "params": {"altitude_m": 8}})
    assert r.json()["result"] == 0, r.text
    wait_for(lambda: bridge.get(f"/fc/{fc_id}/prearm").json()["landed_state"] == 2, 30)  # in air
    home = bridge.get(f"/fc/{fc_id}/prearm").json()["home"]
    # Beyond the hard distance limit: refused by the bridge, nothing sent
    r = bridge.post(f"/fc/{fc_id}/command", json={"command": "goto", "params": {"latitude": home[0] + 0.01, "longitude": home[1], "altitude_m": 10}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "DISTANCE_LIMIT"
    r = bridge.post(f"/fc/{fc_id}/command", json={"command": "goto", "params": {"latitude": home[0], "longitude": home[1], "altitude_m": 45}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ALTITUDE_LIMIT"
    r = bridge.post(f"/fc/{fc_id}/command", json={"command": "goto", "params": {"latitude": home[0] + 0.0002, "longitude": home[1], "altitude_m": 10}})
    assert r.json()["result"] == 0, r.text
    assert bridge.post(f"/fc/{fc_id}/command", json={"command": "rtl"}).json()["result"] == 0
    wait_for(lambda: not fc(bridge, fc_id)["armed"], 120, step=2)  # lands and disarms by itself
    assert fc(bridge, fc_id)["override"] is not None  # RTL -> LAND -> disarm: ArduPilot changed mode on its own
    bridge.post(f"/fc/{fc_id}/override/release", json={"by": "sitl-test"})


def test_mode_change_outside_drone_matrx_locks_out(bridge):
    fc_id = "tcp5760-1"
    from hwbridge.ardupilot import MODE_NUMBER

    v = server.vehicles()[fc_id]
    # Another ground station (or the RC switch) changes the mode: not commanded by Drone MatrX.
    v.mav.command_long_send(v.sysid, v.compid, 176, 0, 1, MODE_NUMBER["LOITER"], 0, 0, 0, 0, 0)
    wait_for(lambda: fc(bridge, fc_id)["override"] is not None, 15)
    r = bridge.post(f"/fc/{fc_id}/command", json={"command": "hold"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PILOT_OVERRIDE"
    bridge.post(f"/fc/{fc_id}/override/release", json={"by": "sitl-test"})
    bridge.post(f"/fc/{fc_id}/unbind")


def test_mission_upload_accepted_by_ardupilot(bridge):
    fc_id = "tcp5770-2"
    pre = wait_for(lambda: (p := bridge.get(f"/fc/{fc_id}/prearm").json()) and p["home_set"] and p, 120)
    lat, lon = pre["home"][0], pre["home"][1]
    items = [{"command": 22, "alt": 10, "frame": 3},
             {"command": 16, "lat": lat + 0.0002, "lon": lon, "alt": 10, "frame": 3},
             {"command": 16, "lat": lat + 0.0002, "lon": lon + 0.0002, "alt": 10, "frame": 3},
             {"command": 20, "frame": 2}]
    r = bridge.post(f"/fc/{fc_id}/mission", json={"items": items})
    assert r.status_code == 200 and r.json()["uploaded"] == 4, r.text
    far = [{"command": 16, "lat": lat + 0.01, "lon": lon, "alt": 10, "frame": 3}]
    r = bridge.post(f"/fc/{fc_id}/mission", json={"items": far})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "DISTANCE_LIMIT"


def test_system_id_change_survives_reboot(bridge):
    r = bridge.post("/fc/tcp5770-2/sysid", json={"system_id": 7})
    assert r.status_code == 200, r.text
    wait_for(lambda: "tcp5770-7" in {c["fc_id"] for c in bridge.get("/fc").json()["controllers"]}, 90, step=2)
    assert bridge.get("/fc/tcp5770-7/setup", params={"refresh": True}).json()["params"]["SYSID_THISMAV"] == 7
    # Restore the instance's original id for the next run
    bridge.post("/fc/tcp5770-7/sysid", json={"system_id": 2})
    wait_for(lambda: "tcp5770-2" in {c["fc_id"] for c in bridge.get("/fc").json()["controllers"]}, 90, step=2)
