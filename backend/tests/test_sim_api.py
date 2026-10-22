"""Sprint 5: simulation API, fleet staging, session/world/preflight, adapters,
route building, RBAC. Uses FakeSimhost for the simulator HTTP API (no
Gazebo here; see test_sim_gazebo_integration.py for the real simulator)."""
import math
import uuid
import xml.etree.ElementTree as ET

import pytest
from shapely.geometry import Point, shape

from app.models import Drone, Mission
from app.services.simulation import orchestrator as orch_mod
from app.services.simulation.adapters import AdapterError, HardwareAdapter, HardwareAuthorization, SimulationAdapter
from app.services.simulation.routes import MAV_CMD_NAV_RETURN_TO_LAUNCH, MAV_CMD_NAV_TAKEOFF, MAV_CMD_NAV_WAYPOINT, TaskGeometry, build_mission
from tests.planning_support import headers_for
from tests.sim_support import SIM_AOI, FakeSimhost, plan_sim_mission


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    fake = FakeSimhost()
    monkeypatch.setattr(orch_mod, "SimhostClient", lambda *a, **k: fake)
    monkeypatch.setattr(orch_mod.settings, "SIM_WORKSPACE_DIR", str(tmp_path / "ws"))
    return fake


def test_sim_fleet_is_provisioned_and_flagged(client, admin_user, fake):
    h = headers_for(client, admin_user)
    r = client.get("/simulations/fleet", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert len(body["drones"]) == 16 and all(d["simulated"] for d in body["drones"])
    assert [d["name"] for d in body["drones"]][:3] == ["SIM-01", "SIM-02", "SIM-03"]
    assert body["capacity"]["max_instances"] == 8
    # Idempotent.
    assert len(client.get("/simulations/fleet", headers=h).json()["drones"]) == 16
    # Real fleet listing marks them.
    drones = client.get("/drones", headers=h).json()["items"]
    assert all(d["simulated"] == d["name"].startswith("SIM-") for d in drones)


def test_stage_fleet_positions(client, admin_user, fake, db):
    h = headers_for(client, admin_user)
    mid = client.post("/missions", headers=h, json={"name": "s", "execution_mode": "simulation"}).json()["id"]
    client.put(f"/missions/{mid}/wizard/steps/1", headers=h, json={"data": {"template": "survey"}})
    client.put(f"/missions/{mid}/wizard/steps/2", headers=h, json={"data": {"source": "drawn", "geometry": SIM_AOI}})
    wiz = client.get(f"/missions/{mid}/wizard", headers=h).json()
    client.put(f"/missions/{mid}/wizard/steps/3", headers=h, json={"data": wiz["config"]["flight"]})
    r = client.post(f"/simulations/missions/{mid}/fleet", headers=h, json={"drone_count": 4})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["drones"]) == 4 and body["min_separation_m"] >= 5.9
    poly = shape(SIM_AOI)
    assert all(poly.contains(Point(lon, lat)) for lon, lat in body["launch_positions"])
    assert body["wizard"]["config"]["sensors"]["drone_ids"] == [d["id"] for d in body["drones"]]
    for d in body["drones"]:
        assert (d["longitude"], d["latitude"]) in [tuple(p) for p in body["launch_positions"]]


def test_stage_never_silently_reduces_fleet(client, admin_user, monkeypatch, tmp_path):
    fake = FakeSimhost(max_instances=2)
    monkeypatch.setattr(orch_mod, "SimhostClient", lambda *a, **k: fake)
    h = headers_for(client, admin_user)
    mid = client.post("/missions", headers=h, json={"name": "s", "execution_mode": "simulation"}).json()["id"]
    client.put(f"/missions/{mid}/wizard/steps/1", headers=h, json={"data": {"template": "survey"}})
    client.put(f"/missions/{mid}/wizard/steps/2", headers=h, json={"data": {"source": "drawn", "geometry": SIM_AOI}})
    r = client.post(f"/simulations/missions/{mid}/fleet", headers=h, json={"drone_count": 4})
    assert r.status_code == 409 and r.json()["code"] == "FLEET_TOO_LARGE" and "supports 2" in r.json()["detail"]


def test_live_mission_cannot_be_simulated(client, admin_user, fake):
    h = headers_for(client, admin_user)
    mid = client.post("/missions", headers=h, json={"name": "live"}).json()["id"]
    r = client.post(f"/simulations/missions/{mid}/fleet", headers=h, json={"drone_count": 2})
    assert r.status_code == 409 and r.json()["code"] == "NOT_SIMULATION"


def test_session_world_and_preflight(client, admin_user, fake, monkeypatch, tmp_path):
    h = headers_for(client, admin_user)
    mid = plan_sim_mission(client, h, drones=3)
    r = client.post("/simulations", headers=h, json={"mission_id": mid, "dem_source": "flat"})
    assert r.status_code == 201, r.text
    sess = r.json()
    assert sess["state"] == "created" and sess["requested_drones"] == 3 and len(sess["instances"]) == 3
    sid = sess["id"]
    # Start is refused before world + preflight.
    assert client.post(f"/simulations/{sid}/start", headers=h).status_code == 409
    r = client.post(f"/simulations/{sid}/world/generate", headers=h)
    assert r.status_code == 200, r.text
    world = r.json()["world"]
    assert world["terrain"]["is_fallback"] is True and world["terrain"]["source"] == "flat"
    sdf = tmp_path / "ws" / sid / "world.sdf"
    root = ET.parse(sdf).getroot()
    assert root.find("world").get("name") == world["world_name"]
    detail = client.get(f"/simulations/{sid}", headers=h).json()
    for inst in detail["instances"]:
        assert {"x", "y", "z", "pose"} <= set(inst["spawn"])
        assert inst["bundle"] is not None
    # Preflight is honest: no telemetry runtime in the test app -> fails that check only.
    pf = client.post(f"/simulations/{sid}/preflight", headers=h).json()
    failed = {c["code"] for c in pf["checks"] if not c["passed"]}
    assert pf["passed"] is False and failed == {"TELEMETRY_PIPELINE"}, pf
    monkeypatch.setattr(orch_mod.SimulationOrchestrator, "telemetry_ready", lambda self: True)
    pf = client.post(f"/simulations/{sid}/preflight", headers=h).json()
    assert pf["passed"] is True, pf
    # One active session per mission.
    r = client.post("/simulations", headers=h, json={"mission_id": mid})
    assert r.status_code == 409 and r.json()["code"] == "SESSION_EXISTS"


def test_simulator_unavailable_blocks_preflight(client, admin_user, monkeypatch, tmp_path, fake):
    from app.services.simulation.simhost import SimhostError

    h = headers_for(client, admin_user)
    mid = plan_sim_mission(client, h, drones=2)
    sid = client.post("/simulations", headers=h, json={"mission_id": mid, "dem_source": "flat"}).json()["id"]
    client.post(f"/simulations/{sid}/world/generate", headers=h)

    def down():
        raise SimhostError("Simulation host unreachable")

    monkeypatch.setattr(fake, "health", down)
    pf = client.post(f"/simulations/{sid}/preflight", headers=h).json()
    failed = {c["code"] for c in pf["checks"] if not c["passed"]}
    assert {"SIMHOST", "GAZEBO", "SITL", "DRONE_COUNT"} <= failed


def test_rbac_simulation_writes(client, observer_user, admin_user, fake):
    ho = headers_for(client, observer_user)
    assert client.get("/simulations/fleet", headers=ho).status_code == 200
    h = headers_for(client, admin_user)
    mid = client.post("/missions", headers=h, json={"name": "s", "execution_mode": "simulation"}).json()["id"]
    assert client.post(f"/simulations/missions/{mid}/fleet", headers=ho, json={"drone_count": 1}).status_code == 403
    assert client.post("/simulations", headers=ho, json={"mission_id": mid}).status_code == 403
    assert client.post(f"/simulations/{uuid.uuid4()}/failures", headers=ho, json={"type": "kill"}).status_code == 403
    assert client.get(f"/simulations/{uuid.uuid4()}", headers=ho).status_code == 404


# ---------------------------------------------------------------- adapters


def test_adapters_keep_simulation_and_hardware_apart(db, organization, fake):
    sim_mission = Mission(organization_id=organization.id, name="sim", execution_mode="simulation")
    live_mission = Mission(organization_id=organization.id, name="live", execution_mode="live")
    sim_drone = Drone(organization_id=organization.id, name="SIM-01", uin="SIM-x-01", model="x500", simulated=True)
    real_drone = Drone(organization_id=organization.id, name="DMX-001", model="M30", simulated=False)
    with pytest.raises(AdapterError, match="real drone"):
        SimulationAdapter(fake, "s1", sim_mission, real_drone)
    with pytest.raises(AdapterError) as e:
        SimulationAdapter(fake, "s1", live_mission, sim_drone)
    assert e.value.code == "NOT_SIMULATION"
    with pytest.raises(AdapterError) as e:
        HardwareAdapter(sim_mission, real_drone)
    assert e.value.code == "SIMULATION_MISSION"
    with pytest.raises(AdapterError) as e:
        HardwareAdapter(live_mission, sim_drone)
    assert e.value.code == "SIMULATED_DRONE"
    hw = HardwareAdapter(live_mission, real_drone)
    with pytest.raises(AdapterError) as e:
        hw.arm()
    assert e.value.code == "NOT_AUTHORIZED"
    hw = HardwareAdapter(live_mission, real_drone, HardwareAuthorization("op", False, "test"))
    with pytest.raises(AdapterError) as e:
        hw.takeoff(10)
    assert e.value.code == "SAFETY_CHECKS"
    hw = HardwareAdapter(live_mission, real_drone, HardwareAuthorization("op", True, "test"))
    with pytest.raises(AdapterError) as e:
        hw.upload_mission([])
    assert e.value.code == "HARDWARE_UNAVAILABLE"
    sim = SimulationAdapter(fake, "s1", sim_mission, sim_drone)
    sim.arm()
    sim.goto(23.2, 72.6, 30)
    sim.return_to_launch()
    assert [c[2] for c in fake.calls if c[0] == "command"] == ["arm", "goto", "rtl"]


# ------------------------------------------------------------ route builder


def test_build_mission_items_and_seq_map():
    geoms = {
        "T001": TaskGeometry("T001", "coverage", [(72.640, 23.210), (72.645, 23.210)]),
        "S001": TaskGeometry("S001", "search_cell", [(72.641, 23.211), (72.642, 23.211), (72.642, 23.212), (72.641, 23.212)]),
        "P001": TaskGeometry("P001", "inspection", [(72.643, 23.213)], hover_s=15),
    }
    m = build_mission((72.639, 23.209), [("T001", 1), ("S001", 0), ("P001", 0)], geoms, 40, 8, rth_on_complete=True)
    cmds = [i["command"] for i in m.items]
    assert cmds[0] == MAV_CMD_NAV_TAKEOFF and cmds[-1] == MAV_CMD_NAV_RETURN_TO_LAUNCH
    wps = [i for i in m.items if i["command"] == MAV_CMD_NAV_WAYPOINT]
    assert len(wps) == 2 + 4 + 1
    # Orientation 1 reverses the lane.
    assert (wps[0]["lon"], wps[1]["lon"]) == (72.645, 72.640)
    exits = {v["task_key"]: s for s, v in m.seq_map.items() if v.get("role") == "exit"}
    assert set(exits) == {"T001", "S001", "P001"} and exits["T001"] < exits["S001"] < exits["P001"]
    assert m.items[exits["P001"]]["param1"] == 15  # inspection hover
    assert all(i["alt"] == 40 for i in wps)
    air = build_mission((72.640, 23.210), [("P001", 0)], geoms, 40, 8, airborne=True)
    assert air.items[0]["command"] != MAV_CMD_NAV_TAKEOFF
    assert math.isclose(m.planned_route[0][0], 72.639)


def test_stage_before_flight_parameters_uses_template_defaults(client, admin_user, fake):
    """The Simulation page chooses drones before flight parameters."""
    h = headers_for(client, admin_user)
    mid = client.post("/missions", headers=h, json={"name": "s", "execution_mode": "simulation"}).json()["id"]
    client.put(f"/missions/{mid}/wizard/steps/1", headers=h, json={"data": {"template": "search"}})
    client.put(f"/missions/{mid}/wizard/steps/2", headers=h, json={"data": {"source": "drawn", "geometry": SIM_AOI}})
    r = client.post(f"/simulations/missions/{mid}/fleet", headers=h, json={"drone_count": 2})
    assert r.status_code == 200, r.text
    wiz = r.json()["wizard"]
    assert {1, 2, 3, 4} <= set(wiz["completed_steps"])
    assert wiz["config"]["flight"]["pattern"] == "sar_cells"  # search template defaults
    assert wiz["config"]["sensors"]["required_sensors"] == ["thermal"]
