"""Guided setup (Drone Setup page) against practice vehicles started like new
flight controllers, plus SiK radio pairing against a simulated radio."""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from hwbridge import radio, server
from hwbridge.tools.practice_vehicle import PracticeCopter

SLOTS = (41, 42)  # tcp 6160 / 6170: away from a running practice session


@pytest.fixture(scope="module")
def bridge(tmp_path_factory):
    vehicles = []
    for slot in SLOTS:
        v = PracticeCopter(slot, 23.24 + 0.0001 * slot, 72.65, fresh=True)
        threading.Thread(target=v.run, daemon=True).start()
        vehicles.append(v)
    cfg = tmp_path_factory.mktemp("bridge") / "config.json"
    server.CONFIG.clear()
    server.CONFIG.update(server.load_config(str(cfg)))
    server.CONFIG["nats_url"] = "nats://127.0.0.1:1"  # no NATS needed here
    server.state["links"] = []
    with TestClient(server.app) as client:
        client.headers["X-Bridge-Token"] = server.CONFIG["token"]
        yield client, vehicles, cfg
    server.state["links"] = []


def wait_for(fn, timeout=12.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = fn()
        if out:
            return out
        time.sleep(0.2)
    raise AssertionError("condition not met in time")


def controllers(client):
    return {c["fc_id"]: c for c in client.get("/fc").json()["controllers"]}


def test_first_start_creates_config_and_token_file(bridge):
    client, _, cfg = bridge
    assert cfg.exists()
    assert server.token_file(cfg).read_text() == server.CONFIG["token"]
    assert len(server.CONFIG["token"]) >= 24
    assert client.get("/fc", headers={"X-Bridge-Token": "wrong"}).status_code == 401


def test_connect_without_editing_config(bridge):
    client, _, cfg = bridge
    for slot in SLOTS:
        r = client.post("/links", json={"endpoint": f"tcp:127.0.0.1:{5760 + 10 * (slot - 1)}", "baud": 0})
        assert r.status_code == 200, r.text
    assert client.post("/links", json={"endpoint": "tcp:127.0.0.1:6160"}).status_code == 409
    assert client.post("/links", json={"endpoint": "rm -rf /"}).status_code == 422
    found = wait_for(lambda: (c := controllers(client)) and len(c) == 2 and c)
    assert set(found) == {"tcp6160-1", "tcp6170-1"}  # new flight controllers all use system id 1
    assert '"tcp:127.0.0.1:6170"' in cfg.read_text()


def test_unique_system_id(bridge):
    client, _, _ = bridge
    view = client.get("/fc/tcp6170-1/setup", params={"refresh": True}).json()
    assert view["params"]["SYSID_THISMAV"] == 1 and view["other_system_ids"] == [1]
    r = client.post("/fc/tcp6170-1/sysid", json={"system_id": 2})
    assert r.status_code == 200 and r.json()["new_fc_id"] == "tcp6170-2"
    wait_for(lambda: "tcp6170-2" in controllers(client) and "tcp6170-1" not in controllers(client))
    r = client.post("/fc/tcp6160-1/sysid", json={"system_id": 2})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SYSID_IN_USE"
    wait_for(lambda: controllers(client)["tcp6170-2"]["heartbeat_age_s"] is not None)
    assert client.post("/fc/tcp6170-2/params", json={"values": {"SYSID_THISMAV": 9}}).status_code == 422


def test_parameters_are_confirmed(bridge):
    client, _, _ = bridge
    r = client.post("/fc/tcp6160-1/params", json={"values": {"FS_GCS_ENABLE": 1, "FENCE_ENABLE": 1, "FRAME_CLASS": 1}})
    assert r.status_code == 200, r.text
    assert r.json()["confirmed"] == {"FS_GCS_ENABLE": 1.0, "FENCE_ENABLE": 1.0, "FRAME_CLASS": 1.0}
    r = client.post("/fc/tcp6160-1/params", json={"values": {"NO_SUCH_PARAM": 1}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PARAM_NOT_SET"
    r = client.post("/fc/tcp6160-1/params", json={"values": {"bad name;": 1}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "BAD_PARAM"


def test_accelerometer_six_positions(bridge):
    client, _, _ = bridge
    r = client.post("/fc/tcp6160-1/calibration/accel-start")
    assert r.status_code == 200 and r.json()["accelcal"]["state"] in ("starting", "waiting")
    seen = []
    for _ in range(6):
        cal = wait_for(lambda: (a := client.get("/fc/tcp6160-1/setup").json()["accelcal"]) and a["state"] in ("waiting", "success") and a)
        if cal["state"] == "success":
            break
        seen.append(cal["position"])
        client.post("/fc/tcp6160-1/calibration/accel-next")
        time.sleep(0.3)
    assert seen == ["LEVEL", "LEFT", "RIGHT", "NOSE DOWN", "NOSE UP", "BACK"]
    wait_for(lambda: client.get("/fc/tcp6160-1/setup").json()["accelcal"]["state"] == "success")
    assert client.post("/fc/tcp6160-1/calibration/accel-next").status_code == 409  # not waiting any more


def test_compass_calibration(bridge):
    client, _, _ = bridge
    assert client.post("/fc/tcp6160-1/calibration/compass-start").status_code == 200
    wait_for(lambda: client.get("/fc/tcp6160-1/setup").json()["magcal"].get("0", {}).get("status") == "SUCCESS")
    view = client.get("/fc/tcp6160-1/setup", params={"refresh": True}).json()
    assert view["params"]["COMPASS_OFS_X"] != 0


def test_motor_test_needs_propellers_off(bridge):
    client, _, _ = bridge
    assert client.post("/fc/tcp6160-1/motor-test", json={"motor": 1, "propellers_removed": False}).status_code == 422
    assert client.post("/fc/tcp6160-1/motor-test", json={"motor": 1, "throttle_pct": 50, "propellers_removed": True}).status_code == 422
    r = client.post("/fc/tcp6160-1/motor-test", json={"motor": 1, "propellers_removed": True})
    assert r.status_code == 200 and r.json()["result"] == "ACCEPTED"


def test_setup_refused_while_armed(bridge):
    client, vehicles, _ = bridge
    v = vehicles[0]
    with v.lock:
        v.armed = True
    try:
        wait_for(lambda: controllers(client)["tcp6160-1"]["armed"])
        r = client.post("/fc/tcp6160-1/params", json={"values": {"FENCE_ENABLE": 0}})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "ARMED"
        assert client.post("/fc/tcp6160-1/reboot").status_code == 409
    finally:
        with v.lock:
            v.armed = False


def test_cameras_and_links(bridge):
    client, _, cfg = bridge
    assert client.put("/cameras/2", json={"url": "rtsp://192.168.1.101:8554/live"}).status_code == 200
    assert client.put("/cameras/2", json={"url": "file:///etc/passwd"}).status_code == 422
    assert client.get("/video/sources").json()["hardware_cameras"][0]["fc_id"] == "tcp6170-2"
    assert client.delete("/cameras/2").status_code == 200
    assert client.delete("/links/tcp6170").status_code == 200
    assert "6170" not in cfg.read_text()


# ------------------------------------------------------------ SiK radios


class FakeSik:
    """Answers the AT commands of a SiK radio pair (ground + air)."""

    def __init__(self, remote=True):
        self.local = {"NETID": "25"}
        self.remote = {"NETID": "25"} if remote else None
        self.out = b""
        self.log = []

    def reset_input_buffer(self):
        self.out = b""

    def write(self, data: bytes):
        cmd = data.decode().strip()
        self.log.append(cmd)
        if cmd == "+++":
            reply = "OK"
        elif cmd == "ATI":
            reply = "SiK 2.2 on HM-TRP"
        elif cmd == "ATI5":
            reply = f"S3:NETID={self.local['NETID']}\r\nS1:SERIAL_SPEED=57"
        elif cmd == "RTI5":
            reply = f"S3:NETID={self.remote['NETID']}" if self.remote else ""
        elif cmd.startswith("ATS3="):
            self.local["NETID"] = cmd[5:]
            reply = "OK"
        elif cmd.startswith("RTS3=") and self.remote:
            self.remote["NETID"] = cmd[5:]
            reply = "OK"
        elif cmd in ("AT&W", "AT", "ATO") or (cmd == "RT&W" and self.remote):
            reply = "OK"
        else:
            reply = ""
        self.out = (reply + "\r\n").encode() if reply else b""

    def read(self, n):
        chunk, self.out = self.out[:n], self.out[n:]
        return chunk

    def close(self):
        pass


@pytest.fixture
def fake_radio(monkeypatch):
    monkeypatch.setattr(radio, "GUARD_S", 0.0)
    holder = {}

    def open_(port, baud):
        return holder["radio"]
    monkeypatch.setattr(radio, "_open", open_)
    return holder


def test_radio_read_and_set_netid(fake_radio):
    fake_radio["radio"] = r = FakeSik()
    info = radio.read_info("COM5")
    assert info["local"]["NETID"] == "25" and info["remote_reachable"]
    out = radio.set_netid("COM5", 26)
    assert out["netid"] == 26 and r.local["NETID"] == "26" and r.remote["NETID"] == "26"
    assert r.log.index("RTS3=26") < r.log.index("ATS3=26")  # air radio first


def test_radio_unreachable_air_radio_changes_nothing(fake_radio):
    fake_radio["radio"] = r = FakeSik(remote=False)
    with pytest.raises(radio.RadioError, match="air radio did not answer"):
        radio.set_netid("COM5", 30)
    assert r.local["NETID"] == "25" and not any(c.startswith("ATS3=") for c in r.log)
    with pytest.raises(radio.RadioError):
        radio.set_netid("COM5", 900)
