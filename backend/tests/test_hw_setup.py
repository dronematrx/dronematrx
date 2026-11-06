"""Guided real-drone setup (Drone Setup page) through the backend: roles,
parameter allow-list, audit, error mapping, token file, bridge start rules --
against a fake bridge that answers like hwbridge.server."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.services.mission_control import fc as fcsvc
from app.services.mission_control import hw_setup
from app.services.mission_control.hwbridge import HwBridgeClient, HwBridgeError
from tests.conftest import auth_headers

FC = "COM5-1"
BASE = "/mission-control/hardware"


class FakeSetupBridge:
    def __init__(self):
        self.calls: list[tuple] = []
        self.armed = False

    def health(self):
        return {"status": "ok", "links": []}

    def ports(self):
        return {"ports": [{"device": "COM5", "description": "Silicon Labs CP210x", "kind": "radio", "connected": False}]}

    def add_link(self, endpoint, baud):
        self.calls.append(("link", endpoint, baud))
        return {"endpoint": endpoint, "name": "COM5", "open": False}

    def remove_link(self, name):
        self.calls.append(("unlink", name))
        return {"removed": name}

    def setup(self, fc_id, refresh):
        if fc_id != FC:
            raise HwBridgeError(f"Flight controller {fc_id} not found", 404)
        return {"fc_id": fc_id, "params": {"SYSID_THISMAV": 1.0}, "accelcal": {"state": "idle"}, "magcal": {}}

    def set_params(self, fc_id, values):
        if self.armed:
            raise HwBridgeError("Setup is only allowed while the drone is disarmed on the ground", 409, "ARMED")
        self.calls.append(("params", fc_id, values))
        return {"fc_id": fc_id, "confirmed": values}

    def set_sysid(self, fc_id, system_id):
        self.calls.append(("sysid", fc_id, system_id))
        return {"previous": 1, "system_id": system_id, "new_fc_id": f"COM5-{system_id}"}

    def reboot(self, fc_id):
        self.calls.append(("reboot", fc_id))
        return {"result": "ACCEPTED"}

    def calibration(self, fc_id, action):
        self.calls.append(("cal", fc_id, action))
        return {"action": action, "accelcal": {"state": "waiting", "position": "LEVEL"}}

    def motor_test(self, fc_id, motor, throttle_pct, seconds):
        self.calls.append(("motor", fc_id, motor, throttle_pct, seconds))
        return {"result": "ACCEPTED"}

    def radio_info(self, port):
        return {"port": port, "local": {"NETID": "25"}, "remote_reachable": True, "suggested_netid": 26}

    def radio_netid(self, port, netid):
        self.calls.append(("netid", port, netid))
        return {"port": port, "netid": netid, "remote_was": "25"}

    def set_camera(self, sysid, url):
        self.calls.append(("camera", sysid, url))
        return {"system_id": sysid, "url": url}


@pytest.fixture()
def bridge(monkeypatch):
    fake = FakeSetupBridge()
    monkeypatch.setattr(fcsvc, "_hw", fake)
    return fake


@pytest.fixture()
def op(client, operator_user):
    return auth_headers(client, operator_user.email)


def audits(db, action):
    return db.scalars(select(AuditLog).where(AuditLog.action == f"hardware_setup.{action}")).all()


def test_observer_cannot_change_a_drone(client, observer_user, bridge):
    h = auth_headers(client, observer_user.email)
    assert client.get(f"{BASE}/bridge", headers=h).status_code == 200  # may look
    assert client.post(f"{BASE}/fc/{FC}/params", headers=h, json={"values": {"FS_GCS_ENABLE": 1}}).status_code == 403
    assert client.post(f"{BASE}/links", headers=h, json={"endpoint": "serial:COM5"}).status_code == 403
    assert client.post(f"{BASE}/bridge/start", headers=h).status_code == 403
    assert bridge.calls == []


def test_connect_and_setup_view(client, op, bridge, db):
    assert client.get(f"{BASE}/ports", headers=op).json()["ports"][0]["kind"] == "radio"
    assert client.post(f"{BASE}/links", headers=op, json={"endpoint": "serial:COM5", "baud": 0}).status_code == 200
    assert ("link", "serial:COM5", 0) in bridge.calls
    assert audits(db, "connect")
    assert client.get(f"{BASE}/fc/{FC}/setup", headers=op).json()["params"]["SYSID_THISMAV"] == 1.0
    r = client.get(f"{BASE}/fc/COM9-3/setup", headers=op)
    assert r.status_code == 404
    assert client.get(f"{BASE}/fc/..%2Fetc/setup", headers=op).status_code in (404, 422)


def test_parameter_allow_list_and_audit(client, op, bridge, db):
    values = {"FS_GCS_ENABLE": 1, "FS_GCS_TIMEOUT": 5, "BATT_FS_LOW_ACT": 2, "FENCE_ENABLE": 1, "RTL_ALT": 2000, "FLTMODE1": 2, "RC3_MIN": 1012}
    r = client.post(f"{BASE}/fc/{FC}/params", headers=op, json={"values": values})
    assert r.status_code == 200, r.text
    assert audits(db, "params")[0].event_metadata["values"]["RTL_ALT"] == 2000
    for refused in ("SYSID_THISMAV", "ARMING_CHECK", "INS_ACCOFFS_X", "SERIAL1_PROTOCOL"):
        r = client.post(f"{BASE}/fc/{FC}/params", headers=op, json={"values": {refused: 0}})
        assert r.status_code == 422 and r.json()["code"] == "PARAM_NOT_ALLOWED", refused
    assert client.post(f"{BASE}/fc/{FC}/params", headers=op, json={"values": {}}).json()["code"] == "NO_VALUES"


def test_bridge_refusals_keep_their_code(client, op, bridge):
    bridge.armed = True
    r = client.post(f"{BASE}/fc/{FC}/params", headers=op, json={"values": {"FENCE_ENABLE": 0}})
    assert r.status_code == 409 and r.json()["code"] == "ARMED"


def test_system_id_reboot_and_calibrations(client, op, bridge, db):
    assert client.post(f"{BASE}/fc/{FC}/sysid", headers=op, json={"system_id": 2}).json()["new_fc_id"] == "COM5-2"
    assert client.post(f"{BASE}/fc/{FC}/sysid", headers=op, json={"system_id": 255}).status_code == 422
    assert client.post(f"{BASE}/fc/{FC}/reboot", headers=op).status_code == 200
    for action in ("accel-start", "accel-next", "level", "compass-start", "compass-cancel"):
        assert client.post(f"{BASE}/fc/{FC}/calibration/{action}", headers=op).status_code == 200
    assert client.post(f"{BASE}/fc/{FC}/calibration/erase-all", headers=op).json()["code"] == "BAD_CALIBRATION"
    assert audits(db, "system_id") and audits(db, "reboot") and audits(db, "calibration.accel-start")
    assert not audits(db, "calibration.accel-next")  # each position is not an audit event


def test_motor_test_needs_propellers_off(client, op, bridge, db):
    url = f"{BASE}/fc/{FC}/motor-test"
    assert client.post(url, headers=op, json={"motor": 1, "propellers_removed": False}).status_code == 422
    assert client.post(url, headers=op, json={"motor": 1, "throttle_pct": 40, "propellers_removed": True}).status_code == 422
    assert client.post(url, headers=op, json={"motor": 1, "propellers_removed": True}).status_code == 200
    assert audits(db, "motor_test")[0].event_metadata["propellers_removed"] is True


def test_radio_pairing_and_camera(client, op, bridge, db):
    assert client.get(f"{BASE}/radio/COM5", headers=op).json()["suggested_netid"] == 26
    assert client.get(f"{BASE}/radio/LPT1", headers=op).status_code == 422
    assert client.post(f"{BASE}/radio/COM5/netid", headers=op, json={"netid": 26}).status_code == 200
    assert client.post(f"{BASE}/radio/COM5/netid", headers=op, json={"netid": 900}).status_code == 422
    r = client.put(f"{BASE}/cameras/2", headers=op, json={"url": "rtsp://admin:secret@192.168.1.101:554/stream1"})
    assert r.status_code == 200
    meta = audits(db, "camera")[0].event_metadata
    assert meta == {"host": "192.168.1.101"}  # the camera password is not written to the audit log


def test_unreachable_bridge_is_a_clear_error(client, op, monkeypatch):
    class Down(FakeSetupBridge):
        def ports(self):
            raise HwBridgeError("Hardware bridge unreachable at http://127.0.0.1:8110: ConnectError")
    monkeypatch.setattr(fcsvc, "_hw", Down())
    r = client.get(f"{BASE}/ports", headers=op)
    assert r.status_code == 503 and r.json()["code"] == "BRIDGE_UNAVAILABLE"


def test_token_is_read_from_the_bridge_token_file(tmp_path, monkeypatch):
    f = tmp_path / ".bridge_token"
    monkeypatch.setattr(get_settings(), "HWBRIDGE_TOKEN", "")
    monkeypatch.setattr(get_settings(), "HWBRIDGE_TOKEN_FILE", str(f))
    c = HwBridgeClient()
    assert c.token == ""  # bridge not started yet
    f.write_text("abc123\n")
    assert c.token == "abc123"
    assert c._http().headers["X-Bridge-Token"] == "abc123"
    f.write_text("rotated")
    assert c._http().headers["X-Bridge-Token"] == "rotated"
    monkeypatch.setattr(get_settings(), "HWBRIDGE_TOKEN", "fixed")
    assert HwBridgeClient().token == "fixed"


def test_bridge_is_only_started_on_this_laptop(client, op, monkeypatch):
    monkeypatch.setattr(get_settings(), "HWBRIDGE_URL", "http://10.0.0.9:8110")

    class Down(FakeSetupBridge):
        def health(self):
            raise HwBridgeError("unreachable")
    monkeypatch.setattr(fcsvc, "_hw", Down())
    assert client.get(f"{BASE}/bridge", headers=op).json()["can_start"] is False
    r = client.post(f"{BASE}/bridge/start", headers=op)
    assert r.status_code == 409 and r.json()["code"] == "CANNOT_START"
    assert client.post(f"{BASE}/bridge/stop", headers=op).json()["code"] == "NOT_STARTED_HERE"
    assert hw_setup.bridge_process.proc is None


# ------------------------------------------------------------- ORCA on real aircraft


class OrcaBridge(FakeSetupBridge):
    def __init__(self):
        super().__init__()
        self.orca_enabled = True

    def orca(self):
        return {"enabled": self.orca_enabled, "rate_hz": 10.0, "deadlock_detours": 0, "aircraft": [], "predictions": []}

    def set_orca(self, enabled):
        self.calls.append(("orca", enabled))
        self.orca_enabled = enabled
        return self.orca()


def test_orca_status_toggle_needs_operator_and_reason(client, observer_user, op, db, monkeypatch):
    fake = OrcaBridge()
    monkeypatch.setattr(fcsvc, "_hw", fake)
    obs = auth_headers(client, observer_user.email)
    assert client.get("/mission-control/hardware/orca", headers=obs).json()["enabled"] is True  # anyone may look
    assert client.post("/mission-control/hardware/orca", headers=obs, json={"enabled": False, "reason": "test"}).status_code == 403
    assert client.post("/mission-control/hardware/orca", headers=op, json={"enabled": False}).status_code == 422  # reason required
    r = client.post("/mission-control/hardware/orca", headers=op, json={"enabled": False, "reason": "bench test, props off"})
    assert r.status_code == 200 and r.json()["enabled"] is False and ("orca", False) in fake.calls
    row = db.scalars(select(AuditLog).where(AuditLog.action == "orca_disabled")).first()
    assert row is not None and row.event_metadata["reason"] == "bench test, props off"


def test_orca_status_when_bridge_down(client, op, monkeypatch):
    class Down(OrcaBridge):
        def orca(self):
            raise HwBridgeError("Hardware bridge unreachable")
    monkeypatch.setattr(fcsvc, "_hw", Down())
    assert client.get("/mission-control/hardware/orca", headers=op).json() == {"available": False, "error": "Hardware bridge unreachable"}
