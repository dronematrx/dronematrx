"""Auto Mission: AOI + mission type + number of drones -> drone choice,
altitude separation, auto geofence, CBBA, one-approval launch, staggered
start, dropout detection and replacement -- against a fake bridge with
several real aircraft that answers like hwbridge.server."""
from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.drone import Drone
from app.models.geofence import Geofence
from app.models.mission import Mission
from app.models.mission_control import FlightPermission
from app.models.payload import Payload
from app.services import auto_mission as auto
from app.services.mission_control import fc as fcsvc
from app.services.mission_control.hwbridge import HwBridgeError
from tests.test_mission_control import LAT, LON, live, mc, op  # noqa: F401 - fixtures

ACKS = {"rc_pilot_ready": True, "area_clear": True, "observer_present": True, "legal_ok": True, "weather_ok": True}
K = 0.0012  # ~130 m AOI half-width around the field


def aoi(lat=LAT, lon=LON, k=K):
    return {"type": "Polygon", "coordinates": [[[lon - k, lat - k], [lon + k, lat - k], [lon + k, lat + k], [lon - k, lat + k], [lon - k, lat - k]]]}


class FleetBridge:
    """Several ArduPilot controllers on COM5 (system ids 1..n)."""

    def __init__(self, n):
        self.fcs = {f"COM5-{i}": {"bound": None, "authorized_until": 0.0, "override": None, "armed": False, "mode": "STABILIZE",
                                  "reached_max": -1, "mission": [], "heartbeat_age_s": 0.3} for i in range(1, n + 1)}
        self.calls: list[tuple] = []
        self.fail_prearm: set[str] = set()

    def _info(self, fc_id):
        f = self.fcs[fc_id]
        sysid = int(fc_id.split("-")[1])
        return {"fc_id": fc_id, "identity": f"serial:COM5/{sysid}", "endpoint": "serial:COM5", "system_id": sysid, "firmware": "ArduPilot v4.6.0",
                "heartbeat_age_s": f["heartbeat_age_s"], "bound": bool(f["bound"]), "drone_id": (f["bound"] or {}).get("drone_id"),
                "uin": (f["bound"] or {}).get("uin"), "mode": f["mode"], "armed": f["armed"], "closed": False, "override": f["override"],
                "authorized_until": f["authorized_until"] if f["authorized_until"] > time.time() else None,
                "mission_count": len(f["mission"]) + 1, "reached_max": f["reached_max"], "landed_state": 1}

    def health(self):
        return {"status": "ok", "links": [{"endpoint": "serial:COM5", "open": True, "vehicles": [1, 2, 3]}], "limits": {"max_alt_m": 30, "max_distance_m": 150}}

    def controllers(self):
        return [self._info(f) for f in self.fcs]

    def bind(self, fc_id, drone_id, uin, name):
        self.fcs[fc_id]["bound"] = {"drone_id": drone_id, "uin": uin}
        return self._info(fc_id)

    def unbind(self, fc_id):
        self.fcs[fc_id]["bound"] = None
        return self._info(fc_id)

    def prearm(self, fc_id):
        bits = 0x200000 | 0x20 | 0x10000 | 0x10000000
        bad = fc_id in self.fail_prearm
        return {**self._info(fc_id), "autopilot": "ardupilot", "sensors_present": bits, "sensors_enabled": bits,
                "sensors_health": bits & ~0x10000000 if bad else bits, "gps_fix": 3, "satellites": 14, "home_set": True,
                "home": (LAT, LON, 50.0), "battery_pct": 96.0, "battery_v": 16.6, "landed_state": 1, "rc_receiver_ok": True, "rc_rssi": 200,
                "statustext": [{"t": 0, "severity": 3, "text": "PreArm: Compass not calibrated"}] if bad else [],
                "failsafe_params": {"FS_GCS_ENABLE": 1.0, "FS_THR_ENABLE": 1.0, "BATT_FS_LOW_ACT": 2.0, "FENCE_ENABLE": 1.0, "FENCE_ACTION": 1.0,
                                    "FENCE_ALT_MAX": 40.0, "FENCE_RADIUS": 150.0}}

    def command(self, fc_id, command, params=None):
        self.calls.append(("command", fc_id, command))
        if command in ("arm", "takeoff", "goto", "mission_start") and time.time() >= self.fcs[fc_id]["authorized_until"]:
            raise HwBridgeError("No valid flight permission", 409, "FLIGHT_NOT_AUTHORIZED")
        if command == "mission_start":
            self.fcs[fc_id].update(armed=True, mode="AUTO")
        return {"fc_id": fc_id, "command": command, "result": 0, "timing": {"handler_ms": 1.0}}

    def authorize(self, fc_id, until_epoch, by):
        self.calls.append(("authorize", fc_id))
        self.fcs[fc_id]["authorized_until"] = until_epoch
        return self._info(fc_id)

    def revoke(self, fc_id):
        self.calls.append(("revoke", fc_id))
        self.fcs[fc_id]["authorized_until"] = 0.0
        return self._info(fc_id)

    def release_override(self, fc_id, by):
        self.fcs[fc_id]["override"] = None
        return self._info(fc_id)

    def upload_mission(self, fc_id, items):
        self.calls.append(("mission", fc_id, items))
        self.fcs[fc_id]["mission"] = items
        return {"uploaded": len(items)}

    def video_sources(self):
        return {"hardware_cameras": []}


@pytest.fixture()
def fleet(monkeypatch):
    fake = FleetBridge(3)
    monkeypatch.setattr(fcsvc, "_hw", fake)
    monkeypatch.setattr(auto, "STAGGER_S", 0.05)
    return fake


def real_drone(db, org, n, battery=95.0, payload="camera"):
    d = Drone(organization_id=org.id, name=f"HW-0{n}", uin=f"HW-0{n}-{uuid.uuid4().hex[:6]}", model="Quad (ArduCopter)", firmware="ArduPilot",
              status="online", hardware=True, active=True, battery=battery, gps_status="3D_FIX",
              location=f"SRID=4326;POINT({LON} {LAT + 0.0002 * n})")
    db.add(d)
    db.commit()
    db.refresh(d)
    if payload:
        db.add(Payload(drone_id=d.id, type=payload, name="Drone camera", status="calibrated"))
        db.commit()
    return d


def setup_fleet(client, db, organization, mc, op, n=3, battery=None):  # noqa: F811
    _, _, runtime = mc
    drones = []
    for i in range(1, n + 1):
        d = real_drone(db, organization, i, battery=(battery or {}).get(i, 95.0))
        r = client.post("/mission-control/fc/bind", headers=op, json={"drone_id": str(d.id), "connection_type": "serial", "endpoint": f"serial:COM5/{i}"})
        assert r.json()["state"] == "bound", r.text
        live(runtime, d, flight_mode="STABILIZE", battery=(battery or {}).get(i, 95.0), latitude=LAT + 0.0002 * i)
        drones.append(d)
    return drones, runtime


def plan(client, h, **kw):
    body = {"template": "survey", "aoi": aoi(), "drone_count": 2, "mode": "hardware", **kw}
    return client.post("/auto-missions", headers=h, json=body)


# ------------------------------------------------------------------ altitude


def test_altitude_plan_separates_and_respects_the_limit():
    assert auto.altitude_plan(2, 60, 30) == [20.0, 25.0]
    assert auto.altitude_plan(4, 60, 30) == [10.0, 15.0, 20.0, 25.0]
    assert auto.altitude_plan(1, 15, 30) == [15.0]
    assert auto.altitude_plan(2, 15, 30) == [15.0, 20.0]
    with pytest.raises(auto.AutoMissionError) as e:
        auto.altitude_plan(5, 60, 30)
    assert e.value.code == "TOO_MANY_DRONES" and "at most 4" in e.value.message


# --------------------------------------------------------------------- plan


def test_plan_picks_best_drones_fits_altitude_and_creates_geofence(client, db, organization, mc, op, fleet):  # noqa: F811
    drones, runtime = setup_fleet(client, db, organization, mc, op, battery={1: 60.0, 2: 99.0, 3: 88.0})
    r = plan(client, op)
    assert r.status_code == 201, r.text
    out = r.json()
    chosen = {v["name"] for v in out["drones"].values()}
    assert chosen == {"HW-02", "HW-03"}  # the two fullest batteries
    assert sorted(v["altitude_m"] for v in out["drones"].values()) == [20.0, 25.0]
    assert out["flight"]["altitude_m"] == 20.0 and out["flight"]["speed_mps"] <= 8  # survey template is 60 m / 10 m/s
    fence = db.get(Geofence, uuid.UUID(out["geofence_id"]))
    assert fence.type == "inclusion" and fence.active
    assert out["validation"]["passed"], out["validation"]
    assert {b["drone_id"] for b in out["plan"]["bundles"]} == set(out["drones"])
    mission = db.get(Mission, uuid.UUID(out["mission_id"]))
    assert mission.execution_mode == "live" and mission.status == "validating"


def test_plan_explains_why_drones_are_not_ready(client, db, organization, mc, op, fleet):  # noqa: F811
    setup_fleet(client, db, organization, mc, op, n=2, battery={1: 30.0})
    r = plan(client, op, drone_count=2)
    assert r.status_code == 409 and r.json()["code"] == "NOT_ENOUGH_DRONES"
    reasons = {x["name"]: x["reason"] for x in r.json()["not_ready"]}
    assert "battery 30 %" in reasons["HW-01"]
    assert db.execute(select(Mission).where(Mission.name.like("%Area Survey%"))).first() is None  # nothing created


def test_sar_needs_thermal_payload(client, db, organization, mc, op, fleet):  # noqa: F811
    setup_fleet(client, db, organization, mc, op, n=2)
    r = plan(client, op, template="search", drone_count=1)
    assert r.status_code == 409 and "thermal" in r.json()["not_ready"][0]["reason"]


def test_observer_cannot_plan(client, observer_user, fleet):
    from tests.conftest import auth_headers

    assert plan(client, auth_headers(client, observer_user.email)).status_code == 403


def test_manual_wizard_refuses_mixed_fleet(client, db, organization, mc, op, fleet):  # noqa: F811
    drones, _ = setup_fleet(client, db, organization, mc, op, n=1)
    demo = Drone(organization_id=organization.id, name="DEMO-1", uin="DEMO-1-x", model="x", firmware="x", status="online", active=True)
    db.add(demo)
    db.commit()
    m = client.post("/missions", headers=op, json={"name": "Mixed", "mission_type": "SURVEY"}).json()
    client.put(f"/missions/{m['id']}/wizard/steps/1", headers=op, json={"data": {"template": "survey"}})
    client.put(f"/missions/{m['id']}/wizard/steps/2", headers=op, json={"data": {"source": "drawn", "geometry": aoi()}})
    client.put(f"/missions/{m['id']}/wizard/steps/3", headers=op, json={"data": {"altitude_m": 20, "speed_mps": 5}})
    r = client.put(f"/missions/{m['id']}/wizard/steps/4", headers=op, json={"data": {"required_sensors": ["camera"], "drone_ids": [str(drones[0].id), str(demo.id)]}})
    assert r.status_code == 422 and r.json()["code"] == "MIXED_FLEET"


# ------------------------------------------------------------------- launch


def launched(client, db, organization, mc, op, fleet):  # noqa: F811
    drones, runtime = setup_fleet(client, db, organization, mc, op)
    out = plan(client, op).json()
    r = client.post(f"/auto-missions/{out['mission_id']}/launch", headers=op, json={"acknowledgements": ACKS, "minutes": 15})
    assert r.status_code == 202, r.text
    return out, r.json(), drones, runtime


def test_launch_needs_every_acknowledgement(client, db, organization, mc, op, fleet):  # noqa: F811
    setup_fleet(client, db, organization, mc, op)
    out = plan(client, op).json()
    r = client.post(f"/auto-missions/{out['mission_id']}/launch", headers=op, json={"acknowledgements": {**ACKS, "area_clear": False}})
    assert r.status_code == 422 and r.json()["code"] == "ACKNOWLEDGEMENTS_MISSING"
    assert not [c for c in fleet.calls if c[0] in ("authorize", "mission", "command")]


def test_one_approval_launches_the_fleet_staggered(client, db, organization, mc, op, fleet):  # noqa: F811
    out, launch, drones, _ = launched(client, db, organization, mc, op, fleet)
    chosen = set(out["drones"])
    assert db.execute(select(FlightPermission).where(FlightPermission.mission_id == uuid.UUID(out["mission_id"]))).scalars().all().__len__() == 2
    uploads = [c for c in fleet.calls if c[0] == "mission"]
    assert len(uploads) == 2
    alts = sorted({it["alt"] for _, _, items in uploads for it in items if it["command"] == 16})
    assert alts == [20.0, 25.0]  # each drone at its own altitude
    time.sleep(0.4)  # second start fires STAGGER_S later
    starts = [c for c in fleet.calls if c[0] == "command" and c[2] == "mission_start"]
    assert len(starts) == 2
    assert db.get(Mission, uuid.UUID(out["mission_id"])).status in ("active", "armed")
    assert db.execute(select(AuditLog).where(AuditLog.action == "auto_mission_launched")).first() is not None
    assert len(launch["start_order"]) == len(chosen)


def test_launch_is_all_or_nothing(client, db, organization, mc, op, fleet):  # noqa: F811
    drones, _ = setup_fleet(client, db, organization, mc, op)
    out = plan(client, op).json()
    bad_fc = next(f"COM5-{i}" for i, d in enumerate(drones, start=1) if str(d.id) in out["drones"])
    fleet.fail_prearm.add(bad_fc)
    r = client.post(f"/auto-missions/{out['mission_id']}/launch", headers=op, json={"acknowledgements": ACKS})
    assert r.status_code == 409 and r.json()["code"] == "LAUNCH_BLOCKED"
    assert not [c for c in fleet.calls if c[0] == "command" and c[2] == "mission_start"]
    assert all(f["authorized_until"] < time.time() for f in fleet.fcs.values())  # granted permissions revoked


# ------------------------------------------------------- status / dropout / reassign


def test_progress_dropout_and_replacement(client, db, organization, mc, op, fleet):  # noqa: F811
    out, launch, drones, runtime = launched(client, db, organization, mc, op, fleet)
    time.sleep(0.3)
    by_name = {d.name: d for d in drones}
    fc_of = {str(d.id): f"COM5-{i}" for i, d in enumerate(drones, start=1)}
    first_id, first = next(iter(launch["launch"]["drones"].items()))
    st = client.get(f"/auto-missions/{out['mission_id']}", headers=op).json()
    assert {d["state"] for d in st["drones_status"]} == {"flying"}

    # The first drone finishes its first task, then the pilot takes over.
    exit_seq = min(int(k) for k, v in first["seq_map"].items() if v["role"] == "exit")
    fleet.fcs[fc_of[first_id]].update(reached_max=exit_seq + 1, mode="LOITER",
                                      override={"reason": "mode changed AUTO -> LOITER outside Drone MatrX"})
    st = client.get(f"/auto-missions/{out['mission_id']}", headers=op).json()
    dropped = next(d for d in st["drones_status"] if d["drone_id"] == first_id)
    assert dropped["state"] == "dropped" and dropped["drop_reason"] == "pilot took control"
    assert dropped["tasks_done"] == 1 and len(dropped["remaining"]) == dropped["tasks_total"] - 1

    spare = next(d for d in drones if str(d.id) not in out["drones"])
    r = client.post(f"/auto-missions/{out['mission_id']}/reassign", headers=op,
                    json={"from_drone_id": first_id, "acknowledgements": ACKS})
    assert r.status_code == 200, r.text
    assert r.json()["to"] == spare.name and r.json()["tasks"] == dropped["remaining"]
    spare_upload = [c for c in fleet.calls if c[0] == "mission" and c[1] == fc_of[str(spare.id)]]
    assert spare_upload and all(it["alt"] == first["altitude_m"] for it in spare_upload[-1][2] if it["command"] == 16)
    assert ("command", fc_of[str(spare.id)], "mission_start") in fleet.calls
    st = client.get(f"/auto-missions/{out['mission_id']}", headers=op).json()
    states = {d["name"]: d["state"] for d in st["drones_status"]}
    assert states[by_name[first["name"]].name] == "replaced" and states[spare.name] == "flying"


def test_mission_completes_when_every_task_is_flown(client, db, organization, mc, op, fleet):  # noqa: F811
    out, launch, drones, _ = launched(client, db, organization, mc, op, fleet)
    fc_of = {str(d.id): f"COM5-{i}" for i, d in enumerate(drones, start=1)}
    for did, entry in launch["launch"]["drones"].items():
        last_exit = max(int(k) for k, v in entry["seq_map"].items() if v["role"] == "exit")
        fleet.fcs[fc_of[did]].update(reached_max=last_exit + 1, mode="RTL")
    st = client.get(f"/auto-missions/{out['mission_id']}", headers=op).json()
    assert {d["state"] for d in st["drones_status"]} == {"completed"}
    assert st["launch"]["status"] == "completed"


def test_no_replacement_when_no_ready_drone(client, db, organization, mc, op, fleet):  # noqa: F811
    drones, runtime = setup_fleet(client, db, organization, mc, op, n=2)
    out = plan(client, op).json()
    client.post(f"/auto-missions/{out['mission_id']}/launch", headers=op, json={"acknowledgements": ACKS})
    first_id = next(iter(out["drones"]))
    fleet.fcs[f"COM5-{[str(d.id) for d in drones].index(first_id) + 1}"].update(mode="RTL")
    r = client.post(f"/auto-missions/{out['mission_id']}/reassign", headers=op, json={"from_drone_id": first_id, "acknowledgements": ACKS})
    assert r.status_code == 409 and r.json()["code"] == "NO_REPLACEMENT"


def test_availability_lists_ready_and_not_ready(client, db, organization, mc, op, fleet):  # noqa: F811
    setup_fleet(client, db, organization, mc, op, n=2, battery={2: 20.0})
    out = client.get("/auto-missions/availability", headers=op).json()
    assert out["hardware"]["survey"]["ready"] == ["HW-01"]
    assert out["hardware"]["survey"]["not_ready"][0]["name"] == "HW-02"
    assert out["limits"]["max_alt_m"] == 30


def test_locked_or_rtl_drones_are_not_offered(client, db, organization, mc, op, fleet):  # noqa: F811
    setup_fleet(client, db, organization, mc, op, n=3)
    fleet.fcs["COM5-1"]["override"] = {"reason": "mode changed AUTO -> LAND outside Drone MatrX"}
    fleet.fcs["COM5-2"]["mode"] = "RTL"
    out = client.get("/auto-missions/availability", headers=op).json()["hardware"]["survey"]
    reasons = {n["name"]: n["reason"] for n in out["not_ready"]}
    assert "pilot override lock" in reasons["HW-01"]
    assert "RTL mode" in reasons["HW-02"]
    assert out["ready"] == ["HW-03"]
    assert plan(client, op, drone_count=2).json()["code"] == "NOT_ENOUGH_DRONES"
