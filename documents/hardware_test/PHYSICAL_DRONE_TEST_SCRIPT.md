# Drone MatrX: Physical Drone Test Script (Sprints 1–6)

| | |
|---|---|
| **Aircraft** | 2 or more ArduPilot (ArduCopter) multirotors, each with an RTSP (IP/Wi-Fi) camera |
| **Link** | SiK-type telemetry radios → USB hub → operations laptop (MAVLink 57600 baud) |
| **Software** | Drone MatrX `main` + hardware bridge (`hwbridge/`) |
| **Order** | Part 0 setup → **Part A: bench, propellers removed** → Part B: supervised flight. Do not start Part B unless every Part A test passed. |
| **Date / site** | ____________________ / ____________________ |
| **Team** | Operator (laptop): __________ · Safety pilot (RC): __________ · Observer: __________ |

> **Golden rules (read aloud before starting)**
> 1. **Propellers OFF** for every Part A test. Remove them; don't just "be careful".
> 2. The **safety pilot holds the RC transmitter, powered on, for every test where a battery is connected**. Their mode switch overrides Drone MatrX at any time. Drone MatrX never sends RC override.
> 3. **Nothing flies without a human flight permission** granted in Drone MatrX, which needs 5 confirmations and expires after at most 30 min.
> 4. Anyone can call **"ABORT"**. The safety pilot then takes over (LOITER/ALT_HOLD → land), and the operator presses **LAND ALL** or **RTL ALL** if both drones are airborne.
> 5. Stay within visual line of sight, below **30 m**, within **150 m** of take-off. These are the bridge's hard limits for today.

---

## Part 0: Preparation

### 0.1 Legal and site

| ✔ | Item |
|---|---|
| ☐ | Site is in a **green zone** on the DGCA Digital Sky airspace map (or written permission for yellow) |
| ☐ | Each aircraft is registered (UIN) and the safety pilot holds a valid Remote Pilot Certificate where required |
| ☐ | Landowner permission; no people, roads or power lines under the flight area |
| ☐ | Wind below the aircraft's limit (typically < 8 m/s); no rain |

### 0.2 ArduPilot parameters (each drone, set in Mission Planner **before** coming to the field)

| Parameter | Drone 1 | Drone 2 | Why |
|---|---|---|---|
| `SYSID_THISMAV` | **1** | **2** | Must be unique: the bridge tells drones apart by it |
| `SERIAL1_BAUD` (radio port) | 57 | 57 | Matches the radio's air/serial rate |
| `FS_GCS_ENABLE` | 1 (RTL) | 1 (RTL) | Laptop/radio lost → return home |
| `FS_GCS_TIMEOUT` | 5 | 5 | seconds |
| `FS_THR_ENABLE` | 1 (RTL) | 1 (RTL) | RC transmitter lost → return home |
| `BATT_FS_LOW_ACT` | 2 (RTL) | 2 (RTL) | Low battery → return home (set `BATT_LOW_VOLT` for your pack) |
| `FENCE_ENABLE` / `FENCE_TYPE` | 1 / 7 | 1 / 7 | Onboard altitude + circle + polygon fence |
| `FENCE_ALT_MAX` | 40 | 40 | m |
| `FENCE_RADIUS` | 150 | 150 | m |
| `FENCE_ACTION` | 1 (RTL) | 1 (RTL) | |
| `RTL_ALT` | **1500** (15 m) | **2000** (20 m) | **Different heights**, so two drones returning at once don't meet |
| RC flight-mode switch | includes **LOITER**, **ALT_HOLD** (or STABILIZE), **RTL** | same | Safety pilot's takeover positions |
| Motor emergency stop / arming switch on RC | configured | configured | Last-resort kill |

**Radios on one USB hub:**
- Each drone's radio pair needs its **own `NETID`** (for example 25 and 26), so the pairs don't cross-talk.
- Each ground radio appears as its own COM port.
- Check them in Device Manager → Ports, or in PowerShell: `[System.IO.Ports.SerialPort]::GetPortNames()`.

**Cameras:** open each camera's RTSP URL in VLC (Media → Open Network Stream) and confirm live video. Note the URLs.

| Drone | SYSID | COM port | Radio NETID | Camera RTSP URL | Drone MatrX name |
|---|---|---|---|---|---|
| 1 | 1 | COM__ | __ | rtsp://__________ | HW-01 |
| 2 | 2 | COM__ | __ | rtsp://__________ | HW-02 |

### 0.3 Laptop

1. Start **Docker Desktop**, then in a terminal from the project folder:
   ```powershell
   $env:MEDIAMTX_WEBRTC_HOSTS = "<this laptop's Wi-Fi IP>"   # ipconfig → IPv4 Address
   docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d db nats mediamtx
   ```
2. Backend:
   ```powershell
   cd backend
   venv\Scripts\alembic upgrade head
   venv\Scripts\python -m uvicorn app.main:app --port 8000
   ```
3. Frontend (second terminal): `cd frontend; npm run dev`, then open http://localhost:5173/login.
4. **Close Mission Planner / QGroundControl.** Only one program can open a COM port.
5. Hardware bridge (third terminal):
   ```powershell
   copy hwbridge\config.example.json hwbridge\config.json
   # edit hwbridge\config.json: COM ports, camera URLs (keyed by SYSID), token, limits (30 m / 150 m)
   backend\venv\Scripts\python -m hwbridge.server --config hwbridge\config.json
   ```
   Put the same token in `backend\.env` as `HWBRIDGE_TOKEN=...` and restart the backend.
6. Check http://127.0.0.1:8110/health: every link has `"open": true`, `vehicles` lists [1] / [2] once the drones are powered, and `nats.connected` is `true`.

---

## Part A: Bench tests (PROPELLERS REMOVED)

Batteries in, drones on the ground 3 m apart, GPS lock outdoors, RC transmitter on.

### A1: Sprint 1: Login and roles

| ID | Steps | Expected | Result |
|---|---|---|---|
| A1.1 | Log in as **operator** | Dashboard opens; name and role in header | ☐ Pass ☐ Fail |
| A1.2 | Log in as **observer** in a private window, open Mission Control | Read-only banner; no Arm, Bind or Hold-all buttons | ☐ Pass ☐ Fail |

### A2: Sprint 2: Fleet, map, live telemetry (real drones)

| ID | Steps | Expected | Result |
|---|---|---|---|
| A2.1 | Fleet → **+ New Drone** → name HW-01, model, UIN, tick **"Real aircraft (hardware)"**. Repeat for HW-02. | Created; Fleet shows both | ☐ Pass ☐ Fail |
| A2.2 | Mission Control → **Flight Controllers** → **Discover** | Two rows: `serial:COMx/1` and `serial:COMy/2`, ArduPilot firmware, CONNECTED | ☐ Pass ☐ Fail |
| A2.3 | Choose HW-01 → Bind `…/1`; HW-02 → Bind `…/2` | Both BOUND; source label **HARDWARE** | ☐ Pass ☐ Fail |
| A2.4 | Look at the map | Each marker is where the drone physically stands (±3 m) | ☐ Pass ☐ Fail |
| A2.5 | Rotate drone 1 by hand 90° | Its heading on the map turns within ~1 s | ☐ Pass ☐ Fail |
| A2.6 | Compare battery % with the drone's OSD / Mission Planner value | Matches (±2 %) | ☐ Pass ☐ Fail |
| A2.7 | Unplug drone 1's ground radio from the hub | HW-01 turns **STALE** after ~5 s; the other stays CONNECTED | ☐ Pass ☐ Fail |
| A2.8 | Plug it back in | CONNECTED again within ~5 s (bridge re-opens the port) | ☐ Pass ☐ Fail |
| A2.9 | Try binding the real controller to a **demo** drone (DMX-…) | Refused: "not registered as a real aircraft" | ☐ Pass ☐ Fail |

### A3: Sprint 6: Pre-arm checklist

Select HW-01 in Mission Control and read the **Pre-arm checklist**.

| ID | Steps | Expected | Result |
|---|---|---|---|
| A3.1 | First time at this site: Settings → draw an **inclusion** geofence around the flying field | Saved | ☐ Pass ☐ Fail |
| A3.2 | Refresh the checklist | All required checks PASS (connected, telemetry, identity, mode, on ground, battery ≥ 50 %, GNSS fix + home, estimator, ArduPilot pre-arm, geofence, failsafes, **RC transmitter**, **no override**). "Flight permission" shows WARNING. | ☐ Pass ☐ Fail |
| A3.3 | Switch the **RC transmitter OFF**, refresh | "RC transmitter connected" **FAIL**, ARM blocked | ☐ Pass ☐ Fail |
| A3.4 | Transmitter ON again, refresh | PASS | ☐ Pass ☐ Fail |
| A3.5 | Note any FAIL text from ArduPilot (e.g. "PreArm: Compass not calibrated") and fix it in Mission Planner before continuing | No ArduPilot pre-arm failures | ☐ Pass ☐ Fail |

### A4: Human flight permission and arming (props OFF)

| ID | Steps | Expected | Result |
|---|---|---|---|
| A4.1 | Press **Arm** with no permission | Refused: `FLIGHT_NOT_AUTHORIZED`; motors stay off | ☐ Pass ☐ Fail |
| A4.2 | **Grant flight permission** → leave one box unticked | Grant button disabled | ☐ Pass ☐ Fail |
| A4.3 | Tick all 5 boxes, 10 min → Grant | "FLIGHT PERMITTED · 9:59 left" countdown | ☐ Pass ☐ Fail |
| A4.4 | **Arm** → untick "take off once armed" → confirm "REAL HARDWARE" | Motors spin (no props); panel shows **ARMED** from telemetry; command CONFIRMED | ☐ Pass ☐ Fail |
| A4.5 | **Disarm** | Motors stop; DISARMED | ☐ Pass ☐ Fail |
| A4.6 | **Revoke** permission, press Arm | Refused again | ☐ Pass ☐ Fail |

### A5: RC override lock-out (props OFF)

| ID | Steps | Expected | Result |
|---|---|---|---|
| A5.1 | Grant permission, Arm from Drone MatrX | ARMED | ☐ Pass ☐ Fail |
| A5.2 | Safety pilot flips the transmitter mode switch (e.g. to LOITER) | Red banner "**PILOT / AUTOPILOT HAS CONTROL**"; checklist "No override" FAIL | ☐ Pass ☐ Fail |
| A5.3 | Press Hold / Land / Arm in Drone MatrX | All refused: `PILOT_OVERRIDE`; nothing sent to the aircraft | ☐ Pass ☐ Fail |
| A5.4 | Pilot disarms with the transmitter | DISARMED | ☐ Pass ☐ Fail |
| A5.5 | Type a reason ("pilot handed back control") → **Release** | Banner clears; the release is recorded in the audit log (`pilot_override_released`) | ☐ Pass ☐ Fail |

### A6: Sprint 6: Live video wall

| ID | Steps | Expected | Result |
|---|---|---|---|
| A6.1 | Video Wall → **Refresh** | Tiles "Camera - HW-01", "Camera - HW-02" with **LIVE CAMERA** label | ☐ Pass ☐ Fail |
| A6.2 | Wave a hand in front of each camera | Movement visible; delay looks < 0.5 s | ☐ Pass ☐ Fail |
| A6.3 | 1 / 2×2 layouts, fullscreen, picture-in-picture | All work | ☐ Pass ☐ Fail |
| A6.4 | Record 20 s on HW-01 → open DVR → seek | Recording plays; seeking works | ☐ Pass ☐ Fail |
| A6.5 | Power-cycle camera 2 | Tile shows link loss, then reconnects by itself | ☐ Pass ☐ Fail |

### A7: Sprint 4: Message bus and telemetry storage

| ID | Steps | Expected | Result |
|---|---|---|---|
| A7.1 | Analyse → **Message bus** | `drone.<UIN>.telemetry` messages from both hardware drones, ~2 per second each | ☐ Pass ☐ Fail |
| A7.2 | Analyse → **Telemetry** → HW-01, last 15 min | History chart of the bench session | ☐ Pass ☐ Fail |

### A8: Sprint 3 + 5: CBBA plan for the real drones (no flight yet)

| ID | Steps | Expected | Result |
|---|---|---|---|
| A8.1 | Missions → New mission (survey) → draw a **small AOI inside the field (≈ 60 × 40 m)**, altitude 10 m, speed 4 m/s, constraints RTH → select **HW-01 and HW-02** | Wizard accepts | ☐ Pass ☐ Fail |
| A8.2 | Run **CBBA** | Coloured lanes split between HW-01 and HW-02 with ETAs | ☐ Pass ☐ Fail |
| A8.3 | *(Recommended)* Mission → New Simulation with the same AOI and parameters (Sprint 5, Gazebo) | Simulated flight completes; lanes covered | ☐ Pass ☐ Fail ☐ Skipped |
| A8.4 | Mission Control → HW-01 → CBBA mission = this mission, **Alt 10 m** → Upload. HW-02 → **Alt 15 m** → Upload. | "N items at 10 m / 15 m (tasks …)" for each | ☐ Pass ☐ Fail |
| A8.5 | Upload with Alt 45 m | Refused: altitude limit 30 m | ☐ Pass ☐ Fail |

### A9: Fleet commands on the ground

| ID | Steps | Expected | Result |
|---|---|---|---|
| A9.1 | **HOLD ALL** → preview | Both hardware drones listed as "not flying" (excluded); nothing sent | ☐ Pass ☐ Fail |

**Part A sign-off:** all Pass ☐ · Operator ________ · Safety pilot ________ → **Fit propellers** (check rotation direction and tightness).

---

## Part B: Supervised flight (PROPELLERS ON)

**Go / no-go** (all must be yes): ☐ Part A passed ☐ Batteries ≥ 90 % ☐ RC transmitter on, mode switch in LOITER ☐ Pre-arm all PASS ☐ Area clear ☐ Observer in position ☐ Wind OK

Fly **one drone first** (B1–B4), then two (B5–B7).

### B1: Take-off, hold, go-to, return (HW-01)

| ID | Steps | Expected | Result |
|---|---|---|---|
| B1.1 | Grant permission (10 min) → Arm with "take off once armed" at **5 m** | Climbs to 5 m and hovers; map altitude ≈ 5 m | ☐ Pass ☐ Fail |
| B1.2 | **Hold** | Mode LOITER; holds position | ☐ Pass ☐ Fail |
| B1.3 | **Go to** → click a point ~20 m away → 8 m → Send | Flies there; command CONFIRMED when within 6 m | ☐ Pass ☐ Fail |
| B1.4 | Go to a point **> 150 m** away | Refused: distance limit (never sent) | ☐ Pass ☐ Fail |
| B1.5 | Video tile during flight | Live camera from the air | ☐ Pass ☐ Fail |
| B1.6 | **RTL** | Climbs to RTL_ALT, returns, lands, disarms | ☐ Pass ☐ Fail |

### B2: RC takeover in flight (HW-01)

| ID | Steps | Expected | Result |
|---|---|---|---|
| B2.1 | Permission → take off 5 m → Go to a point 20 m away | Flying to the point | ☐ Pass ☐ Fail |
| B2.2 | Halfway, safety pilot switches to **LOITER** | Drone stops and obeys the pilot **immediately**; red override banner | ☐ Pass ☐ Fail |
| B2.3 | Operator presses RTL | Refused (`PILOT_OVERRIDE`): Drone MatrX doesn't fight the pilot | ☐ Pass ☐ Fail |
| B2.4 | Pilot lands manually and disarms | Landed | ☐ Pass ☐ Fail |

### B3: Ground-station link loss (HW-01, optional, experienced pilot only)

| ID | Steps | Expected | Result |
|---|---|---|---|
| B3.1 | Hover at 8 m in LOITER (pilot ready) → unplug drone 1's ground radio | After `FS_GCS_TIMEOUT` (5 s) the drone switches to **RTL** by itself; the UI shows STALE | ☐ Pass ☐ Fail ☐ Skipped |
| B3.2 | Plug back in | CONNECTED; override banner (mode changed by failsafe), release after landing | ☐ Pass ☐ Fail ☐ Skipped |

### B4: Battery and limits check

| ID | Steps | Expected | Result |
|---|---|---|---|
| B4.1 | After B1–B3, read battery in Drone MatrX vs the pack voltage | Consistent; swap packs below 50 % | ☐ Pass ☐ Fail |

### B5: CBBA mission with two drones

Launch points at least **5 m apart**. Uploaded altitudes **10 m (HW-01) and 15 m (HW-02)** from A8.4.

| ID | Steps | Expected | Result |
|---|---|---|---|
| B5.1 | Grant permission for **each** drone (15 min) | Both "FLIGHT PERMITTED" | ☐ Pass ☐ Fail |
| B5.2 | HW-01 → **Start mission** → confirm | Arms, takes off, flies its lanes in AUTO | ☐ Pass ☐ Fail |
| B5.3 | 10 s later HW-02 → **Start mission** | Same, at 15 m | ☐ Pass ☐ Fail |
| B5.4 | Watch the map and both video tiles | Tracks follow the CBBA lanes; both feeds live | ☐ Pass ☐ Fail |
| B5.5 | Missions finish | Each returns (RTL), lands and disarms | ☐ Pass ☐ Fail |

### B6: Fleet commands in flight

| ID | Steps | Expected | Result |
|---|---|---|---|
| B6.1 | Start both missions again → **HOLD ALL** → confirm | Both switch to LOITER and hover; per-drone results CONFIRMED | ☐ Pass ☐ Fail |
| B6.2 | **RTL ALL** → confirm | Both return at their different RTL altitudes and land | ☐ Pass ☐ Fail |

### B7: Emergency LAND ALL

| ID | Steps | Expected | Result |
|---|---|---|---|
| B7.1 | Both hovering at 8–10 m → **LAND ALL** → type `LAND ALL` | Both land where they are and disarm | ☐ Pass ☐ Fail |

---

## Emergency procedures

| Situation | Who | Action |
|---|---|---|
| Anything unexpected | Anyone | Call **ABORT** |
| Drone not doing what it should | Safety pilot | Mode switch → **LOITER** (or ALT_HOLD), fly back, land |
| Both drones airborne, operator still in control | Operator | **RTL ALL** (or **LAND ALL** if returning is unsafe) |
| Laptop / radio lost | Autopilot | GCS failsafe → RTL after 5 s; pilot monitors and takes over if needed |
| RC transmitter lost | Autopilot | Radio failsafe → RTL |
| Low battery | Autopilot | Battery failsafe → RTL; land immediately after |
| Fly-away / imminent danger | Safety pilot | **Motor emergency stop** switch |

**Abort criteria:**
- unexpected mode changes;
- telemetry STALE for more than 5 s while flying;
- position jumping more than 5 m on the map;
- any ArduPilot EKF, vibration or compass warning;
- wind gusts;
- a person entering the area.

---

## After the session

| ✔ | Item |
|---|---|
| ☐ | Save the **audit log** (permissions, overrides, commands): http://localhost:8000/docs → Authorize (operator login) → `GET /mission-control/audit` with `limit=500` → Execute → Download |
| ☐ | Download the video recordings from the DVR |
| ☐ | Download the ArduPilot **dataflash logs** (Mission Planner → DataFlash Logs) for each flight |
| ☐ | Fill in every Pass/Fail above and note failures with times |

## What this session cannot test (known limits)

- **Collision avoidance (Sprint 4 ORCA) does not command real drones.** Separation is by different altitudes, areas and RTL heights. Never fly two drones at the same altitude on crossing lanes.
- **Object detection** runs on the simulator's camera only; real camera frames aren't analysed yet.
- **Wi-Fi / LTE / LoRa failover** (Sprint 4) is a simulated link model, not the real radios.
- The bridge supports **ArduCopter only** (not ArduPlane or Rover).

| Session result | ☐ All passed ☐ Passed with issues ☐ Aborted |
|---|---|
| Signed: Operator | ____________________ |
| Signed: Safety pilot | ____________________ |
