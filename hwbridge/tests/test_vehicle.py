"""Hardware-bridge safety logic against real MAVLink messages (pymavlink
ardupilotmega dialect), with a fake radio that answers like ArduPilot.

Run from the repo root:  backend/venv/Scripts/python -m pytest hwbridge/tests -q
"""
from __future__ import annotations

import threading
import time

import pytest
from pymavlink.dialects.v20 import ardupilotmega as mavlink

from hwbridge.ardupilot import MODE_NUMBER, RC_RECEIVER, Limits, Vehicle, VehicleError
from hwbridge.links import Link

HOME = (23.2400, 72.6500, 50.0)


def packed(msg, sysid: int = 1, compid: int = 1):
    """Encode + decode so the message carries a real MAVLink header."""
    mav = mavlink.MAVLink(None, srcSystem=sysid, srcComponent=compid)
    return mav.decode(bytearray(msg.pack(mav)))


def heartbeat(mode: str = "LOITER", armed: bool = False, vtype: int = 2, autopilot: int = 3):
    return packed(mavlink.MAVLink_heartbeat_message(vtype, autopilot, 0x80 if armed else 0, MODE_NUMBER[mode], 3, 3))


class FakeRadio:
    """Records what the bridge sends; answers commands like ArduPilot would."""

    def __init__(self):
        self.sent: list[tuple[str, tuple]] = []
        self.vehicle: Vehicle | None = None
        self.result = 0
        self.mission_requests = True

    def __getattr__(self, name):
        def call(*args):
            self.sent.append((name, args))
            v = self.vehicle
            if v is None:
                return
            if name in ("command_long_send", "command_int_send"):
                cmd = args[3] if name == "command_int_send" else args[2]
                threading.Timer(0.01, lambda: v.handle(packed(mavlink.MAVLink_command_ack_message(cmd, self.result)))).start()
            elif name == "mission_count_send" and self.mission_requests:
                n = args[2]

                def serve():
                    for seq in range(n):
                        v.handle(packed(mavlink.MAVLink_mission_request_int_message(255, 190, seq)))
                        time.sleep(0.005)
                    v.handle(packed(mavlink.MAVLink_mission_ack_message(255, 190, 0)))
                threading.Timer(0.01, serve).start()
        return call

    def names(self) -> list[str]:
        return [n for n, _ in self.sent]


@pytest.fixture
def vehicle():
    radio = FakeRadio()
    events: list[tuple[str, dict]] = []
    v = Vehicle("COM5", "serial:COM5", 1, 1, radio, Limits(max_alt_m=30, max_distance_m=150), lambda k, d: events.append((k, d)))
    radio.vehicle = v
    v.handle(heartbeat("STABILIZE"))
    v.handle(packed(mavlink.MAVLink_home_position_message(int(HOME[0] * 1e7), int(HOME[1] * 1e7), int(HOME[2] * 1000), 0, 0, 0, [1, 0, 0, 0], 0, 0, 0)))
    v.handle(packed(mavlink.MAVLink_global_position_int_message(0, int(HOME[0] * 1e7), int(HOME[1] * 1e7), 50000, 0, 0, 0, 0, 0)))
    v.handle(packed(mavlink.MAVLink_extended_sys_state_message(0, 1)))
    v.drone_id, v.uin, v.name = "d-1", "DRN-HW-1", "HW-1"
    v.events = events
    v.radio = radio
    return v


def authorize(v: Vehicle, s: float = 600):
    v.authorized_until = time.time() + s


def test_flight_commands_need_permission(vehicle):
    for cmd, params in (("arm", {}), ("takeoff", {"altitude_m": 10}), ("goto", {"latitude": HOME[0], "longitude": HOME[1], "altitude_m": 10}),
                        ("mission_start", {})):
        with pytest.raises(VehicleError) as e:
            vehicle.execute(cmd, params)
        assert e.value.code == "FLIGHT_NOT_AUTHORIZED", cmd
    # Safety commands never need a permission.
    assert vehicle.execute("hold", {}) == 0
    assert vehicle.execute("rtl", {}) == 0


def test_permission_expires(vehicle):
    authorize(vehicle, 0.05)
    time.sleep(0.1)
    with pytest.raises(VehicleError) as e:
        vehicle.execute("arm", {})
    assert e.value.code == "FLIGHT_NOT_AUTHORIZED"


def test_arm_goes_guided_and_is_never_forced(vehicle):
    authorize(vehicle)
    vehicle.handle(heartbeat("RTL"))  # e.g. left in RTL: arming must switch to GUIDED first
    vehicle.override = None  # (that mode change was outside the bridge; covered by its own test)
    assert vehicle.execute("arm", {}) == 0
    longs = [a for n, a in vehicle.radio.sent if n == "command_long_send"]
    set_mode, arm = longs[-2], longs[-1]
    assert set_mode[2] == 176 and set_mode[5] == MODE_NUMBER["GUIDED"]
    assert arm[2] == 400 and arm[4] == 1 and arm[5] == 0  # param2 != 21196: never force-arm


def test_rc_mode_switch_locks_out_automation(vehicle):
    authorize(vehicle)
    vehicle.execute("hold", {})          # we command LOITER ...
    vehicle.handle(heartbeat("LOITER"))  # ... and see it: ours, no override
    assert vehicle.override is None
    vehicle.handle(heartbeat("ALT_HOLD"))  # pilot flips the transmitter's mode switch
    assert vehicle.override is not None and "ALT_HOLD" in vehicle.override["reason"]
    assert ("pilot_override", {"reason": vehicle.override["reason"]}) in vehicle.events
    for cmd in ("hold", "rtl", "land", "arm"):
        with pytest.raises(VehicleError) as e:
            vehicle.execute(cmd, {})
        assert e.value.code == "PILOT_OVERRIDE"
    vehicle.override = None  # operator release (audited in the backend)
    assert vehicle.execute("hold", {}) == 0


def test_failsafe_mode_change_and_stick_arming_lock_out(vehicle):
    vehicle.handle(heartbeat("LAND"))  # battery failsafe: autopilot in charge
    assert vehicle.override is not None
    vehicle.override = None
    vehicle.handle(heartbeat("LAND", armed=True))  # armed with the sticks
    assert vehicle.override is not None and "armed outside" in vehicle.override["reason"]


def test_limits(vehicle):
    authorize(vehicle)
    vehicle.execute("arm", {})                       # the bridge arms (in GUIDED) ...
    vehicle.handle(heartbeat("GUIDED", armed=True))  # ... so this state change is ours
    assert vehicle.override is None
    with pytest.raises(VehicleError) as e:
        vehicle.execute("takeoff", {"altitude_m": 45})
    assert e.value.code == "ALTITUDE_LIMIT"
    with pytest.raises(VehicleError) as e:  # ~330 m north of home
        vehicle.execute("goto", {"latitude": HOME[0] + 0.003, "longitude": HOME[1], "altitude_m": 20})
    assert e.value.code == "DISTANCE_LIMIT"
    assert vehicle.execute("goto", {"latitude": HOME[0] + 0.0005, "longitude": HOME[1], "altitude_m": 20}) == 0
    reposition = [a for n, a in vehicle.radio.sent if n == "command_int_send"][-1]
    assert reposition[2] == 6 and reposition[3] == 192  # GLOBAL_RELATIVE_ALT_INT, MAV_CMD_DO_REPOSITION


def test_never_sends_rc_override(vehicle):
    authorize(vehicle)
    for cmd in ("arm", "hold", "rtl", "land"):
        try:
            vehicle.execute(cmd, {})
        except VehicleError:
            pass
    assert not any("rc_channels_override" in n for n in vehicle.radio.names())


def test_disarm_only_on_ground(vehicle):
    vehicle.handle(heartbeat("STABILIZE", armed=True))
    vehicle.override = None
    vehicle.handle(packed(mavlink.MAVLink_extended_sys_state_message(0, 2)))  # in air
    with pytest.raises(VehicleError) as e:
        vehicle.execute("disarm", {})
    assert e.value.code == "NOT_ON_GROUND"


def test_mission_upload_puts_home_first_and_checks_limits(vehicle):
    items = [{"command": 22, "lat": HOME[0], "lon": HOME[1], "alt": 20},
             {"command": 178, "param1": 1, "param2": 5, "param3": -1, "lat": 0, "lon": 0, "alt": 0, "frame": 2},
             {"command": 16, "lat": HOME[0] + 0.0004, "lon": HOME[1], "alt": 20},
             {"command": 20, "lat": 0, "lon": 0, "alt": 0, "frame": 2}]
    assert vehicle.upload_mission(items) == 4
    sent = [a for n, a in vehicle.radio.sent if n == "mission_item_int_send"]
    assert [a[2] for a in sent] == [0, 1, 2, 3, 4]
    assert sent[0][4] == 16 and sent[0][3] == 0  # home placeholder, GLOBAL frame
    assert sent[2][3] == 6  # PX4 MISSION frame mapped to GLOBAL_RELATIVE_ALT_INT
    assert vehicle.state.mission_count == 5
    too_far = items[:2] + [{"command": 16, "lat": HOME[0] + 0.01, "lon": HOME[1], "alt": 20}]
    with pytest.raises(VehicleError) as e:
        vehicle.upload_mission(too_far)
    assert e.value.code == "DISTANCE_LIMIT"


def test_mission_start_arms_then_starts(vehicle):
    vehicle.state.mission_count = 5
    authorize(vehicle)
    assert vehicle.execute("mission_start", {}) == 0
    cmds = [a[2] for n, a in vehicle.radio.sent if n == "command_long_send"]
    assert cmds[-3:] == [176, 400, 300]  # GUIDED, ARM, MISSION_START


def test_prearm_reports_rc_receiver_and_telemetry(vehicle):
    vehicle.handle(packed(mavlink.MAVLink_sys_status_message(RC_RECEIVER | 0x10000000, RC_RECEIVER, RC_RECEIVER, 0, 16000, -1, 76, 0, 0, 0, 0, 0, 0)))
    vehicle._params_cache, vehicle._params_read_at = {"FS_GCS_ENABLE": 1.0}, time.time()
    p = vehicle.prearm()
    assert p["autopilot"] == "ardupilot" and p["rc_receiver_ok"] is True and p["battery_pct"] == 76 and p["battery_v"] == 16.0
    t = vehicle.telemetry()
    assert t["hardware"] is True and t["simulation"] is False and t["uin"] == "DRN-HW-1" and t["flight_mode"] == "STABILIZE"
    assert t["link_type"] is None  # the TimescaleDB writer accepts only wifi/lte/lora
    vehicle.drone_id = None
    assert vehicle.telemetry() is None  # unbound: publishes nothing


def test_link_accepts_only_ardupilot_autopilots():
    link = Link("serial:COM9", 57600, Limits(), lambda v, k, d: None)
    link.mav = FakeRadio()
    link._route(packed(mavlink.MAVLink_heartbeat_message(2, 12, 0, 0, 3, 3), sysid=7))  # PX4
    link._route(packed(mavlink.MAVLink_heartbeat_message(6, 8, 0, 0, 4, 3), sysid=200))  # another ground station
    link._route(packed(mavlink.MAVLink_heartbeat_message(30, 3, 0, 0, 4, 3), sysid=1, compid=154))  # gimbal of sysid 1
    assert link.vehicles == {} and 7 in link.ignored
    link._route(heartbeat("LOITER"))  # ArduCopter, sysid 1, autopilot component
    link._route(packed(mavlink.MAVLink_heartbeat_message(2, 3, 0, 5, 3, 3), sysid=2))
    assert sorted(link.vehicles) == [1, 2]
    assert link.vehicles[1].fc_id == "COM9-1" and link.vehicles[2].identity == "serial:COM9/2"


def test_late_heartbeats_during_mission_start_are_not_an_override(vehicle):
    vehicle.state.mission_count = 5
    authorize(vehicle)
    vehicle.execute("mission_start", {})  # commands GUIDED, ARM, AUTO within ~1 s
    vehicle.handle(heartbeat("GUIDED", armed=True))  # heartbeat lagging behind the AUTO command
    vehicle.handle(heartbeat("AUTO", armed=True))
    assert vehicle.override is None


def test_bridge_telemetry_is_accepted_by_the_platform(vehicle):
    """The payload published on NATS must pass the backend's own validators:
    the live pipeline (TelemetryUpdate) and the TimescaleDB writer (parse_record)."""
    import sys
    import uuid as _uuid
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.schemas.telemetry import TelemetryUpdate
    from app.services.telemetry.store import parse_record

    drone_id = _uuid.uuid4()
    vehicle.drone_id = str(drone_id)
    vehicle.handle(packed(mavlink.MAVLink_sys_status_message(0, 0, 0, 0, 16000, -1, 80, 0, 0, 0, 0, 0, 0)))
    payload = vehicle.telemetry()
    TelemetryUpdate(**payload)
    rec = parse_record(payload, known_drones={drone_id})
    assert rec.lat == pytest.approx(HOME[0]) and rec.soc == 80
