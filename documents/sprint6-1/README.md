# Sprint 6.1: Flying Real Drones with Drone MatrX

**What this is.** Sprint 6.1 connects Drone MatrX to **real ArduPilot drones**. Until now every drone in the app was simulated. With this release:

- you set up each drone **inside Drone MatrX** on the **Drone Setup** page: no Mission Planner, no config files, no tokens to copy;
- your drones appear on the map and in the fleet, and their cameras on the video wall;
- you can arm them, fly them, send them to a point and fly CBBA-planned missions from **Mission Control**.

Three safety rules are enforced in code:

1. **Nothing flies without a person granting permission.** Arming, take-off, go-to and starting a mission are refused until an operator grants a **flight permission**. Granting needs 5 confirmations, and the permission expires after at most 30 minutes.
2. **The RC pilot always wins.** Drone MatrX never overrides the transmitter. If the pilot flips the mode switch, a failsafe triggers, or someone arms with the sticks, Drone MatrX **locks itself out** of that drone until someone releases it with a written reason.
3. **Hard limits:** 30 m altitude and 150 m from take-off, checked before anything is sent.

> **About the screenshots.** They come from the real Drone MatrX software (bridge, backend and browser) connected to **practice vehicles** (`hwbridge/tools/practice_vehicle.py`). These are small programs that answer like an ArduCopter so the workflow can be shown without aircraft. **They are not real drones or real flights.** The video tiles show labelled TEST PATTERNS instead of real cameras.

**Field checklist:** [`documents/hardware_test/PHYSICAL_DRONE_TEST_SCRIPT.md`](../hardware_test/PHYSICAL_DRONE_TEST_SCRIPT.md) has every test with pass/fail boxes. Print it.

---

## Contents

1. [How it works](#1-how-it-works)
2. [What you need](#2-what-you-need)
3. [Start Drone MatrX on the laptop](#3-start-drone-matrx-on-the-laptop)
4. [Drone Setup: start the service and connect the radios](#4-drone-setup-start-the-service-and-connect-the-radios)
5. [Drone Setup: the 12 steps for each drone](#5-drone-setup-the-12-steps-for-each-drone)
6. [Prepare the cameras](#6-prepare-the-cameras)
7. [Manual alternatives (advanced)](#7-manual-alternatives-advanced)
8. [Operating real drones: the full workflow](#8-operating-real-drones-the-full-workflow)
9. [Flying a CBBA mission with two drones](#9-flying-a-cbba-mission-with-two-drones)
10. [Emergencies](#10-emergencies)
11. [Troubleshooting](#11-troubleshooting)
12. [Practice without drones](#12-practice-without-drones)
13. [What is not supported yet](#13-what-is-not-supported-yet)

---

## 1. How it works

```
 Drone 1 (ArduPilot) ─ air radio ))) ((( ground radio ─ COM5 ┐
 Drone 2 (ArduPilot) ─ air radio ))) ((( ground radio ─ COM6 ┤ USB hub
                                                             ▼
                         ┌──────────── LAPTOP ─────────────────────────────┐
                         │ connection service (hardware bridge)             │
                         │   • the only program that talks to the drones    │
                         │   • setup: settings, calibration, radio pairing  │
                         │   • flight: permission, override, limits         │
                         │   • telemetry ──► NATS ──► backend               │
                         │ backend (FastAPI) ◄──── setup, commands ─────────┤
                         │ browser: Drone MatrX → Drone Setup / Mission Ctl │
                         │ MediaMTX ◄── pulls each camera's RTSP stream     │
                         └──────────────────────────────────────────────────┘
 Camera on each drone ──── Wi-Fi (RTSP) ────────────► MediaMTX ──► video wall
```

| Part | Job |
|---|---|
| **ArduPilot** on each drone | Flies the drone and runs its own safety checks and failsafes |
| **Telemetry radios** | Carry MAVLink between each drone and the laptop |
| **Connection service** (`hwbridge/`, "hardware bridge") | The only program that talks to the drones. Runs on the laptop because Docker can't open COM ports. Started from the Drone Setup page. Refuses anything unsafe on its own. |
| **Backend** | Setup actions, pre-arm checklist, permissions, commands, audit log, missions |
| **Drone Setup** page (browser) | Connect radios and prepare each drone, step by step |
| **Mission Control** page (browser) | Where you fly |
| **MediaMTX** | Fetches each camera's RTSP stream and plays it in the browser |

Every flight command passes **two safety gates**: the backend and then the bridge. Each checks permission, override and limits independently. Every setup change is written to the **audit log** with your name.

---

## 2. What you need

| Item | Notes |
|---|---|
| 2+ multirotors with **ArduCopter 4.x** firmware installed | ArduPlane, Rover and PX4 are not supported. Installing firmware on a brand-new board is the one job Drone MatrX does not do (see section 13). |
| One telemetry radio **pair** per drone | SiK / 3DR / Holybro / RFD900. Or connect a drone with its USB cable for setup. |
| USB hub | All ground radios plug into it |
| One RC transmitter per drone, with a safety pilot | 3-position mode switch and a motor emergency stop switch |
| An RTSP camera on each drone, on the same Wi-Fi as the laptop | IP camera or companion computer (Raspberry Pi, Jetson) |
| Laptop | Windows, Docker Desktop, this repository. **Mission Planner is not needed.** |
| Charged batteries | Pre-arm needs **≥ 50 %** for real drones |

---

## 3. Start Drone MatrX on the laptop

Do this once at the start of each session. Start **Docker Desktop** and wait until it says *running*. Then, in PowerShell:

```powershell
cd "D:\Drone MatrX"
ipconfig            # note the Wi-Fi "IPv4 Address", e.g. 192.168.1.14
$env:MEDIAMTX_WEBRTC_HOSTS = "192.168.1.14"      # your IPv4 address
docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d --force-recreate db nats mediamtx
```

`MEDIAMTX_WEBRTC_HOSTS` must be the laptop's **current** Wi-Fi IP, or every video tile shows **VIDEO LINK LOST**. Run these lines again whenever you change network.

Window 1, the backend (the first time only, `alembic upgrade head` adds the real-aircraft tables):

```powershell
cd "D:\Drone MatrX\backend"
venv\Scripts\alembic upgrade head
venv\Scripts\python -m uvicorn app.main:app --port 8000
```

Window 2, the website: `cd "D:\Drone MatrX\frontend"; npm run dev`, then open **http://localhost:5173/login** and log in as an **operator** or **admin**.

That's all the typing. Everything else happens in the browser.

> **Close Mission Planner** (and QGroundControl) if it is open. Only one program can use a radio's COM port at a time. Check Task Manager that `MissionPlanner.exe` is gone.

---

## 4. Drone Setup: start the service and connect the radios

Open the menu (☰) → **Drone Setup**. The page has three sections; work through them top to bottom. **Keep the propellers OFF for the whole setup.**

### 4.1 Start the drone connection service

Press **Start service**. Drone MatrX starts the connection service on this laptop and shows **RUNNING** within a few seconds. The service creates its own settings file and security token on first start, and the backend reads the token automatically: nothing to copy.

![Start the connection service](screenshots/S01_setup_start_service.png)

If it shows a log instead of RUNNING, the most common cause is another program holding the port the service uses: close other copies of the bridge, or restart the laptop.

### 4.2 Connect the radios

1. Plug every **ground radio** into the laptop (a USB hub is fine). Each radio appears as its own **COM port**, labelled *Telemetry radio*. A flight controller plugged in by USB cable appears as *Flight controller (USB cable)*.
2. Power on the drones and the RC transmitters.
3. Press **Connect** on each port. The speed (baud rate) is detected automatically. Within a few seconds the row says *1 drone(s) found*.

![Connect the radios](screenshots/S02_setup_connect_radios.png)

In the screenshot the practice vehicles are connected as **network connections** (`tcp:…`). With real radios you'll see `COM5`, `COM6` rows with **Connect** buttons. The **Network connection** section is for Wi-Fi telemetry or a companion computer (`udp:0.0.0.0:14550`).

If a port says **In use by another program**, close Mission Planner (or any other ground station) and press Connect again. If no port appears, install the radio's driver (**CP210x** from Silicon Labs or **FTDI**) and press the refresh button.

Connections are remembered: next time the service starts, it reconnects them by itself.

### 4.3 Choose a drone

Every connected drone appears as a button. New flight controllers all come with **ID 1**, so the page warns you when two drones share an ID. Fix that first (step 1 below).

![Drones found, duplicate ID](screenshots/S03_setup_drones_found_duplicate_id.png)

---

## 5. Drone Setup: the 12 steps for each drone

Pick a drone. The left column lists the 12 steps. Each step gets a **green tick only when the drone itself reports it is done** (Drone MatrX reads the drone's settings back), so the list is always the truth, even after closing the page. *Optional* steps can be skipped; *Not needed* means it doesn't apply to this drone (for example radio pairing on a USB cable). **Back** / **Next step** move through them in order.

Every change is refused while the drone is **armed**, and every change is confirmed by the drone before the page says it worked.

### Step 1: Unique drone ID

Drone MatrX tells drones apart by their MAVLink ID, so every drone needs its own. The page suggests the lowest free ID. Press **Set ID & restart**: the drone saves the ID, restarts, and reappears as "Drone ID 2" after about 15 seconds. Select it again and continue. **Write the ID on the drone with tape.**

![Unique ID](screenshots/S04_setup_unique_id.png)

*(Connect all your drones before this step, so the page can see which IDs are taken.)*

### Step 2: Frame type

Pick the number of motors (Quad, Hexa, Octa, ...) and the layout (most quads are **X**). **Save frame**. It takes effect after the restart in step 11.

![Frame type](screenshots/S05_setup_frame.png)

### Step 3: Telemetry radio pairing

Two radio pairs on the same network ID mix their data, so each pair needs its own. With the drone powered on:

1. **Read radio**: shows the radio version and the network ID of the ground radio and of the air radio on the drone.
2. **Pair** with the suggested ID (25, 26, 27 ...; IDs used by your other radios are listed). The air radio is changed first: if the drone's radio doesn't answer, **nothing is changed**. Both radios save the ID and restart (about 5 seconds).

Drone MatrX pauses that connection while it talks to the radio, then reconnects. This step shows *Not needed* for drones on a USB cable or network connection.

### Step 4: Accelerometer calibration

Propellers off. Press **Start calibration**. The page shows a position in large letters with an explanation (**LEVEL**, **LEFT** side down, **RIGHT** side down, **NOSE DOWN**, **NOSE UP**, upside down on its **BACK**). Hold the drone still in that position and press **Done, next position**. While it says *Measuring, hold still...* keep it still. After the sixth position it says **Accelerometer calibration successful**.

![Accelerometer position](screenshots/S06_setup_accel_position.png)

![Accelerometer done](screenshots/S07_setup_accel_done.png)

**Level only (quick)** re-trims "level" without the six positions; use it only if a calibrated drone drifts.

### Step 5: Compass calibration

Outdoors, away from cars, metal tables and reinforced concrete. Press **Start compass calibration**, then pick the drone up and slowly turn it so every side points at the ground once (nose down, tail down, left, right, top, bottom), until the bar is full and says **SUCCESS**. The result is saved automatically; restart in step 11.

![Compass calibration](screenshots/S08_setup_compass_progress.png)

If it fails, move further from metal and turn more slowly.

### Step 6: RC transmitter calibration

The bars show the live sticks and switches from the drone's receiver. Turn the transmitter on, throttle down, then:

1. Press **Start**.
2. Move both sticks slowly to every corner, and every switch through every position. Each stick's bar fills with a green range.
3. When all four sticks are green, **centre the sticks with the throttle DOWN** and press **Save calibration**.

![RC calibration](screenshots/S09_setup_rc_calibration.png)

No bars move? The transmitter is off or not bound to the receiver.

### Step 7: Flight mode switch

The safety pilot takes over with the mode switch, so it must hold the recovery modes. **Use recommended modes** sets a 3-position switch to:

| Switch | Mode | What the drone does |
|---|---|---|
| Down | **ALT_HOLD** | Pilot flies, the drone holds its height |
| Middle | **LOITER** | Holds its position (GPS) |
| Up | **RTL** | Flies home and lands |

Flip the switch: the highlighted box follows it, so you can check every position.

![Flight modes](screenshots/S10_setup_flight_modes.png)

### Step 8: Safety settings

One button sets every failsafe Drone MatrX's pre-arm checklist requires:

| Situation | Drone does |
|---|---|
| Laptop / radio link lost for 5 s | Returns home |
| RC transmitter lost | Returns home |
| Battery low | Returns home |
| Battery critical | Lands immediately |
| Leaves a 150 m circle or climbs above 40 m (fence) | Returns home |
| Return height | 15 m for ID 1, 20 m for ID 2, 25 m for ID 3 ... (different per drone, so returning drones don't meet) |

Enter the battery's **cell count** (the page guesses it from the voltage: a 4S pack is about 16 V), check the low/critical voltages shown, and press **Apply safety settings**. If the page says the **battery monitor is off**, press **Enable battery monitor** (standard power module) and restart in step 11.

![Safety settings](screenshots/S11_setup_safety.png)

### Step 9: Motor test (optional)

**Remove all propellers.** Tick the confirmation, then press **Motor A**, **Motor B** ... Each spins one motor at 7 % for 2 seconds. Check each motor spins and in the right direction (on an X quad, motor A is front-right, then clockwise). The test is refused unless the box is ticked, and limited to 15 % for 3 s.

![Motor test](screenshots/S12_setup_motor_test.png)

### Step 10: Camera (optional)

Paste the camera's RTSP address (section 6), for example `rtsp://192.168.1.101:8554/live`, and press **Save camera**. It appears on the Mission Control video wall once the drone is added (step 12).

### Step 11: Reboot & final check

Press **Restart flight controller** so frame and calibration changes take effect, and wait about 15 seconds. The page then lists anything ArduPilot still complains about, **with a plain-language fix** (for example *"Compass not calibrated: do step 5, then restart"*, *"Need 3D Fix: go outdoors and wait for the GPS"*). When the list is empty it says **The flight controller reports no pre-arm problems**.

![Final check](screenshots/S13_setup_final_check.png)

Indoors, GPS messages are normal; they clear outdoors after a few minutes.

### Step 12: Add to Drone MatrX

Choose **New drone** (or an existing real-aircraft record), check the name and UIN, and press **Add & link**. Drone MatrX:

- registers the drone as a **real aircraft** (it never receives simulated positions),
- adds a camera payload (status *calibrated*) if you saved a camera, so CBBA can plan camera tasks for it,
- links the flight controller to the record.

All required steps now show a green tick.

![All steps done](screenshots/S14_setup_registered_all_done.png)

**Repeat steps 1–12 for each drone**, then continue with section 8 in Mission Control.

> **Before the first flight at a new field**, draw an **inclusion geofence** around the flying area in **Settings**. The pre-arm checklist fails "Outside every inclusion geofence" until you do.

---

## 6. Prepare the cameras

Drone MatrX shows any **RTSP** stream. It does not need drivers or apps for the camera.

1. Connect each camera (or the drone's companion computer) to the **same Wi-Fi** as the laptop. A phone hotspot or travel router works in the field.
2. Find its RTSP URL in the camera's manual or app. Typical forms:
   `rtsp://192.168.1.101:8554/live`, `rtsp://user:pass@192.168.1.101:554/stream1`
3. Test it in **VLC**: Media → Open Network Stream → paste the URL. You should see live video.
4. Prefer **H.264** video. Browsers may not play H.265 over WebRTC; switch the camera to H.264 if the tile stays black.
5. Paste the URL in **Drone Setup → step 10** for that drone.

---

## 7. Manual alternatives (advanced)

You don't need any of this for the normal workflow; it is here for unusual setups.

- **Starting the service by hand** (for example on another laptop): `backend\venv\Scripts\python -m hwbridge.server`. Its settings live in `hwbridge\config.json` (created on first start; git-ignored because it holds your camera addresses) and its token in `hwbridge\.bridge_token`, which the backend reads automatically. If you set `HWBRIDGE_TOKEN` in `backend\.env`, that value is used instead and must match the `"token"` in `config.json`.
- **Mission Planner still works** for anything the Drone Setup page doesn't cover (firmware installation, ESC calibration, advanced tuning). Stop the connection service first (**Drone Setup → Stop service**), use Mission Planner, close it, then start the service again.
- **Registering and binding by hand:** Fleet → **+ New Drone** with **"Real aircraft (hardware)"** ticked, then Mission Control → **Flight controllers → Auto-discover → Bind**. This is what step 12 does for you.

![Register a real aircraft](screenshots/01_register_real_aircraft.png)

![Discover ArduPilot controllers](screenshots/02_discover_ardupilot_controllers.png)

![Bound to the hardware drones](screenshots/03_bound_to_hardware_drones.png)

---

## 8. Operating real drones: the full workflow

Select a drone in the fleet list (left). Its control panel shows the **pre-arm checklist**, the **Real aircraft safety** box and the command buttons.

### 8.1 Pre-arm checklist

![Pre-arm and safety panel](screenshots/04_prearm_and_safety_panel.png)

Every required check must be **PASS** before arming. Real aircraft get three extra checks:

| Check | Passes when |
|---|---|
| Flight controller connected / Telemetry fresh | Heartbeat and data within the last 5 s |
| Correct drone identity | The answering controller is the one bound to this drone |
| Flight mode | Not armed, not in RTL / LAND / AUTO / BRAKE |
| On the ground | ArduPilot reports landed |
| Battery | **≥ 50 %** (stricter than in simulation) |
| GNSS fix and home | 3D fix, ≥ 6 satellites, home set |
| Estimator healthy | ArduPilot's AHRS healthy |
| Firmware pre-arm checks | ArduPilot's own checks pass (its message is shown if not) |
| Geofence / no-fly zones | Inside an inclusion geofence and outside any NFZ. **Before the first flight at a new site**, draw an inclusion geofence around the field in **Settings**. |
| Failsafes configured | GCS-loss, RC-loss and low-battery failsafes are on |
| Simulation / hardware confirmed | You confirm "REAL HARDWARE" in the dialog |
| **RC transmitter connected** | The RC receiver is healthy, so manual takeover is possible |
| **No pilot / autopilot override** | Drone MatrX may send commands |
| **Human flight permission** | Shown here; enforced when you press ARM |

If the safety pilot's **transmitter is off**, arming is blocked:

![RC transmitter off blocks arming](screenshots/05_rc_transmitter_off_blocks_arming.png)

### 8.2 Grant the flight permission

Press **Grant flight permission** in the red **Real aircraft safety** box. Tick all five confirmations (the button stays disabled until you do), choose how long (1–30 min), and press **Grant permission**.

![Grant flight permission](screenshots/06_grant_flight_permission.png)

The box then shows **FLIGHT PERMITTED** with a countdown. **Revoke** ends it at once. When the time runs out, flight commands are refused again; RTL, LAND and HOLD always stay available.

![Flight permitted](screenshots/07_flight_permitted.png)

Each grant is recorded with your name, the time and the confirmations. The bridge also holds the permission's expiry and refuses on its own once it passes.

### 8.3 Arm and take off

Press **Arm**. The confirmation dialog warns that this is a **REAL AIRCRAFT** and asks you to confirm the mode. Leave **"take off once armed"** ticked to climb straight to the go-to altitude (ArduPilot disarms on its own if no take-off follows).

![Arm confirmation](screenshots/08_arm_real_aircraft_confirmation.png)

Behind the scenes:

1. The backend re-runs the whole checklist.
2. The bridge switches the drone to **GUIDED** and sends ARM. ArduPilot's own checks still decide; arming is never forced.
3. Once **telemetry** shows ARMED, it sends TAKEOFF.

A command shows **CONFIRMED** only when the drone's telemetry shows the result, never just because a message was sent.

![Airborne](screenshots/09_airborne.png)

### 8.4 Hold, go to, return

- **Hold:** the drone switches to LOITER and stays in place.
- **Go to:** click **Go to**, click the map, set the altitude, **Send GO TO** → confirm. The drone flies there in GUIDED, and the command is CONFIRMED when it's within 6 m.

![Go-to confirmed](screenshots/10_goto_confirmed.png)

- A destination outside the geofence, beyond **150 m**, or above **30 m** is **refused before anything is sent**:

![Go-to beyond the limit refused](screenshots/11_goto_beyond_limit_refused.png)

- **RTL:** returns at `RTL_ALT`, lands and disarms. **Land:** lands where it is.

**After every landing (RTL or Land):** ArduPilot stays in RTL or LAND mode, which can't be armed. Before the next flight:

1. The safety pilot flips the mode switch back to **LOITER** or **STABILIZE**.
2. Drone MatrX sees a mode change it didn't make and shows the override banner. This is expected.
3. The operator types "landed, pilot reset the mode switch" and presses **Release**. The checklist turns green again.

### 8.5 RC takeover by the safety pilot

At any moment the safety pilot can flip the transmitter's mode switch (for example to LOITER or ALT_HOLD). The drone obeys the pilot **immediately**; ArduPilot always prefers the RC mode switch. Drone MatrX notices the mode change it didn't make, shows the red **"PILOT / AUTOPILOT HAS CONTROL"** banner, and **refuses all its own commands** for that drone, so it never fights the pilot.

![Pilot override locks out Drone MatrX](screenshots/12_pilot_override_locks_out.png)

The same lock-out happens when ArduPilot changes mode by itself (a failsafe RTL or LAND) or when someone arms with the sticks.

To hand control back to Drone MatrX: the pilot confirms they're done, the operator types the reason (for example "pilot handed control back") and presses **Release**. The release is audited. Then you can send RTL or other commands again.

![Released, then RTL](screenshots/13_released_then_rtl.png)

### 8.6 Live video

**Video wall** tab → **Refresh**. Each drone's camera appears as **Camera - HW-01**, **Camera - HW-02**, labelled LIVE CAMERA. You can use 1 / 2×2 / 3×3 layouts, fullscreen, picture-in-picture, recording and DVR playback.

![Camera feeds](screenshots/14_camera_feeds.png)

In this practice run the tiles show labelled TEST PATTERNS in place of real cameras.

---

## 9. Flying a CBBA mission with two drones

1. **Plan:** Missions → new survey mission. Draw a **small AOI inside the field** (about 60 × 40 m), altitude 10 m, speed 4 m/s, select **HW-01 and HW-02** → **Run CBBA**. The lanes are split between the two drones.

   ![CBBA plan for the real drones](screenshots/15_cbba_plan_for_real_drones.png)

   *(Recommended: fly the same plan in the Sprint 5 Gazebo simulation first.)*

2. **Upload:** Mission Control → select HW-01 → in the safety box pick the mission, set **Alt 10 m** → **Upload to aircraft**. Select HW-02 → set **Alt 15 m** → Upload.

   **Give every drone its own altitude.** ORCA collision avoidance now also steers real drones (Sprint 6.2), but different heights remain the first layer of separation.

   ![Mission uploaded with per-drone altitude](screenshots/16_mission_uploaded_per_drone_altitude.png)

   Every waypoint is checked against the 30 m / 150 m limits. A mission outside them is refused.

3. **Permission:** grant a flight permission for **each** drone.
4. **Start:** HW-01 → **Start mission** → confirm. Ten seconds later, do the same for HW-02. Each drone arms in GUIDED, starts the mission (ArduPilot AUTO mode), flies its lanes and returns home.

   ![Two drones flying the CBBA mission](screenshots/17_two_drones_flying_cbba_mission.png)

5. **Fleet commands:** **HOLD ALL** stops every flying drone in place, and **RTL ALL** brings them all home. Each drone gets its own confirmed result.

   ![HOLD ALL on real drones](screenshots/18_hold_all_real_drones.png)

   **LAND ALL** (red) lands every drone where it is, after you type `LAND ALL`.

---

## 10. Emergencies

| Situation | Who | Do this |
|---|---|---|
| Anything unexpected | Anyone | Call **ABORT** |
| Drone misbehaving | Safety pilot | Mode switch → **LOITER / ALT_HOLD**, fly it back, land |
| Several drones airborne, operator in control | Operator | **RTL ALL** (or **LAND ALL** if returning is unsafe) |
| Laptop, bridge or radio lost | ArduPilot | GCS failsafe → RTL after 5 s. The pilot watches and takes over if needed. |
| RC transmitter lost | ArduPilot | RC failsafe → RTL |
| Low battery | ArduPilot | Battery failsafe → RTL |
| Fly-away / danger | Safety pilot | **Motor emergency stop** switch |

After any failsafe or takeover, Drone MatrX stays locked for that drone until it's released. Land first, then release.

---

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Drone Setup: port says *In use by another program* | Mission Planner (or another program) holds the COM port | Close it (check Task Manager), press Connect again |
| Drone Setup: *Start service* shows a log, not RUNNING | Another copy of the service is already running, or port 8110 is taken | Close the other copy (or restart the laptop) and press Start again |
| Port connected but no drone found | Radio pair not linked, or drone off | Check the radio LEDs are solid green and the drone is powered; pair the radios (step 3) |
| Only one drone appears, or they swap | Two drones have the same ID | Drone Setup step 1 (unique ID) |
| Data drops when both drones fly | Radio pairs share a network ID | Drone Setup step 3 (radio pairing) |
| Discover shows "Hardware bridge: unreachable" | Connection service not running | Drone Setup → **Start service** (section 4.1) |
| `401` / "Bad or missing X-Bridge-Token" | `HWBRIDGE_TOKEN` in `backend\.env` differs from the service's token | Delete the `HWBRIDGE_TOKEN` line from `backend\.env` (the token is then read automatically) and restart the backend |
| "not registered as a real aircraft" | Binding to a demo record | Register the drone with "Real aircraft (hardware)" ticked |
| Checklist: Geofence FAIL "Outside every inclusion geofence" | No geofence drawn for this field | Settings → draw an **inclusion** geofence around the field |
| Checklist: Failsafes FAIL | A failsafe is off | Drone Setup step 8 (safety settings) |
| Checklist: Firmware pre-arm FAIL with a `PreArm:` message | ArduPilot's own check | Drone Setup step 11 lists it with the fix (calibration, GPS ...) |
| Setup step stays orange after you did it | ArduPilot repeats old warnings for up to 30 s, or needs a restart | Wait 30 s, or do step 11 (restart) |
| Calibration / settings refused `ARMED` | Setup only works disarmed | Land and disarm first |
| Checklist: Flight mode FAIL "Mode LAND" or "Mode RTL" | Drone landed and stayed in RTL/LAND | Pilot flips the mode switch to LOITER / STABILIZE, then **Release** the override (section 8.4) |
| ARM refused `FLIGHT_NOT_AUTHORIZED` | No permission, or it expired | Grant a flight permission |
| Every command refused `PILOT_OVERRIDE` | The pilot or a failsafe took control | Land, then Release with a reason |
| Video tiles: VIDEO LINK LOST | `MEDIAMTX_WEBRTC_HOSTS` isn't the current Wi-Fi IP | Section 3 (re-run the docker line with the new IP) |
| Video tile black, no error | Camera sends H.265, or the RTSP URL is wrong | Test the URL in VLC; switch the camera to H.264 |

---

## 12. Practice without drones

The team can rehearse the whole workflow on the laptop with two **practice vehicles**. These are small programs that answer like ArduCopters: they are not ArduPilot and not a simulator of real flight.

**Practising the Drone Setup page** (two brand-new, unconfigured "drones", both with ID 1):

```powershell
backend\venv\Scripts\python -m hwbridge.tools.practice_vehicle --count 2 --fresh
```

Then Drone Setup → **Start service** → **Network connection** → connect `tcp:127.0.0.1:5760` and `tcp:127.0.0.1:5770`, and do the 12 steps. For step 6 (RC), move the practice "sticks" from another window:
`Invoke-WebRequest -Method Post "http://127.0.0.1:5799/sticks?sysid=2&mode=sweep"` (and `mode=center` before **Save calibration**). Disconnect the two `tcp:` connections when you are done, so they aren't reconnected next time.

**Practising flying** (two already-configured practice drones):

```powershell
backend\venv\Scripts\python -m hwbridge.tools.practice_vehicle --count 2
backend\venv\Scripts\python -m hwbridge.server --config hwbridge\config.practice.json
# backend\.env:  HWBRIDGE_TOKEN=practice-token     (restart the backend)
```

Play the safety pilot from another window:

```powershell
Invoke-WebRequest -Method Post "http://127.0.0.1:5799/pilot?sysid=1&mode=LOITER"   # flip the RC mode switch
Invoke-WebRequest -Method Post "http://127.0.0.1:5799/rc?sysid=1&on=0"             # transmitter off (on=1 back on)
```

The screenshots in this folder were produced this way, by `frontend/e2e/drone-setup-walkthrough.spec.ts` (S01–S14) and `frontend/e2e/hardware-walkthrough.spec.ts` (01–18), run with `npx playwright test -c playwright.hw.config.ts`.

---

## 13. What is not supported yet

- **Collision avoidance:** since Sprint 6.2 the simulator's ORCA also steers real drones that Drone MatrX is flying (see `documents/sprint6-2/README.md` section 1b). Still keep drones apart with different altitudes, areas and `RTL_ALT`: ORCA is a second layer.
- **Object detection** works on the simulator's camera only; real camera frames aren't analysed yet.
- **Wi-Fi / LTE / LoRa failover** (Sprint 4) is a simulated link model, not your radios.
- **ArduCopter only** (no ArduPlane, Rover or PX4 hardware yet).
- **Not in Drone Setup:** installing firmware on a brand-new board, ESC calibration and PID tuning. Most drones ship with ArduCopter installed; for the rest, see section 7.
- **SiK radio pairing (step 3) was tested against a simulated radio only**; the AT-command sequence follows the SiK documentation. If it fails on your radios, pair them as before and tell us the radio model.
- Software in this folder was tested against practice vehicles and automated tests. The **first contact with real ArduPilot firmware is your bench test (props off)**. Do the Drone Setup page with propellers off, then follow the test script in order.
