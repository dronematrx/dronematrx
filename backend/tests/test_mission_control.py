"""Sprint 6 mission control: API, persistence, PostGIS and RBAC tests.

The simulation host and the fleet runtime are replaced by fakes that record
calls and answer in their real wire format (FakeMcSimhost answers like
simhost + PX4 COMMAND_ACK). They never claim to be PX4: PX4 SITL behaviour is
covered by test_mc_sitl_integration.py (marked `gazebo`). No test reaches
hardware: the hardware adapter is locked and never configured here.
"""
from __future__ import annotations

import asyncio
import base64
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.drone import Drone
from app.models.geofence import Geofence
from app.models.mission_control import Detection, DroneCommand, FlightControllerBinding, VideoSource
from app.models.user import User
from app.schemas.telemetry import TelemetryUpdate
from app.services.mission_control import MissionControl
from app.services.mission_control import detections as detsvc
from app.services.mission_control import qr as qrsvc
from app.services.mission_control import video as videosvc
from app.services.mission_control.commands import CommandService
from app.services.simulation.simhost import SimhostError
from tests.conftest import _make_user, auth_headers

LAT, LON = 23.2400, 72.6500
SID = "bench-abc123"
FC = [f"FC-abc123-0{i}" for i in (1, 2, 3)]
PNG_1PX = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de0000000c4944415408d763f8cfc000000301010018dd8db00000000049454e44ae426082")).decode()


# ------------------------------------------------------------------ fakes


class FakeMcSimhost:
    """simhost bench endpoints. `health_for` holds per-controller pre-arm health."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.up = True
        self.sessions_state = {SID: "ready"}
        self.bound: dict[str, dict] = {}
        self.ack = 0
        self.health_for: dict[str, dict] = {}
        self.test_sources: list[int] = []

    def _check(self):
        if not self.up:
            raise SimhostError("Simulation host unreachable: ConnectError")

    def sessions(self):
        self._check()
        return dict(self.sessions_state)

    def _fc(self, fc):
        b = self.bound.get(fc)
        i = int(fc[-2:]) - 1
        return {"fc_id": fc, "index": i, "api_port": 14540 + i, "system_id": i + 1, "endpoint": f"udp:127.0.0.1:{14540 + i}",
                "firmware": "PX4 v1.15.4", "heartbeat_age_s": 0.4, "bound": b is not None, "drone_id": b["drone_id"] if b else None,
                "uin": b["uin"] if b else None, "mode": "HOLD", "armed": False, "closed": False}

    def flight_controllers(self, sid):
        self._check()
        return {"session_id": sid, "controllers": [self._fc(f) for f in FC] if self.sessions_state.get(sid) in ("ready", "running") else []}

    def bind_fc(self, sid, fc, drone_id, uin, name):
        self._check()
        self.calls.append(("bind", sid, fc, drone_id))
        self.bound[fc] = {"drone_id": drone_id, "uin": uin}
        return self._fc(fc)

    def unbind_fc(self, sid, fc):
        self.calls.append(("unbind", sid, fc))
        self.bound.pop(fc, None)
        return self._fc(fc)

    def prearm(self, sid, fc):
        self._check()
        base = {**self._fc(fc), "sensors_present": 0x20802D | 0x200000, "sensors_enabled": 0x20802D | 0x200000,
                "sensors_health": 0x20802D | 0x200000 | 0x10000000, "gps_fix": 3, "satellites": 10, "home_set": True, "battery_pct": 100.0,
                "landed_state": 1, "last_rx_age_s": 0.1, "statustext": [], "failsafe_params": {"NAV_DLL_ACT": 2, "COM_LOW_BAT_ACT": 3, "GF_ACTION": 2}}
        return {**base, **self.health_for.get(fc, {})}

    def command(self, sid, fc, command, params=None):
        self._check()
        self.calls.append(("command", fc, command, params))
        return {"uin": fc, "command": command, "result": self.ack, "timing": {"handler_ms": 4.0, "recv_to_send_ms": 0.05, "send_to_ack_ms": 3.9}}

    def video_sources(self):
        self._check()
        return {"test_patterns": [{"path": f"test/{n:02d}", "number": n, "running": True} for n in self.test_sources],
                "sitl_cameras": [{"path": f"sitl/{f}", "fc_id": f, "running": True} for f in FC]}

    def start_test_sources(self, count):
        self.test_sources = list(range(1, count + 1))
        return self.video_sources()

    def control_test_source(self, n, action):
        self.calls.append(("testsrc", n, action))
        return {"number": n, "running": action == "start"}

    def clock(self):
        import time
        return {"epoch_ms": time.time() * 1000 + 12.5}

    def stop(self, sid):
        self.sessions_state[sid] = "stopped"
        return {"sitl_stopped": 3}


class FakeConnections:
    def __init__(self):
        self.sent: list[dict] = []

    async def broadcast_json(self, payload, organization_id=None):
        self.sent.append(payload)


@dataclass
class KinState:
    drone_id: str


@dataclass
class FakeRuntime:
    running: bool = True
    nats_ok: bool = False
    message_listeners: list = field(default_factory=list)
    sent: list = field(default_factory=list)
    simulator: object = None
    telemetry_manager: object = None

    async def send_command(self, drone_id, command, issued_by):
        self.sent.append((drone_id, command))
        return {"command": command}


@dataclass
class _Sim:
    states: list


@dataclass
class _TM:
    latest: dict


def telemetry(drone: Drone, **kw) -> TelemetryUpdate:
    base = dict(drone_id=str(drone.id), name=drone.name, uin=drone.uin, latitude=LAT, longitude=LON, altitude=0.0, battery=100.0,
                heading=0.0, speed=0.0, flight_mode="HOLD", gps_status="3D_FIX", armed=False, link_quality=100.0, status="online",
                timestamp=datetime.now(timezone.utc))
    base.update(kw)
    return TelemetryUpdate(**base)


@pytest.fixture()
def mc(client, db, organization, monkeypatch):
    """Mission control wired to the test transaction, a fake simhost and a fake runtime."""
    fake = FakeMcSimhost()
    runtime = FakeRuntime(simulator=_Sim([]), telemetry_manager=_TM({}))
    m = MissionControl(sessionmaker(bind=db.get_bind()), FakeConnections(), runtime, client=fake)
    m.commands.loop = None  # watchers are driven explicitly in tests
    old_mc, old_rt = getattr(client.app.state, "mc", None), getattr(client.app.state, "runtime", None)
    client.app.state.mc, client.app.state.runtime = m, runtime
    monkeypatch.setattr(get_settings(), "FC_HARDWARE_ENDPOINTS", "udp:192.168.1.50:14550")
    # The bench operating area, as start_bench would register it.
    k = 0.003
    db.add(Geofence(organization_id=organization.id, name="Bench field", type="inclusion",
                    geometry=f"SRID=4326;POLYGON(({LON - k} {LAT - k}, {LON + k} {LAT - k}, {LON + k} {LAT + k}, {LON - k} {LAT + k}, {LON - k} {LAT - k}))"))
    db.commit()
    yield m, fake, runtime
    client.app.state.mc, client.app.state.runtime = old_mc, old_rt


def sim_drone(db, org, name="SIM-01", simulated=True) -> Drone:
    d = Drone(organization_id=org.id, name=name, uin=f"{name}-{uuid.uuid4().hex[:6]}", model="x500", firmware="PX4 v1.15", status="online",
              simulated=simulated, active=True)
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def bind(client, h, drone, fc=FC[0]):
    return client.post("/mission-control/fc/bind", headers=h, json={"drone_id": str(drone.id), "connection_type": "sitl",
                                                                   "endpoint": f"sitl:{SID}:{fc}"})


def live(runtime, drone, **kw):
    runtime.telemetry_manager.latest[str(drone.id)] = telemetry(drone, **kw)


@pytest.fixture()
def op(client, operator_user):
    return auth_headers(client, operator_user.email)


# ================================================================ dashboard


def test_fleet_sources_staleness_and_summary(client, db, organization, mc, op):
    m, fake, runtime = mc
    demo = sim_drone(db, organization, "DMX-1", simulated=False)
    sitl = sim_drone(db, organization, "SIM-01")
    stale = sim_drone(db, organization, "SIM-02")
    sim_drone(db, organization, "SIM-03")  # no source at all
    runtime.simulator.states.append(KinState(str(demo.id)))
    live(runtime, demo, flight_mode="LOITER", status="active")
    assert bind(client, op, sitl).json()["state"] == "bound"
    live(runtime, sitl)
    live(runtime, stale, timestamp=datetime.now(timezone.utc) - timedelta(seconds=30))
    r = client.get("/mission-control/fleet", headers=op)
    assert r.status_code == 200
    by = {d["name"]: d for d in r.json()["drones"]}
    assert by["DMX-1"]["telemetry_source"] == "kinematic_demo" and by["DMX-1"]["capabilities"] == ["HOLD", "RESUME", "RTL"]
    assert by["SIM-01"]["telemetry_source"] == "sitl" and by["SIM-01"]["connection_state"] == "connected" and "ARM" in by["SIM-01"]["capabilities"]
    assert by["SIM-02"]["connection_state"] == "stale"
    assert by["SIM-03"]["telemetry_source"] == "none" and by["SIM-03"]["connection_state"] == "no_telemetry"
    s = r.json()["summary"]
    assert s["connected"] == 2 and s["stale"] == 1 and s["by_source"]["sitl"] == 1 and s["by_source"]["kinematic_demo"] == 1
    assert r.json()["stale_after_s"] == get_settings().MC_TELEMETRY_STALE_S


def test_out_of_order_telemetry_is_dropped():
    from app.services.telemetry.manager import TelemetryManager

    tm = TelemetryManager(provider=None, connection_manager=FakeConnections(), session_factory=lambda: None)
    tm._persist = lambda u: None
    now = datetime.now(timezone.utc)
    newer = TelemetryUpdate(drone_id="d", latitude=1, longitude=1, altitude=1, battery=1, heading=0, speed=0, flight_mode="HOLD",
                            gps_status="3D_FIX", armed=False, link_quality=1, status="online", timestamp=now)
    older = newer.model_copy(update={"timestamp": now - timedelta(seconds=2), "latitude": 9})
    asyncio.run(tm._on_update(newer))
    asyncio.run(tm._on_update(older))
    assert tm.latest["d"].latitude == 1 and tm.dropped_out_of_order == 1


def test_dashboard_auth_missing_drone_and_overview(client, db, organization, mc, op, observer_user):
    assert client.get("/mission-control/fleet").status_code == 401
    assert client.get(f"/mission-control/drones/{uuid.uuid4()}", headers=op).status_code == 404
    assert client.get("/mission-control/drones/not-a-uuid", headers=op).status_code == 422
    obs = auth_headers(client, observer_user.email)
    assert client.get("/mission-control/overview", headers=obs).status_code == 200
    m, fake, _ = mc
    fake.up = False
    ov = client.get("/mission-control/overview", headers=obs).json()
    assert ov["simhost"].startswith("unavailable")


# ================================================================ binding


def test_discovery_scope_sitl_and_hardware_allow_list(client, mc, op):
    d = client.post("/mission-control/fc/discover", headers=op).json()
    sitl = [c for c in d["controllers"] if c["connection_type"] == "sitl"]
    hw = [c for c in d["controllers"] if c["connection_type"] == "udp"]
    assert [c["fc_id"] for c in sitl] == FC and all(c["firmware"] == "PX4 v1.15.4" and c["status"] == "connected" for c in sitl)
    assert hw and hw[0]["status"] == "not_probed" and hw[0]["simulation"] is False
    assert d["discovery_ms"] < 5000


def test_discovery_without_controllers_or_simhost(client, mc, op):
    m, fake, _ = mc
    fake.sessions_state = {}
    d = client.post("/mission-control/fc/discover", headers=op).json()
    assert [c for c in d["controllers"] if c["connection_type"] == "sitl"] == []
    fake.up = False
    d = client.post("/mission-control/fc/discover", headers=op).json()
    assert d["errors"] and "unreachable" in d["errors"][0]


def test_bind_persist_duplicate_and_unbind(client, db, organization, mc, op):
    m, fake, _ = mc
    a, b = sim_drone(db, organization, "SIM-01"), sim_drone(db, organization, "SIM-02")
    r = bind(client, op, a)
    assert r.status_code == 201 and r.json()["state"] == "bound" and r.json()["system_id"] == 1
    assert fake.calls[-1] == ("bind", SID, FC[0], str(a.id))
    # Persisted (a new session sees it) and audited.
    with sessionmaker(bind=db.get_bind())() as s2:
        row = s2.execute(select(FlightControllerBinding).where(FlightControllerBinding.drone_id == a.id)).scalars().one()
        assert row.state == "bound" and row.bound_via == "discovery"
    assert bind(client, op, b).json()["code"] == "FC_ALREADY_BOUND"          # same controller twice
    assert bind(client, op, a, FC[1]).json()["code"] == "DRONE_ALREADY_BOUND"  # drone already has one
    disc = client.post("/mission-control/fc/discover", headers=op).json()
    assert next(c for c in disc["controllers"] if c["fc_id"] == FC[0])["bound_drone_name"] == "SIM-01"
    bid = r.json()["id"]
    u = client.post(f"/mission-control/fc/bindings/{bid}/unbind", headers=op).json()
    assert u["state"] == "unbound" and ("unbind", SID, FC[0]) in fake.calls
    assert bind(client, op, b).json()["state"] == "bound"  # controller free again
    actions = [a.action for a in db.execute(select(AuditLog).where(AuditLog.action.in_(["fc_bind", "fc_unbind"]))).scalars()]
    assert actions.count("fc_bind") == 2 and actions.count("fc_unbind") == 1


def test_bind_validation_and_mode_separation(client, db, organization, mc, op):
    real = sim_drone(db, organization, "DMX-9", simulated=False)
    sim = sim_drone(db, organization, "SIM-07")
    assert bind(client, op, real).json()["code"] == "REAL_DRONE_SITL"
    r = client.post("/mission-control/fc/bind", headers=op, json={"drone_id": str(sim.id), "connection_type": "sitl", "endpoint": "sitl:bench-zz:../../etc"})
    assert r.status_code == 422 and r.json()["code"] == "INVALID_ENDPOINT"
    r = client.post("/mission-control/fc/bind", headers=op, json={"drone_id": str(real.id), "connection_type": "udp", "endpoint": "udp:10.0.0.9:14550"})
    assert r.status_code == 403 and r.json()["code"] == "ENDPOINT_NOT_APPROVED"
    r = client.post("/mission-control/fc/bind", headers=op, json={"drone_id": str(real.id), "connection_type": "udp", "endpoint": "udp:10.0.0.9;rm -rf:1"})
    assert r.status_code == 422
    # Approved hardware endpoint: the binding is recorded but cannot connect (adapter locked).
    r = client.post("/mission-control/fc/bind", headers=op, json={"drone_id": str(real.id), "connection_type": "udp", "endpoint": "udp:192.168.1.50:14550", "via": "manual"})
    assert r.status_code == 201 and r.json()["state"] == "connection_failed" and "HARDWARE_UNAVAILABLE" in r.json()["error"]


def test_bind_failure_reconnect_and_reconcile(client, db, organization, mc, op):
    m, fake, _ = mc
    d = sim_drone(db, organization)
    fake.up = False
    r = bind(client, op, d)
    assert r.status_code == 201 and r.json()["state"] == "connection_failed"
    fake.up = True
    rc = client.post(f"/mission-control/fc/bindings/{r.json()['id']}/reconnect", headers=op).json()
    assert rc["state"] == "bound"
    # The bench disappears (simhost restart): reconcile marks the binding failed.
    fake.sessions_state = {}
    from app.services.mission_control import fc as fcsvc
    changed = fcsvc.reconcile(db, fake)
    assert changed["failed"] == 1
    assert db.get(FlightControllerBinding, uuid.UUID(r.json()["id"])).state == "connection_failed"


def test_qr_payload_rules():
    good = qrsvc.build(f"sitl:{SID}:{FC[0]}", "sitl", 1, FC[0])
    assert qrsvc.parse(good)["fc"] == f"sitl:{SID}:{FC[0]}"
    cases = {
        "": "QR_EMPTY", "hello": "QR_NOT_DRONE_MATRX", "DMXFC2:{}": "QR_UNSUPPORTED_VERSION", "DMXFC1:{oops": "QR_MALFORMED",
        'DMXFC1:{"v":2,"fc":"sitl:x","proto":"mavlink2","conn":"sitl"}': "QR_UNSUPPORTED_VERSION",
        'DMXFC1:{"v":1,"fc":"sitl:x","proto":"mavlink2","conn":"sitl","password":"hunter2"}': "QR_CONTAINS_SECRET",
        'DMXFC1:{"v":1,"fc":"sitl:x","proto":"mavlink2","conn":"sitl","api_token":"t"}': "QR_CONTAINS_SECRET",
        'DMXFC1:{"v":1,"fc":"sitl:x","proto":"mavlink2","conn":"sitl","extra":1}': "QR_UNKNOWN_FIELDS",
        'DMXFC1:{"v":1,"fc":"ftp:x","proto":"mavlink2","conn":"sitl"}': "QR_INVALID_FC",
        'DMXFC1:{"v":1,"fc":"udp:x","proto":"mavlink2","conn":"sitl"}': "QR_INVALID_CONN",
        'DMXFC1:{"v":1,"fc":"sitl:x","proto":"mavlink2","conn":"sitl","sysid":300}': "QR_INVALID_SYSID",
        "DMXFC1:" + "x" * 600: "QR_TOO_LONG",
    }
    for text, code in cases.items():
        with pytest.raises(qrsvc.QrError) as e:
            qrsvc.parse(text)
        assert e.value.code == code, text


def test_qr_validate_and_bind_api(client, db, organization, mc, op):
    d = sim_drone(db, organization)
    text = client.get(f"/mission-control/fc/qr?fc_identity=sitl:{SID}:{FC[1]}&conn=sitl&sysid=2&ref={FC[1]}", headers=op).json()["qr_text"]
    v = client.post("/mission-control/fc/qr/validate", headers=op, json={"qr_text": text}).json()
    assert v["bindable"] and v["discovered"]["system_id"] == 2
    bad = client.post("/mission-control/fc/qr/validate", headers=op, json={"qr_text": text.replace('"v":1', '"v":1,"secret":"x"')})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "QR_CONTAINS_SECRET"
    b = client.post("/mission-control/fc/bind-qr", headers=op, json={"qr_text": text, "drone_id": str(d.id)}).json()
    assert b["state"] == "bound" and b["bound_via"] == "qr" and b["fc_identity"] == f"sitl:{SID}:{FC[1]}"


# ================================================================ pre-arm


def _prearm(client, h, d, mode="sitl"):
    return client.get(f"/mission-control/drones/{d.id}/prearm?expected_mode={mode}", headers=h).json()


def test_prearm_all_pass(client, db, organization, mc, op):
    m, fake, runtime = mc
    d = sim_drone(db, organization)
    bind(client, op, d)
    live(runtime, d)
    p = _prearm(client, op, d)
    assert p["can_arm"], p["blocking"]
    assert {c["id"] for c in p["checks"] if c["status"] == "PASS"} >= {"fc_connected", "telemetry_fresh", "identity", "flight_mode", "on_ground",
                                                                       "battery", "positioning", "estimator", "firmware_prearm", "operating_area",
                                                                       "failsafes", "mode_confirmed"}


@pytest.mark.parametrize("health,check,expect", [
    ({"battery_pct": 12.0}, "battery", "FAIL"),
    ({"mode": "RTL"}, "flight_mode", "FAIL"),
    ({"sensors_health": 0x802D | 0x10000000}, "estimator", "FAIL"),           # AHRS bit (0x200000) cleared
    ({"sensors_health": 0x20802D | 0x200000}, "firmware_prearm", "FAIL"),     # PX4 prearm bit cleared
    ({"sensors_present": None, "sensors_health": None}, "estimator", "UNKNOWN"),
    ({"gps_fix": 1}, "positioning", "FAIL"),
    ({"failsafe_params": {"NAV_DLL_ACT": 0, "COM_LOW_BAT_ACT": 3}}, "failsafes", "FAIL"),
    ({"failsafe_params": {}}, "failsafes", "UNKNOWN"),
    ({"heartbeat_age_s": 30.0}, "fc_connected", "FAIL"),
])
def test_prearm_failures_block(client, db, organization, mc, op, health, check, expect):
    m, fake, runtime = mc
    d = sim_drone(db, organization)
    bind(client, op, d)
    live(runtime, d)
    fake.health_for[FC[0]] = health
    p = _prearm(client, op, d)
    assert next(c for c in p["checks"] if c["id"] == check)["status"] == expect
    assert not p["can_arm"] and check in p["blocking"]


def test_prearm_stale_unbound_mode_and_nfz(client, db, organization, mc, op):
    m, fake, runtime = mc
    d = sim_drone(db, organization)
    p = _prearm(client, op, d)  # nothing bound
    assert not p["can_arm"] and next(c for c in p["checks"] if c["id"] == "fc_connected")["status"] == "FAIL"
    bind(client, op, d)
    live(runtime, d, timestamp=datetime.now(timezone.utc) - timedelta(seconds=20))
    assert "telemetry_fresh" in _prearm(client, op, d)["blocking"]
    live(runtime, d)
    assert "mode_confirmed" in _prearm(client, op, d, "hardware")["blocking"]
    k = 0.0005
    db.add(Geofence(organization_id=organization.id, name="Test NFZ", type="nfz",
                    geometry=f"SRID=4326;POLYGON(({LON - k} {LAT - k}, {LON + k} {LAT - k}, {LON + k} {LAT + k}, {LON - k} {LAT + k}, {LON - k} {LAT - k}))"))
    db.commit()
    p = _prearm(client, op, d)
    assert next(c for c in p["checks"] if c["id"] == "operating_area")["status"] == "FAIL" and "Test NFZ" in p["checks"][9]["detail"]


# ================================================================ commands


def cmd(client, h, d, command, params=None, mode="sitl", key=None):
    body = {"command": command, "params": params or {}, "confirm_mode": mode}
    if key:
        body["idempotency_key"] = key
    return client.post(f"/mission-control/drones/{d.id}/commands", headers=h, json=body)


def flying(runtime, d, **kw):
    live(runtime, d, **{"armed": True, "altitude": 20.0, "flight_mode": "MISSION", **kw})


def test_arm_backend_enforces_prearm(client, db, organization, mc, op):
    m, fake, runtime = mc
    d = sim_drone(db, organization)
    bind(client, op, d)
    live(runtime, d)
    fake.health_for[FC[0]] = {"battery_pct": 10.0}
    r = cmd(client, op, d, "ARM")
    assert r.status_code == 409 and r.json()["code"] == "PREARM_FAILED" and "battery" in r.json()["prearm"]["blocking"]
    assert not any(c[0] == "command" for c in fake.calls)  # nothing reached the controller
    assert db.execute(select(DroneCommand).where(DroneCommand.drone_id == d.id)).scalars().one().state == "rejected"
    fake.health_for = {}
    r = cmd(client, op, d, "ARM")
    assert r.status_code == 202 and r.json()["state"] == "accepted" and ("command", FC[0], "arm_once", {}) in fake.calls
    t = r.json()["timings"]
    assert t["ack_kind"] == "mavlink_command_ack" and t["fc_ack_ms"] == 3.9 and t["request_to_fc_dispatch_ms"] > 0


def test_arm_requires_mode_confirmation(client, db, organization, mc, op):
    m, fake, runtime = mc
    d = sim_drone(db, organization)
    bind(client, op, d)
    live(runtime, d)
    r = cmd(client, op, d, "ARM", mode=None)
    assert r.status_code == 409 and r.json()["code"] == "MODE_NOT_CONFIRMED"


def test_hold_rtl_goto_and_zone_rejection(client, db, organization, mc, op):
    m, fake, runtime = mc
    d = sim_drone(db, organization)
    bind(client, op, d)
    flying(runtime, d)
    assert cmd(client, op, d, "HOLD").json()["state"] == "accepted"
    assert cmd(client, op, d, "RTL").json()["state"] == "accepted"
    ok = cmd(client, op, d, "GOTO", {"latitude": LAT + 0.0005, "longitude": LON, "altitude_m": 25})
    assert ok.status_code == 202 and fake.calls[-1][2] == "goto"
    assert cmd(client, op, d, "GOTO", {"latitude": 95, "longitude": LON}).status_code == 422
    assert cmd(client, op, d, "GOTO", {"latitude": LAT + 0.0005, "longitude": LON, "altitude_m": 500}).status_code == 422
    out = cmd(client, op, d, "GOTO", {"latitude": LAT + 0.01, "longitude": LON, "altitude_m": 25})  # outside the inclusion field
    assert out.status_code == 409 and out.json()["code"] == "ZONE_VIOLATION"
    k = 0.0002
    db.add(Geofence(organization_id=organization.id, name="Pad NFZ", type="nfz",
                    geometry=f"SRID=4326;POLYGON(({LON - k} {LAT + 0.0010}, {LON + k} {LAT + 0.0010}, {LON + k} {LAT + 0.0014}, {LON - k} {LAT + 0.0014}, {LON - k} {LAT + 0.0010}))"))
    db.commit()
    nfz = cmd(client, op, d, "GOTO", {"latitude": LAT + 0.0012, "longitude": LON, "altitude_m": 25})
    assert nfz.status_code == 409 and "Pad NFZ" in nfz.json()["detail"]
    across = cmd(client, op, d, "GOTO", {"latitude": LAT + 0.0020, "longitude": LON, "altitude_m": 25})  # path crosses it
    assert across.status_code == 409 and "path crosses Pad NFZ" in across.json()["detail"]


def test_invalid_state_offline_missing_and_rbac(client, db, organization, mc, op, observer_user):
    m, fake, runtime = mc
    d = sim_drone(db, organization)
    bind(client, op, d)
    live(runtime, d)  # on the ground
    assert cmd(client, op, d, "HOLD").json()["code"] == "INVALID_STATE"
    live(runtime, d, timestamp=datetime.now(timezone.utc) - timedelta(seconds=60))
    assert cmd(client, op, d, "HOLD").json()["code"] == "TELEMETRY_STALE"
    assert client.post(f"/mission-control/drones/{uuid.uuid4()}/commands", headers=op, json={"command": "HOLD"}).status_code == 404
    obs = auth_headers(client, observer_user.email)
    assert cmd(client, obs, d, "HOLD").status_code == 403
    assert client.post(f"/mission-control/drones/{d.id}/commands", json={"command": "HOLD"}).status_code == 401
    assert client.post("/mission-control/fc/discover", headers=obs).status_code == 403
    nobody = sim_drone(db, organization, "SIM-08")
    assert cmd(client, op, nobody, "HOLD").json()["code"] == "NO_FLIGHT_CONTROLLER"


def test_fc_rejection_duplicate_and_idempotency(client, db, organization, mc, op):
    m, fake, runtime = mc
    d = sim_drone(db, organization)
    bind(client, op, d)
    flying(runtime, d)
    fake.ack = 4  # MAV_RESULT_FAILED
    r = cmd(client, op, d, "HOLD")
    assert r.json()["state"] == "rejected" and "FAILED" in r.json()["failure_reason"]
    fake.ack = 0
    key = "idem-" + uuid.uuid4().hex
    first = cmd(client, op, d, "RTL", key=key)
    again = cmd(client, op, d, "RTL", key=key)
    assert again.json()["id"] == first.json()["id"] and again.json()["replayed"]
    assert sum(1 for c in fake.calls if c[0] == "command" and c[2] == "rtl") == 1
    dup = cmd(client, op, d, "RTL")  # same command still in flight
    assert dup.status_code == 409 and dup.json()["code"] == "DUPLICATE_COMMAND"
    other = cmd(client, op, d, "HOLD")  # a different command supersedes the in-flight one
    assert other.json()["state"] == "accepted"
    assert db.get(DroneCommand, uuid.UUID(first.json()["id"])).state == "cancelled"
    conflict = cmd(client, op, d, "HOLD", key=key)
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_confirmation_and_timeout_from_telemetry(client, db, organization, mc, op, monkeypatch):
    m, fake, runtime = mc
    d = sim_drone(db, organization)
    bind(client, op, d)
    flying(runtime, d)
    a = cmd(client, op, d, "HOLD").json()
    flying(runtime, d, flight_mode="HOLD")  # telemetry after the dispatch shows the commanded state
    asyncio.run(m.commands.watch(a["id"], 0.0))
    row = db.get(DroneCommand, uuid.UUID(a["id"]))
    db.refresh(row)
    assert row.state == "confirmed" and "observed_state_ms" in row.timings
    monkeypatch.setattr(get_settings(), "MC_COMMAND_CONFIRM_TIMEOUT_S", 0.3)
    b = cmd(client, op, d, "RTL").json()  # telemetry keeps saying HOLD
    asyncio.run(m.commands.watch(b["id"], 0.0))
    row = db.get(DroneCommand, uuid.UUID(b["id"]))
    db.refresh(row)
    assert row.state == "timed_out" and row.failure_reason


def test_kinematic_demo_adapter_and_unsupported(client, db, organization, mc, op):
    m, fake, runtime = mc
    d = sim_drone(db, organization, "DMX-2", simulated=False)
    runtime.simulator.states.append(KinState(str(d.id)))
    live(runtime, d, status="active", flight_mode="LOITER", armed=True)
    loop = asyncio.new_event_loop()
    import threading
    th = threading.Thread(target=loop.run_forever, daemon=True)
    th.start()
    m.commands.loop = loop
    try:
        r = cmd(client, op, d, "HOLD", mode="kinematic_demo")
        assert r.json()["state"] == "dispatched" and r.json()["timings"]["ack_kind"] == "nats_publish_ack"
        assert runtime.sent == [(str(d.id), "HOLD")]
        u = cmd(client, op, d, "GOTO", {"latitude": LAT, "longitude": LON}, mode="kinematic_demo")
        assert u.status_code == 409 and u.json()["code"] == "UNSUPPORTED" and u.json()["command"]["state"] == "unsupported"
    finally:
        m.commands.loop = None
        loop.call_soon_threadsafe(loop.stop)


def test_fleet_commands_preview_confirm_partial(client, db, organization, mc, op):
    m, fake, runtime = mc
    a, b, c = sim_drone(db, organization, "SIM-01"), sim_drone(db, organization, "SIM-02"), sim_drone(db, organization, "SIM-03")
    bind(client, op, a, FC[0])
    bind(client, op, b, FC[1])
    flying(runtime, a)
    flying(runtime, b)
    live(runtime, c, timestamp=datetime.now(timezone.utc) - timedelta(seconds=90))  # offline
    p = client.post("/mission-control/fleet-commands/preview", headers=op, json={"fleet_command": "HOLD_ALL"}).json()
    assert sorted(t["name"] for t in p["targets"]) == ["SIM-01", "SIM-02"]
    assert any(e["name"] == "SIM-03" for e in p["excluded"])
    targets = [t["drone_id"] for t in p["targets"]]
    body = {"fleet_command": "HOLD_ALL", "expected_targets": targets, "confirm": False, "idempotency_key": "fleet-" + uuid.uuid4().hex}
    assert client.post("/mission-control/fleet-commands", headers=op, json=body).status_code == 409  # not confirmed: nothing sent
    assert not any(x[0] == "command" for x in fake.calls)
    stale_view = {**body, "confirm": True, "expected_targets": targets[:1]}
    assert client.post("/mission-control/fleet-commands", headers=op, json=stale_view).json()["code"] == "TARGETS_CHANGED"
    original = fake.command

    def flaky(sid, fc, command, params=None):  # SIM-02's controller rejects
        if fc == FC[1]:
            return {**original(sid, fc, command, params), "result": 1}
        return original(sid, fc, command, params)

    fake.command = flaky
    r = client.post("/mission-control/fleet-commands", headers=op, json={**body, "confirm": True}).json()
    states = {x["drone_name"]: x["state"] for x in r["results"]}
    assert states == {"SIM-01": "accepted", "SIM-02": "rejected"} and r["summary"]["rejected"] == 1
    assert len({x["fleet_command_id"] for x in r["results"]}) == 1
    replay = client.post("/mission-control/fleet-commands", headers=op, json={**body, "confirm": True}).json()
    assert replay["replayed"] and replay["fleet_command_id"] == r["fleet_command_id"]
    land = client.post("/mission-control/fleet-commands", headers=op, json={**body, "fleet_command": "LAND_ALL", "confirm": True,
                                                                           "idempotency_key": "land-" + uuid.uuid4().hex})
    assert land.json()["code"] in ("CONFIRMATION_REQUIRED", "TARGETS_CHANGED")
    audit = db.execute(select(AuditLog).where(AuditLog.action.like("command_%"))).scalars().all()
    assert any(x.event_metadata.get("fleet_command_id") == r["fleet_command_id"] for x in audit)


def test_assign_task(client, db, organization, mc, op, admin_user):
    from tests.sim_support import plan_sim_mission
    from app.models.mission_planning import MissionTask

    h = auth_headers(client, admin_user.email)
    mid = plan_sim_mission(client, h, drones=2)
    task = db.execute(select(MissionTask).where(MissionTask.mission_id == uuid.UUID(mid))).scalars().first()
    other = sim_drone(db, organization, "SIM-11")
    r = client.post(f"/mission-control/drones/{other.id}/assign-task", headers=op, json={"mission_id": mid, "task_key": task.task_key})
    assert r.status_code == 200 and r.json()["changed"] and r.json()["drone_id"] == str(other.id)
    assert client.post(f"/mission-control/drones/{other.id}/assign-task", headers=op, json={"mission_id": mid, "task_key": "T999"}).status_code == 404


# ================================================================ detections


def ingest(db, drone, **kw):
    ev = {"event_id": "ev-" + uuid.uuid4().hex, "drone_id": str(drone.id), "provider": "test_fixture", "label": "person", "confidence": 0.8,
          "inferred_at": datetime.now(timezone.utc).isoformat(), "thumbnail_png_b64": PNG_1PX, "latitude": LAT, "longitude": LON}
    ev.update(kw)
    return detsvc.ingest(db, ev), ev


def test_ingest_validation_and_idempotency(db, organization):
    d = sim_drone(db, organization)
    (row, created, err), ev = ingest(db, d)
    assert created and err is None and row.thumbnail_type == "image/png" and row.latitude == LAT and row.accepted_location is None
    (row2, created2, _), _ = ingest(db, d, event_id=ev["event_id"])
    assert row2.id == row.id and not created2  # duplicate event: one row
    (bad, _, _), _ = ingest(db, d, latitude=123.0, longitude=LON)
    assert bad.latitude is None and "invalid coordinates" in bad.location_error
    (nogps, _, _), _ = ingest(db, d, latitude=None, longitude=None, location_error="fixture without GPS")
    assert nogps.latitude is None and nogps.location_error == "fixture without GPS"
    (nothumb, _, _), _ = ingest(db, d, thumbnail_png_b64=base64.b64encode(b"not an image").decode())
    assert nothumb.thumbnail is None and "PNG or JPEG" in nothumb.details["thumbnail_error"]
    (_, _, e), _ = ingest(db, d, confidence=1.7)
    assert e and "confidence" in e
    (_, _, e), _ = ingest(db, d, drone_id="not-a-drone")
    assert e


def test_review_accept_reject_postgis_and_audit(client, db, organization, mc, op, observer_user, monkeypatch):
    d = sim_drone(db, organization)
    (a, _, _), _ = ingest(db, d)
    (b, _, _), _ = ingest(db, d, latitude=LAT + 0.0003)
    (c, _, _), _ = ingest(db, d, latitude=None, longitude=None)
    r = client.post(f"/detections/{a.id}/review", headers=op, json={"decision": "accepted"})
    assert r.status_code == 200 and r.json()["review_status"] == "accepted" and r.json()["changed"]
    again = client.post(f"/detections/{a.id}/review", headers=op, json={"decision": "accepted"}).json()
    assert not again["changed"]  # idempotent
    flip = client.post(f"/detections/{a.id}/review", headers=op, json={"decision": "rejected"})
    assert flip.status_code == 409 and flip.json()["code"] == "ALREADY_REVIEWED"
    rj = client.post(f"/detections/{b.id}/review", headers=op, json={"decision": "rejected", "reason": "shadow"}).json()
    assert rj["reject_reason"] == "shadow"
    client.post(f"/detections/{c.id}/review", headers=op, json={"decision": "accepted"})  # no location -> no geometry
    db.expire_all()
    from sqlalchemy import func
    wkt = db.execute(select(func.ST_AsText(Detection.accepted_location), func.ST_SRID(Detection.accepted_location)).where(Detection.id == a.id)).one()
    assert wkt[0] == f"POINT({LON} {LAT})" and wkt[1] == 4326
    assert db.get(Detection, c.id).accepted_location is None and db.get(Detection, b.id).accepted_location is None
    gj = client.get("/detections/accepted.geojson", headers=op).json()
    assert [f["properties"]["id"] for f in gj["features"]] == [str(a.id)]  # rejected and location-less excluded
    audit = db.execute(select(AuditLog).where(AuditLog.resource == "detection")).scalars().all()
    assert sorted(x.action for x in audit) == ["detection_accepted", "detection_accepted", "detection_rejected"]
    assert client.get(f"/detections/{a.id}/thumbnail", headers=op).headers["content-type"] == "image/png"
    obs = auth_headers(client, observer_user.email)
    (z, _, _), _ = ingest(db, d)
    assert client.post(f"/detections/{z.id}/review", headers=obs, json={"decision": "accepted"}).status_code == 403
    analyst = _make_user(db, organization, "ANALYST")
    assert client.post(f"/detections/{z.id}/review", headers=auth_headers(client, analyst.email), json={"decision": "rejected"}).status_code == 200


def test_review_is_atomic(client, db, organization, mc, op, monkeypatch):
    d = sim_drone(db, organization)
    (row, _, _), _ = ingest(db, d)

    class Boom(Exception):
        pass

    def broken(*a, **k):
        raise Boom("audit write failed")

    monkeypatch.setattr(detsvc, "AuditLog", broken)
    reviewer = db.execute(select(User).where(User.organization_id == organization.id)).scalars().first()
    savepoint = db.begin_nested()  # stands in for the request's transaction
    with pytest.raises(Boom):
        detsvc.review(db, reviewer, row.id, "accepted", None)
    savepoint.rollback()  # what the request session does on the error: nothing was committed
    fresh = db.get(Detection, row.id)
    db.refresh(fresh)
    assert fresh.review_status == "pending" and fresh.accepted_location is None


def test_heatmap_counts_cells_and_filters(client, db, organization, mc, op):
    d = sim_drone(db, organization)
    ids = []
    for dlat in (0.0, 0.0, 0.0, 0.002):  # three separate events at one spot, one ~220 m away
        (r, _, _), _ = ingest(db, d, latitude=LAT + dlat)
        ids.append(r.id)
    (rej, _, _), _ = ingest(db, d)
    for i in ids:
        client.post(f"/detections/{i}/review", headers=op, json={"decision": "accepted"})
    client.post(f"/detections/{rej.id}/review", headers=op, json={"decision": "rejected"})
    h = client.get("/detections/heatmap?cell_m=20", headers=op).json()
    assert h["total"] == 4 and h["max"] == 3 and len(h["cells"]) == 2
    big = client.get("/detections/heatmap?cell_m=500", headers=op).json()
    assert big["total"] == 4
    filtered = client.get(f"/detections/heatmap?mission_id={uuid.uuid4()}", headers=op).json()
    assert filtered["total"] == 0


def test_detection_bus_message_and_fixtures(client, db, organization, mc, admin_user):
    m, fake, runtime = mc
    d = sim_drone(db, organization)
    events = detsvc.fixture_events(d, 3, (LAT, LON), 0, "valid")
    assert all(e["provider"] == "test_fixture" for e in events)

    @dataclass
    class Msg:
        kind: str
        payload: dict

    out = asyncio.run(m.detections.handle(events[0]))
    assert out["test_fixture"] and m.connections.sent[-1]["type"] == "detection"
    asyncio.run(m.detections.on_bus_message(Msg("detection", events[0])))  # redelivery: no second card
    assert m.detections.stats == {"ingested": 1, "duplicates": 1, "dropped": 0}
    h = auth_headers(client, admin_user.email)
    r = client.post("/detections/test-fixtures", headers=h, json={"drone_id": str(d.id), "count": 2, "location": "missing"})
    assert r.status_code == 202 and r.json()["via"].startswith("direct")
    rows = client.get("/detections?status=pending", headers=h).json()
    assert sum(1 for x in rows if x["location_error"] == "fixture without GPS") == 2


def test_fixture_endpoint_admin_only_and_switch(client, db, organization, mc, op, admin_user, monkeypatch):
    d = sim_drone(db, organization)
    assert client.post("/detections/test-fixtures", headers=op, json={"drone_id": str(d.id)}).status_code == 403
    monkeypatch.setattr(get_settings(), "DETECTION_TEST_FIXTURES", False)
    h = auth_headers(client, admin_user.email)
    assert client.post("/detections/test-fixtures", headers=h, json={"drone_id": str(d.id)}).status_code == 403


# ================================================================ video


def test_video_feed_registry_and_status(client, db, organization, mc, op, monkeypatch):
    m, fake, _ = mc
    d = sim_drone(db, organization)
    bind(client, op, d)
    fake.test_sources = [1, 2]
    monkeypatch.setattr(videosvc, "relay_paths", lambda: {"test/01/hi": {"ready": True, "tracks": ["H264"], "readers": []},
                                                          f"sitl/{FC[0]}/hi": {"ready": True, "tracks": ["H264"], "readers": []}})
    f = client.get("/video/feeds", headers=op).json()
    by = {x["path"]: x for x in f["feeds"]}
    assert by["test/01/hi"]["status"] == "live" and by["test/02/hi"]["status"] == "source_unavailable"
    assert by["test/01/hi"]["live_label"] == "TEST PATTERN" and "not a drone camera" in by["test/01/hi"]["label"]
    assert by[f"sitl/{FC[0]}/hi"]["drone_name"] == d.name and by[f"sitl/{FC[0]}/hi"]["live_label"] == "SITL CAMERA (simulated)"
    assert f["camera_configured"]
    monkeypatch.setattr(videosvc, "relay_paths", lambda: None)
    assert all(x["status"] == "relay_unavailable" for x in client.get("/video/feeds", headers=op).json()["feeds"])
    fake.test_sources = [1]
    f2 = client.get("/video/feeds", headers=op).json()
    assert "test/02/hi" not in {x["path"] for x in f2["feeds"]} and f2["sync"]["retired"] == 1


def test_whep_proxy_errors_recording_rbac_and_clock(client, db, organization, mc, op, observer_user, monkeypatch):
    m, fake, _ = mc
    fake.test_sources = [1]
    client.get("/video/feeds", headers=op)
    feed = db.execute(select(VideoSource).where(VideoSource.path == "test/01/hi")).scalars().one()

    class Resp:
        def __init__(self, code, text="", headers=None):
            self.status_code, self.text, self.headers = code, text, headers or {}

    calls = []

    def post(url, **kw):
        calls.append(url)
        return Resp(201, "v=0\r\nanswer", {"Location": "/test/01/hi/whep/abc"}) if "hi" in url else Resp(404, "no stream")

    monkeypatch.setattr(videosvc._http, "post", post)
    ok = client.post(f"/video/feeds/{feed.id}/whep", headers=op, json={"sdp": "v=0\r\noffer", "rendition": "hi"})
    assert ok.status_code == 200 and ok.json()["sdp"].startswith("v=0") and calls[-1].endswith("/test/01/hi/whep")
    lo = client.post(f"/video/feeds/{feed.id}/whep", headers=op, json={"sdp": "v=0\r\noffer", "rendition": "lo"})
    assert lo.status_code == 404 and lo.json()["code"] == "SOURCE_UNAVAILABLE"
    assert client.post(f"/video/feeds/{feed.id}/whep", headers=op, json={"sdp": "not sdp"}).status_code == 422
    assert client.post(f"/video/feeds/{uuid.uuid4()}/whep", headers=op, json={"sdp": "v=0"}).status_code == 404
    assert client.post(f"/video/feeds/{feed.id}/whep", json={"sdp": "v=0"}).status_code == 401
    obs = auth_headers(client, observer_user.email)
    assert client.post(f"/video/feeds/{feed.id}/whep", headers=obs, json={"sdp": "v=0\r\n"}).status_code == 200  # viewing allowed
    assert client.post(f"/video/feeds/{feed.id}/recordings", headers=obs, json={"record": True}).status_code == 403
    assert client.post("/video/test-sources/1/stop", headers=obs).status_code == 403
    off = client.get("/video/clock-offset", headers=op).json()
    assert 5 < off["offset_ms"] < 25 and off["rtt_ms"] >= 0


def test_command_service_unit_matching():
    @dataclass
    class T:
        armed: bool = False
        altitude: float = 0
        latitude: float = LAT
        longitude: float = LON
        flight_mode: str = "HOLD"

    assert CommandService.matches({"armed": True}, T(armed=True))
    assert CommandService.matches({"flight_mode": ["RTL", "LAND"]}, T(flight_mode="LAND"))
    assert not CommandService.matches({"flight_mode": ["HOLD"]}, T(flight_mode="MISSION"))
    assert CommandService.matches({"within_m": 6.0, "latitude": LAT + 0.00003, "longitude": LON}, T())
    assert CommandService.matches({"flight_mode": ["LAND"], "or_disarmed": True}, T(armed=False, flight_mode="HOLD"))
    assert CommandService.matches({"min_altitude_m": 9.0}, T(altitude=12))


def test_freshness_uses_backend_receive_time(client, db, organization, mc, op):
    """A producer clock 30 s behind (e.g. a simulator VM after host sleep) must not
    make a live drone stale: freshness is the backend's receive time."""
    m, fake, runtime = mc
    d = sim_drone(db, organization)
    bind(client, op, d)
    flying(runtime, d, timestamp=datetime.now(timezone.utc) - timedelta(seconds=30))
    runtime.telemetry_manager.received_at = {str(d.id): datetime.now(timezone.utc)}
    row = next(x for x in client.get("/mission-control/fleet", headers=op).json()["drones"] if x["id"] == str(d.id))
    assert row["connection_state"] == "connected" and row["telemetry_age_s"] < 2
    assert cmd(client, op, d, "HOLD").json()["state"] == "accepted"
    # ... and a frame that *arrived* long ago is stale even if its own timestamp is "now".
    flying(runtime, d)
    runtime.telemetry_manager.received_at = {str(d.id): datetime.now(timezone.utc) - timedelta(seconds=30)}
    assert cmd(client, op, d, "RTL").json()["code"] == "TELEMETRY_STALE"


def test_websocket_fanout_serializes_and_closes_failed_clients():
    from app.websocket.connection_manager import ConnectionManager

    class WS:
        def __init__(self, fail=False):
            self.fail, self.sent, self.closed, self.active = fail, [], False, 0

        async def accept(self):
            pass

        async def send_text(self, m):
            self.active += 1
            assert self.active == 1, "concurrent send on one socket"
            await asyncio.sleep(0.001)
            self.active -= 1
            if self.fail:
                raise RuntimeError("broken pipe")
            self.sent.append(m)

        async def close(self, code=1000):
            self.closed = True

    async def go():
        cm = ConnectionManager()
        ok, bad = WS(), WS(fail=True)
        await cm.connect(ok, "org")
        await cm.connect(bad, "org")
        await asyncio.gather(*(cm.broadcast_json({"type": "t", "i": i}, organization_id="org") for i in range(20)))
        return cm, ok, bad

    cm, ok, bad = asyncio.run(go())
    assert len(ok.sent) == 20                      # every message, never two sends at once
    assert bad.closed and cm.active_count == 1     # the failed client is closed (it will reconnect), not silently dropped
