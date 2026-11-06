"""ORCA on real-aircraft control paths: the simulator's engine running in the
bridge, steering practice vehicles through the same MAVLink the bridge uses
with ArduPilot (GUIDED velocity setpoints, AUTO resume).

Head-on at the same altitude: without ORCA the two aircraft fly through each
other; with ORCA they keep their distance and both still finish the mission.
"""
from __future__ import annotations

import math
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from hwbridge import server
from hwbridge.tools.practice_vehicle import M_PER_DEG, PracticeCopter

LAT, LON = 23.2500, 72.6600
SLOTS = (61, 62)  # tcp 6360 / 6370
GAP_DEG = 100 / (M_PER_DEG * math.cos(math.radians(LAT)))  # 100 m east-west


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    a = PracticeCopter(SLOTS[0], LAT, LON)                 # west, flies east
    b = PracticeCopter(SLOTS[1], LAT, LON + GAP_DEG)       # east, flies west
    for v in (a, b):
        threading.Thread(target=v.run, daemon=True).start()
    cfg = tmp_path_factory.mktemp("orca") / "config.json"
    server.CONFIG.clear()
    server.CONFIG.update(server.load_config(str(cfg)))
    server.CONFIG["nats_url"] = "nats://127.0.0.1:1"
    server.state["links"] = []
    with TestClient(server.app) as client:
        client.headers["X-Bridge-Token"] = server.CONFIG["token"]
        for slot in SLOTS:
            assert client.post("/links", json={"endpoint": f"tcp:127.0.0.1:{5760 + 10 * (slot - 1)}"}).status_code == 200
        ids = [f"tcp{5760 + 10 * (s - 1)}-{s}" for s in SLOTS]
        wait_for(lambda: all(i in {c["fc_id"] for c in client.get("/fc").json()["controllers"]} for i in ids), 20)
        for fc in ids:
            client.post(f"/fc/{fc}/bind", json={"drone_id": str(uuid.uuid4()), "uin": fc.upper(), "name": fc})
        wait_for(lambda: all(client.get(f"/fc/{fc}/prearm").json()["home_set"] for fc in ids), 20)
        yield client, (a, b), ids
    server.state["links"] = []


def wait_for(fn, timeout=30.0, step=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = fn()
        if out:
            return out
        time.sleep(step)
    raise AssertionError("condition not met in time")


def reset(client, copters, ids):
    """Back on the ground at the start points. Putting them there 'by hand' is a
    mode change outside Drone MatrX, so the bridge sets the override lock: wait
    until it has seen the change, then release the lock (as an operator would)."""
    for c, (lat, lon) in zip(copters, ((LAT, LON), (LAT, LON + GAP_DEG))):
        with c.lock:
            c.lat, c.lon, c.alt, c.armed, c.mode = lat, lon, 0.0, False, "STABILIZE"
            c.mission, c.mission_seq, c.target, c.vel_cmd, c.speed = [], 0, None, None, 5.0
    wait_for(lambda: all(not x["armed"] and x["mode"] == "STABILIZE" for x in client.get("/fc").json()["controllers"]), 10)
    time.sleep(0.5)
    for fc in ids:
        client.post(f"/fc/{fc}/override/release", json={"by": "test"})
    assert all(x["override"] is None for x in client.get("/fc").json()["controllers"])


def fly(client, fc, dest_lon, alt=20.0, speed=5.0):
    items = [{"command": 22, "alt": alt, "frame": 3}, {"command": 178, "param1": 1, "param2": speed, "frame": 2},
             {"command": 16, "lat": LAT, "lon": dest_lon, "alt": alt, "frame": 3}]
    assert client.post(f"/fc/{fc}/mission", json={"items": items}).status_code == 200
    client.post(f"/fc/{fc}/authorize", json={"until_epoch": time.time() + 300, "by": "test"})
    r = client.post(f"/fc/{fc}/command", json={"command": "mission_start"})
    assert r.status_code == 200 and r.json()["result"] == 0, r.text


def separation(a, b) -> float:
    dn = (b.lat - a.lat) * M_PER_DEG
    de = (b.lon - a.lon) * M_PER_DEG * math.cos(math.radians(a.lat))
    return math.hypot(dn, de)


def head_on(client, copters, ids, orca: bool) -> dict:
    a, b = copters
    reset(client, copters, ids)
    client.post("/orca", json={"enabled": orca})
    fly(client, ids[0], LON + GAP_DEG)
    fly(client, ids[1], LON)
    min_sep, modes = 1e9, set()
    deadline = time.time() + 60
    while time.time() < deadline:
        if a.alt > 15 and b.alt > 15:
            min_sep = min(min_sep, separation(a, b))
            modes.update((a.mode, b.mode))  # airborne only: mission_start itself passes through GUIDED
        done = all(len(c.mission) and c.mission_seq >= len(c.mission) for c in copters)
        if done:
            break
        time.sleep(0.05)
    return {"min_sep": min_sep, "modes": modes, "done": done,
            "end_dist": [abs((a.lon - (LON + GAP_DEG)) * M_PER_DEG * math.cos(math.radians(LAT))), abs((b.lon - LON) * M_PER_DEG * math.cos(math.radians(LAT)))]}


def test_without_orca_they_fly_through_each_other(rig):
    client, copters, ids = rig
    out = head_on(client, copters, ids, orca=False)
    assert out["done"], out
    assert out["min_sep"] < 1.0, out  # what ORCA must prevent
    assert out["modes"] == {"AUTO"}, out  # nobody steered them


def test_with_orca_they_keep_their_distance_and_finish(rig):
    client, copters, ids = rig
    out = head_on(client, copters, ids, orca=True)
    # The engine's avoidance envelope is 2 x (1 m radius + 2 m margin) = 6 m.
    assert out["min_sep"] > 4.0, out
    assert out["done"], out  # both resumed AUTO and reached their destination
    assert "GUIDED" in out["modes"]  # ORCA steered through GUIDED velocity setpoints
    assert max(out["end_dist"]) < 3.0, out
    st = client.get("/orca").json()
    kinds = {e["kind"] for e in st["events"]}
    assert {"conflict_predicted", "avoidance_applied"} <= kinds
    # ORCA's own mode changes are never mistaken for a pilot takeover
    assert all(c["override"] is None for c in client.get("/fc").json()["controllers"])


def test_pilot_flown_aircraft_is_avoided_but_never_steered(rig):
    client, copters, ids = rig
    a, b = copters
    reset(client, copters, ids)
    client.post("/orca", json={"enabled": True})
    # B: the safety pilot hovers it half-way, in A's path (mode change outside Drone MatrX).
    with b.lock:
        b.lon, b.alt, b.armed, b.mode = LON + GAP_DEG / 2, 20.0, True, "LOITER"
        b.target = (b.lat, b.lon, 20.0)
    wait_for(lambda: next(c for c in client.get("/fc").json()["controllers"] if c["fc_id"] == ids[1])["armed"], 10)
    fly(client, ids[0], LON + GAP_DEG)
    min_sep, b_modes = 1e9, set()
    deadline = time.time() + 60
    while time.time() < deadline and not (a.mission and a.mission_seq >= len(a.mission)):
        if a.alt > 15:
            min_sep = min(min_sep, separation(a, b))
        b_modes.add(b.mode)
        time.sleep(0.05)
    assert a.mission_seq >= len(a.mission), "A did not finish around the hovering aircraft"
    assert min_sep > 4.0, min_sep
    assert b_modes == {"LOITER"}  # never taken over by ORCA


def test_no_flight_permission_no_steering(rig):
    """Permissions end before the two meet: ORCA must not steer them (only the RC pilots can act)."""
    client, copters, ids = rig
    a, b = copters
    reset(client, copters, ids)
    client.post("/orca", json={"enabled": True})
    fly(client, ids[0], LON + GAP_DEG)
    fly(client, ids[1], LON)
    for fc in ids:
        client.delete(f"/fc/{fc}/authorize")
    modes, steered = set(), False
    deadline = time.time() + 60
    while time.time() < deadline and not all(c.mission and c.mission_seq >= len(c.mission) for c in copters):
        if a.alt > 15 and b.alt > 15:
            modes.update((a.mode, b.mode))
        steered = steered or any(x["steered_by_orca"] for x in client.get("/orca").json()["aircraft"])
        time.sleep(0.1)
    assert modes == {"AUTO"} and not steered, modes
