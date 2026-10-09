"""Sprint 4 API: ORCA endpoints (behaviour, validation), RBAC on every new
mutation, and graceful 503s when the live runtime is not running (the test
app runs with the simulator/runtime disabled)."""
import pytest

from tests.conftest import _make_user, auth_headers


@pytest.fixture()
def analyst_user(db, organization):
    return _make_user(db, organization, "ANALYST")


def test_orca_step_returns_safe_velocities(client, observer_user):
    headers = auth_headers(client, observer_user.email)
    body = {
        "drones": [
            {"drone_id": "DMX-001", "x": -50, "y": 0, "vx": 10, "vy": 0, "preferred_vx": 10, "preferred_vy": 0},
            {"drone_id": "DMX-002", "x": 50, "y": 0, "vx": -10, "vy": 0, "preferred_vx": -10, "preferred_vy": 0},
        ]
    }
    resp = client.post("/orca/step", headers=headers, json=body)
    assert resp.status_code == 200
    data = resp.json()
    by_id = {d["drone_id"]: d for d in data["drones"]}
    a, b = by_id["DMX-001"], by_id["DMX-002"]
    assert a["collision_predicted"] and a["neighbors"] == ["DMX-002"]
    assert a["predictions"][0]["with"] == "DMX-002" and a["predictions"][0]["time_to_collision_s"] == pytest.approx(4.7, abs=0.01)
    assert a["safe_velocity"]["y"] != 0 and (a["safe_velocity"]["y"] > 0) != (b["safe_velocity"]["y"] > 0)
    assert a["avoidance_vector"]["y"] == pytest.approx(a["safe_velocity"]["y"])
    assert len(a["constraints"]) == 1 and data["computation_time_ms"] >= 0
    assert [e["kind"] for e in data["events"]][:1] == ["conflict_predicted"]


def test_orca_step_validation(client, observer_user):
    headers = auth_headers(client, observer_user.email)
    dup = {"drones": [{"drone_id": "A", "x": 0, "y": 0}, {"drone_id": "A", "x": 5, "y": 0}]}
    assert client.post("/orca/step", headers=headers, json=dup).status_code == 422
    assert client.post("/orca/step", headers=headers, json={"drones": []}).status_code == 422
    assert client.post("/orca/step", headers=headers, json={"drones": [{"drone_id": "A", "x": 0, "y": 0, "radius": -1}]}).status_code == 422


def test_orca_simulate_named_and_custom(client, analyst_user):
    headers = auth_headers(client, analyst_user.email)
    named = client.post("/orca/simulate", headers=headers, json={"scenario": "run:3", "max_time_s": 90})
    assert named.status_code == 200
    summary = named.json()["summary"]
    assert summary["agents"] == 8 and summary["collision_violations"] == 0 and summary["arrived"] == 8
    custom = client.post("/orca/simulate", headers=headers, json={"drones": [
        {"drone_id": "A", "x": -60, "y": 0, "goal_x": 60, "goal_y": 0},
        {"drone_id": "B", "x": 60, "y": 0, "goal_x": -60, "goal_y": 0},
    ]})
    data = custom.json()
    assert data["summary"]["min_separation_m"] >= 5.95 and data["trajectories"]["A"]
    assert client.post("/orca/simulate", headers=headers, json={"scenario": "nope"}).status_code == 422
    assert client.post("/orca/simulate", headers=headers, json={}).status_code == 422


LIVE_READS = ["/orca/state", "/orca/events", "/orca/demo", "/comms/links", "/comms/events", "/messaging/status"]


def test_live_endpoints_503_when_runtime_not_running(client, observer_user):
    headers = auth_headers(client, observer_user.email)
    for path in LIVE_READS:
        assert client.get(path, headers=headers).status_code == 503, path


@pytest.mark.parametrize("role_fixture", ["observer_user", "analyst_user"])
def test_read_only_roles_cannot_operate(client, request, role_fixture, sample_drone):
    headers = auth_headers(client, request.getfixturevalue(role_fixture).email)
    mutations = [
        ("/orca/demo/start", None), ("/orca/demo/stop", None),
        ("/orca/command", {"drone_id": str(sample_drone.id), "command": "RTH"}),
        (f"/comms/{sample_drone.id}/degrade", {"link": "wifi", "rssi_dbm": -90}),
        (f"/comms/{sample_drone.id}/restore", None),
    ]
    for path, body in mutations:
        assert client.post(path, headers=headers, json=body).status_code == 403, path


def test_operators_pass_rbac_then_hit_runtime_check(client, operator_user, sample_drone):
    headers = auth_headers(client, operator_user.email)
    assert client.post("/orca/demo/start", headers=headers).status_code == 503
    assert client.post(f"/comms/{sample_drone.id}/degrade", headers=headers, json={"link": "wifi", "rssi_dbm": -90}).status_code == 503


def test_unauthenticated_requests_rejected(client, sample_drone):
    for path in LIVE_READS + ["/telemetry/fleet", "/telemetry/stats", f"/telemetry/drones/{sample_drone.id}"]:
        assert client.get(path).status_code == 401, path
    assert client.post("/orca/step", json={"drones": [{"drone_id": "A", "x": 0, "y": 0}]}).status_code == 401
    assert client.post("/messaging/replay", json={}).status_code == 401


def test_telemetry_endpoints_validate_and_scope(client, operator_user, sample_drone, db, organization):
    headers = auth_headers(client, operator_user.email)
    resp = client.get(f"/telemetry/drones/{sample_drone.id}?minutes=60", headers=headers)
    assert resp.status_code == 200 and resp.json()["rows"] == 0 and "query_ms" in resp.json()
    assert client.get("/telemetry/fleet?minutes=60", headers=headers).json()["columns"][:3] == ["timestamp_ms", "drone_id", "lat"]
    bad = client.get("/telemetry/fleet?start=2026-10-06T10:00:00Z&end=2026-10-06T09:00:00Z", headers=headers)
    assert bad.status_code == 422
    import uuid

    assert client.get(f"/telemetry/drones/{uuid.uuid4()}", headers=headers).status_code == 404


def test_telemetry_stats_are_organization_scoped(client, engine, operator_user, organization):
    """Rows from another organization's drones must never appear."""
    import uuid
    from datetime import datetime, timezone

    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    from app.models import Drone, Organization

    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        other = Organization(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        s.add(other)
        s.flush()
        foreign = Drone(organization_id=other.id, name="FOREIGN-1", model="M30", status="active", battery=90)
        s.add(foreign)
        s.flush()
        s.execute(text("INSERT INTO drone_telemetry (timestamp, drone_id, lat, lon, alt, speed, heading, soc) VALUES (:t, :d, 23.2, 72.6, 50, 5, 10, 80)"),
                  {"t": datetime.now(timezone.utc), "d": foreign.id})
        s.commit()
        other_id = other.id
    try:
        stats = client.get("/telemetry/stats?minutes=5", headers=auth_headers(client, operator_user.email)).json()
        assert all(row["name"] != "FOREIGN-1" for row in stats["per_drone"])
    finally:
        with Session() as s:
            s.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": other_id})
            s.commit()


def test_fleet_reset_is_admin_only(client, operator_user, admin_user):
    assert client.post("/orca/fleet/reset", headers=auth_headers(client, operator_user.email)).status_code == 403
    assert client.post("/orca/fleet/reset").status_code == 401
    assert client.post("/orca/fleet/reset", headers=auth_headers(client, admin_user.email)).status_code == 503  # runtime off in tests


def test_live_event_message_and_replay_endpoints_are_organization_scoped(client, operator_user, sample_drone, db):
    """The ORCA/comms buffers and the NATS stream are shared by every organization;
    the endpoints must only return the caller's drones. The runtime is a stand-in
    for the in-memory buffers -- the filtering under test is the real route code."""
    from types import SimpleNamespace

    from app.main import app
    from app.messaging.bus import DroneMessage
    from app.models import Drone
    from app.services.orca.models import OrcaEvent

    sample_drone.uin = "DRN-MINE-1"
    db.commit()
    mine, foreign = str(sample_drone.id), "00000000-0000-0000-0000-00000000f0f0"

    def message(uin: str, seq: int) -> DroneMessage:
        return DroneMessage(subject=f"drone.{uin}.telemetry", uin=uin, kind="telemetry", payload={"lat": 1}, stream_seq=seq,
                            num_delivered=1, published_ns=None, received_ns=0, link="wifi", msg_id=None)

    async def bus_status():
        return {"connected": True}

    no_uin = Drone(organization_id=sample_drone.organization_id, name="NO-UIN", model="M30", status="active", battery=90)
    db.add(no_uin)
    db.commit()

    async def bus_replay(**_):  # a drone without a UIN is published under its id
        return [message("DRN-MINE-1", 1), message("DRN-FOREIGN-9", 2), message(str(no_uin.id), 3)]

    events = [OrcaEvent(1, "conflict_predicted", 1.0, "t", [mine]), OrcaEvent(2, "conflict_predicted", 1.0, "t", [mine, foreign]),
              OrcaEvent(3, "avoidance_applied", 1.0, "t", [foreign])]
    runtime = SimpleNamespace(
        running=True,
        orca=SimpleNamespace(engine=SimpleNamespace(recent_events=lambda since, limit: events), event_dict=lambda e: e.as_dict()),
        comms=SimpleNamespace(events=[{"drone_id": mine, "kind": "fallback"}, {"drone_id": foreign, "kind": "fallback"}],
                              switches=[{"drone_id": foreign, "from": "wifi", "to": "lte"}]),
        bus=SimpleNamespace(status=bus_status, replay=bus_replay),
        messaging_view=lambda: {"recent": [{"uin": "DRN-MINE-1"}, {"uin": "DRN-FOREIGN-9"}],
                                "commands": [{"command": "RTH", "uin": "DRN-FOREIGN-9"}]},
    )
    previous = getattr(app.state, "runtime", None)
    app.state.runtime = runtime
    try:
        headers = auth_headers(client, operator_user.email)
        assert [e["seq"] for e in client.get("/orca/events", headers=headers).json()] == [1]
        comms = client.get("/comms/events", headers=headers).json()
        assert [e["drone_id"] for e in comms["events"]] == [mine] and comms["switches"] == []
        status_ = client.get("/messaging/status", headers=headers).json()
        assert [m["uin"] for m in status_["recent"]] == ["DRN-MINE-1"] and status_["commands"] == []
        replay = client.post("/messaging/replay", headers=headers, json={"minutes": 5}).json()
        assert [m["stream_seq"] for m in replay["messages"]] == [1, 3]
        assert client.post("/messaging/replay", headers=headers, json={"uin": "DRN-FOREIGN-9"}).status_code == 404
    finally:
        app.state.runtime = previous
