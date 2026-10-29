# Hardware bridge (real ArduPilot aircraft)

Connects Drone MatrX to real ArduCopter drones over MAVLink telemetry radios.
It runs on the operations laptop, because Docker on Windows cannot open COM ports.

```
ArduPilot ── radio ── COMn ──► hwbridge (127.0.0.1:8110) ──► NATS drone.{UIN}.telemetry ──► backend / map / fleet / TimescaleDB
                                     ▲  commands, pre-arm, flight permission, missions
                                     └── backend (HWBRIDGE_URL, X-Bridge-Token)
cameras (RTSP) ──────────────────────────► MediaMTX (path hw/sysN, pulled) ──► video wall (WHEP)
```

## Run

```powershell
copy hwbridge\config.example.json hwbridge\config.json     # COM ports, camera URLs by SYSID, token, limits
backend\venv\Scripts\python -m hwbridge.server --config hwbridge\config.json
```

Set `HWBRIDGE_TOKEN` in `backend\.env` to the same token. Check http://127.0.0.1:8110/health.

## Safety model

- **Human flight permission.** ARM, TAKEOFF, GOTO and MISSION_START need a time-limited permission (5 acknowledgements, at most 30 min). It is granted in Mission Control and checked by both the backend and the bridge.
- **The RC pilot always wins.** The bridge never sends RC override. Any mode change or arming it did not command (an RC mode switch, a failsafe, stick arming) sets a **pilot override** that refuses every automated command until an operator releases it, which is audited.
- **Hard limits** (`limits` in config): maximum altitude and maximum distance from home, checked for take-off, go-to and every mission waypoint.
- **No forced arming.** ArduPilot's own pre-arm checks decide. Arming always happens in GUIDED.
- **1 Hz ground-station heartbeat**, so ArduPilot's GCS failsafe (`FS_GCS_ENABLE`) triggers if the laptop or radio is lost.
- **ArduCopter only.** Every aircraft needs a unique `SYSID_THISMAV`.

## Test

```powershell
backend\venv\Scripts\python -m pytest hwbridge\tests -q        # bridge safety logic (real MAVLink messages)
cd backend; venv\Scripts\python -m pytest tests\test_hardware_flight.py -q
```

Without aircraft, `sim\ardupilot_sitl\start.ps1` runs two ArduCopter SITL instances on TCP 5760/5770.
Use `"links": [{"endpoint": "tcp:127.0.0.1:5760"}, {"endpoint": "tcp:127.0.0.1:5770"}]` to point the bridge at them.

The field procedure is in [`documents/hardware_test/PHYSICAL_DRONE_TEST_SCRIPT.md`](../documents/hardware_test/PHYSICAL_DRONE_TEST_SCRIPT.md).
