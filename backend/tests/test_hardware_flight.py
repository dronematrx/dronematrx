"""Real aircraft (hardware bridge, ArduPilot): binding rules, the human flight
permission, pilot-override lockout, ArduPilot pre-arm, CBBA-free command
routing, hardware cameras -- against a fake hardware bridge that answers
like hwbridge.server."""
from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models.audit_log import AuditLog
from app.models.drone import Drone
from app.models.mission_control import DroneCommand, FlightPermission, VideoSource
from app.services.mission_control import fc as fcsvc
from app.services.mission_control import video as videosvc
from app.services.mission_control.hwbridge import HwBridgeError, parse_identity
from app.services.telemetry.simulator import SimulationTelemetryProvider
from tests.conftest import auth_headers
from tests.test_mission_control import LAT, LON, live, mc, op  # noqa: F401 - fixtures

IDENTITY = "serial:COM5/1"
FC_ID = "COM5-1"
ACKS = {"rc_pilot_ready": True, "area_clear": True, "observer_present": True, "legal_ok": True, "weather_ok": True}


class FakeHwBridge:
    def __init__(self):
        self.up = True
        self.bound: dict[str, dict] = {}
        self.authorized_until = 0.0
        self.override: dict | None = None
        self.rc_ok = True
        self.calls: list[tuple] = []
        self.health_extra: dict = {}

    def _check(self):
        if not self.up:
            raise HwBridgeError("Hardware bridge unreachable at http://127.0.0.1:8110: ConnectError")

    def _info(self):
        b = self.bound.get(FC_ID, {})
        return {"fc_id": FC_ID, "identity": IDENTITY, "endpoint": "serial:COM5", "system_id": 1, "firmware": "ArduPilot v4.7.2",
                "heartbeat_age_s": 0.3, "bound": bool(b), "drone_id": b.get("drone_id"), "uin": b.get("uin"), "mode": "STABILIZE",
                "armed": False, "closed": False, "override": self.override,
                "authorized_until": self.authorized_until if self.authorized_until > time.time() else None}

    def health(self):
        self._check()
        return {"status": "ok", "links": [{"endpoint": "serial:COM5", "open": True, "vehicles": [1]}]}

    def controllers(self):
        self._check()
        return [self._info()]

    def bind(self, fc_id, drone_id, uin, name):
        self._check()
        self.bound[fc_id] = {"drone_id": drone_id, "uin": uin}
        return self._info()

    def unbind(self, fc_id):
        self.bound.pop(fc_id, None)
        return self._info()

    def prearm(self, fc_id):
        self._check()
        bits = 0x200000 | 0x20 | 0x10000 | 0x10000000  # AHRS, GPS, RC receiver, pre-arm check
        health = bits if self.rc_ok else bits & ~0x10000
        return {**self._info(), "autopilot": "ardupilot", "sensors_present": bits, "sensors_enabled": bits, "sensors_health": health,
                "gps_fix": 3, "satellites": 14, "home_set": True, "battery_pct": 96.0, "battery_v": 16.6, "landed_state": 1,
                "rc_receiver_ok": self.rc_ok, "rc_rssi": 200, "statustext": [],
                "failsafe_params": {"FS_GCS_ENABLE": 1.0, "FS_THR_ENABLE": 1.0, "BATT_FS_LOW_ACT": 2.0, "FENCE_ENABLE": 1.0, "FENCE_ACTION": 1.0,
                                    "FENCE_ALT_MAX": 40.0, "FENCE_RADIUS": 200.0}}

    def command(self, fc_id, command, params=None):
        self._check()
        self.calls.append(("command", command, params))
        if command in ("arm", "takeoff", "goto", "mission_start") and time.time() >= self.authorized_until:
            raise HwBridgeError("No valid flight permission", 409, "FLIGHT_NOT_AUTHORIZED")
        return {"fc_id": fc_id, "command": command, "result": 0, "timing": {"handler_ms": 120.0, "recv_to_send_ms": 0.2, "send_to_ack_ms": 110.0}}

    def authorize(self, fc_id, until_epoch, by):
        self._check()
        self.calls.append(("authorize", until_epoch, by))
        self.authorized_until = until_epoch
        return self._info()

    def revoke(self, fc_id):
        self.calls.append(("revoke",))
        self.authorized_until = 0.0
        return self._info()

    def release_override(self, fc_id, by):
        self.calls.append(("release", by))
        self.override = None
        return self._info()

    def upload_mission(self, fc_id, items):
        self.calls.append(("mission", items))
        return {"uploaded": len(items)}

    def video_sources(self):
        self._check()
        b = self.bound.get(FC_ID, {})
        return {"hardware_cameras": [{"system_id": 1, "url": "rtsp://192.168.1.101:8554/live", "path": "hw/sys1", "fc_id": FC_ID,
                                      "identity": IDENTITY, "drone_id": b.get("drone_id")}]}


@pytest.fixture()
def hw(monkeypatch):
    fake = FakeHwBridge()
    monkeypatch.setattr(fcsvc, "_hw", fake)
    return fake


def hw_drone(db, org, name="HW-1", hardware=True) -> Drone:
    d = Drone(organization_id=org.id, name=name, uin=f"{name}-{uuid.uuid4().hex[:6]}", model="F450", firmware="ArduPilot", status="online",
              hardware=hardware, active=True)
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def bind_hw(client, h, drone):
    return client.post("/mission-control/fc/bind", headers=h, json={"drone_id": str(drone.id), "connection_type": "serial", "endpoint": IDENTITY})


def ready(client, db, organization, mc, op, hw):  # noqa: F811
    _, _, runtime = mc
    d = hw_drone(db, organization)
    assert bind_hw(client, op, d).json()["state"] == "bound"
    live(runtime, d, flight_mode="STABILIZE")
    return d, runtime


def cmd(client, h, d, command, **params):
    return client.post(f"/mission-control/drones/{d.id}/commands", headers=h,
                       json={"command": command, "params": params, "confirm_mode": "hardware", "idempotency_key": uuid.uuid4().hex})


# ----------------------------------------------------------------- tests


def test_identity_parsing():
    assert parse_identity("serial:COM5/1") == {"endpoint": "serial:COM5", "conn": "serial", "system_id": 1, "fc_id": "COM5-1"}
    assert parse_identity("udp:0.0.0.0:14550/2")["fc_id"] == "udp14550-2"
    assert parse_identity("tcp:127.0.0.1:5760/1")["conn"] == "udp"
    for bad in ("serial:COM5", "serial:COM5/0", "serial:COM5/999", "serial:COM5/1; rm -rf", "http://x/1"):
        assert parse_identity(bad) is None


def test_hardware_drone_is_never_driven_by_the_demo_simulator(client, db, organization, operator_user):
    h = auth_headers(client, operator_user.email)
    r = client.post("/drones", headers=h, json={"name": "HW-REAL", "model": "F450", "firmware": "ArduPilot", "hardware": True,
                                                "latitude": LAT, "longitude": LON})
    assert r.status_code == 201 and r.json()["hardware"] is True
    demo = client.post("/drones", headers=h, json={"name": "DEMO-X", "model": "M30", "latitude": LAT, "longitude": LON}).json()
    sim = SimulationTelemetryProvider(sessionmaker(bind=db.get_bind()))
    loaded = {s.drone_id for s in sim.load_states([r.json()["id"], demo["id"]])}
    assert r.json()["id"] not in loaded and demo["id"] in loaded
    assert r.json()["id"] not in sim.initial_states()


def test_hardware_binding_rules(client, db, organization, mc, op, hw):  # noqa: F811
    demo = hw_drone(db, organization, "DEMO-1", hardware=False)
    r = bind_hw(client, op, demo)
    assert r.status_code == 409 and r.json()["code"] == "NOT_HARDWARE_DRONE"
    disc = client.post("/mission-control/fc/discover", headers=op).json()
    c = next(x for x in disc["controllers"] if x["fc_identity"] == IDENTITY)
    assert c["simulation"] is False and c["connection_type"] == "serial" and "ArduPilot" in c["note"]
    real = hw_drone(db, organization)
    b = bind_hw(client, op, real).json()
    assert b["state"] == "bound" and b["system_id"] == 1 and hw.bound[FC_ID]["drone_id"] == str(real.id)
    fleet = {d["name"]: d for d in client.get("/mission-control/fleet", headers=op).json()["drones"]}
    assert fleet["HW-1"]["telemetry_source"] == "hardware" and fleet["HW-1"]["hardware"] is True
    assert set(fleet["HW-1"]["capabilities"]) >= {"ARM", "TAKEOFF", "HOLD", "RTL", "LAND", "GOTO", "MISSION_START"}
    hw.up = False  # bridge down -> binding fails clearly, nothing is pretended
    other = hw_drone(db, organization, "HW-2")
    hw.bound.clear()
    client.post(f"/mission-control/fc/bindings/{b['id']}/unbind", headers=op)
    r2 = bind_hw(client, op, other).json()
    assert r2["state"] == "connection_failed" and "unreachable" in r2["error"]


def test_ardupilot_prearm_includes_rc_override_and_permission(client, db, organization, mc, op, hw):  # noqa: F811
    d, _ = ready(client, db, organization, mc, op, hw)
    pre = client.get(f"/mission-control/drones/{d.id}/prearm?expected_mode=hardware", headers=op).json()
    by = {c["id"]: c for c in pre["checks"]}
    assert pre["can_arm"] is True, pre["blocking"]
    assert by["rc_link"]["status"] == "PASS" and by["pilot_override"]["status"] == "PASS"
    assert by["firmware_prearm"]["detail"].startswith("ArduPilot") and "GCS loss=1" in by["failsafes"]["detail"]
    assert by["flight_permission"]["status"] == "WARNING" and by["flight_permission"]["required"] is False
    hw.rc_ok = False
    pre = client.get(f"/mission-control/drones/{d.id}/prearm?expected_mode=hardware", headers=op).json()
    assert "rc_link" in pre["blocking"]
    hw.rc_ok, hw.override = True, {"reason": "mode changed GUIDED -> LOITER outside Drone MatrX"}
    pre = client.get(f"/mission-control/drones/{d.id}/prearm?expected_mode=hardware", headers=op).json()
    assert "pilot_override" in pre["blocking"]


def test_flight_needs_a_human_permission(client, db, organization, mc, op, hw, observer_user):  # noqa: F811
    d, _ = ready(client, db, organization, mc, op, hw)
    r = cmd(client, op, d, "ARM")
    assert r.status_code == 409 and r.json()["code"] == "FLIGHT_NOT_AUTHORIZED"
    assert not [c for c in hw.calls if c[0] == "command"]  # never reached the aircraft
    # Grant: every acknowledgement is required, and the observer may not grant.
    r = client.post(f"/mission-control/drones/{d.id}/flight-permission", headers=op, json={"minutes": 10, "acknowledgements": {**ACKS, "area_clear": False}})
    assert r.status_code == 422 and r.json()["code"] == "ACKNOWLEDGEMENTS_MISSING"
    obs = auth_headers(client, observer_user.email)
    assert client.post(f"/mission-control/drones/{d.id}/flight-permission", headers=obs, json={"minutes": 10, "acknowledgements": ACKS}).status_code == 403
    r = client.post(f"/mission-control/drones/{d.id}/flight-permission", headers=op, json={"minutes": 45, "acknowledgements": ACKS})
    assert r.status_code == 422  # longer than HW_FLIGHT_PERMISSION_MAX_MIN
    r = client.post(f"/mission-control/drones/{d.id}/flight-permission", headers=op, json={"minutes": 10, "acknowledgements": ACKS})
    assert r.status_code == 201 and r.json()["active"] and 590 <= r.json()["remaining_s"] <= 600
    assert hw.authorized_until > time.time() + 590  # mirrored on the bridge
    r = cmd(client, op, d, "ARM")
    assert r.status_code == 202 and r.json()["state"] == "accepted" and r.json()["adapter"] == "hardware_mavlink"
    assert ("command", "arm", {}) in hw.calls
    actions = {a.action for a in db.execute(select(AuditLog)).scalars()}
    assert {"flight_permission_granted", "command_requested"} <= actions
    # Revoke: flight commands are refused again.
    client.delete(f"/mission-control/drones/{d.id}/flight-permission?reason=done", headers=op)
    assert hw.authorized_until == 0.0
    assert cmd(client, op, d, "ARM").json()["code"] == "FLIGHT_NOT_AUTHORIZED"
    assert db.execute(select(FlightPermission)).scalars().one().revoked_at is not None


def test_permission_refused_while_prearm_fails(client, db, organization, mc, op, hw):  # noqa: F811
    d, _ = ready(client, db, organization, mc, op, hw)
    hw.rc_ok = False
    r = client.post(f"/mission-control/drones/{d.id}/flight-permission", headers=op, json={"minutes": 10, "acknowledgements": ACKS})
    assert r.status_code == 409 and r.json()["code"] == "PREARM_FAILED" and "rc_link" in r.json()["prearm"]["blocking"]


def test_pilot_override_locks_out_every_command_until_released(client, db, organization, mc, op, hw):  # noqa: F811
    d, runtime = ready(client, db, organization, mc, op, hw)
    live(runtime, d, flight_mode="LOITER", armed=True, altitude=12.0)
    hw.override = {"reason": "mode changed GUIDED -> LOITER outside Drone MatrX (RC mode switch)"}
    for c in ("HOLD", "RTL", "LAND"):
        r = cmd(client, op, d, c)
        assert r.status_code == 409 and r.json()["code"] == "PILOT_OVERRIDE", c
    assert not [c for c in hw.calls if c[0] == "command"]
    r = client.post(f"/mission-control/drones/{d.id}/override/release", headers=op, json={"reason": "no"})
    assert r.status_code == 422
    r = client.post(f"/mission-control/drones/{d.id}/override/release", headers=op, json={"reason": "pilot handed control back"})
    assert r.status_code == 200 and hw.override is None
    r = cmd(client, op, d, "HOLD")
    assert r.status_code == 202 and ("command", "hold", {}) in hw.calls
    row = db.execute(select(DroneCommand).where(DroneCommand.command == "HOLD", DroneCommand.state == "accepted")).scalars().one()
    assert row.expected_state == {"flight_mode": ["LOITER", "BRAKE", "POSHOLD"]}  # ArduPilot mode names
    assert "pilot_override_released" in {a.action for a in db.execute(select(AuditLog)).scalars()}


def test_hardware_camera_registered_for_the_bound_drone(client, db, organization, mc, op, hw, monkeypatch):  # noqa: F811
    d, _ = ready(client, db, organization, mc, op, hw)
    posted = []

    class _R:
        status_code, text = 200, ""

    monkeypatch.setattr(videosvc._http, "post", lambda url, **kw: posted.append((url, kw.get("json"))) or _R())
    m, fake, _ = mc
    out = videosvc.sync_sources(db, organization.id, fake)
    v = db.execute(select(VideoSource).where(VideoSource.kind == "hardware_camera")).scalars().one()
    assert v.path == "hw/sys1" and v.low_path is None and v.drone_id == d.id and v.label == f"Camera - {d.name}"
    assert posted[0][0].endswith("/v3/config/paths/add/hw/sys1") and posted[0][1]["source"] == "rtsp://192.168.1.101:8554/live"
    assert out["synced"] >= 1
