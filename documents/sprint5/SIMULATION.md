# Sprint 5 — Gazebo + PX4 SITL mission simulation

Technical reference for the end-to-end simulation added in Sprint 5. Test
results, measurements and screenshots are in
[`SPRINT_5_TEST_REPORT.md`](SPRINT_5_TEST_REPORT.md); evidence files live in
[`documents/sprint5/`](../../documents/sprint5/).

## 1. What it does

An operator selects a real Area of Interest on the existing Leaflet map, picks
how many drones fly and what they do, and Drone MatrX:

1. plans the task-type specific work and allocates it with the existing **CBBA** engine (Sprint 3),
2. generates a **Gazebo Harmonic** world of that same AOI from real elevation data,
3. spawns one **PX4 SITL** flight controller per drone in that world,
4. uploads each drone's CBBA route as a MAVLink mission and flies it,
5. streams the simulator's telemetry through the existing **NATS → WebSocket** pipeline
   onto the same Leaflet map,
6. tracks task completion from PX4 `MISSION_ITEM_REACHED`, reacts to injected
   failures, and
7. writes a simulation report (with a diff against earlier runs).

The mission created in Drone MatrX is the source of truth; nothing is animated
in the browser.

## 2. Architecture

```text
 React (Simulation page, existing MissionMap)          single /ws/telemetry socket
        │ REST /simulations/*                                   ▲
        ▼                                                       │ telemetry · simulation · simulation_event
 FastAPI backend (Windows host)                                 │
   MissionPlanningService ── wizard steps, task planners, CBBA (unchanged engine)
   SimulationService ──────── fleet staging, session, world generation, preflight
   SimulationOrchestrator ─── launch, FlightControllerAdapter, progress, failure matrix, report
        │ HTTP (simhost API)                 ▲ NATS JetStream `ui` consumer (FleetRuntime)
        ▼                                    │ drone.{UIN}.telemetry / drone.{UIN}.alert
 simhost container (Ubuntu 22.04)            │
   SitlProcessManager ── PX4 SITL v1.15 × N (px4 -i N, PX4_GZ_STANDALONE)
   GazeboWorld ───────── gz sim server (Harmonic) + gz-transport control
   DroneLink × N ─────── pymavlink: missions, commands, params, telemetry
   LinkEmulator ──────── latency / loss / link-down at the bridge boundary
```

Key properties:

* **Same drone ids everywhere.** Simulation drones are rows of the existing
  `drones` table (`simulated = true`, `SIM-01…SIM-16`). The id selected in the
  wizard is the CBBA agent id, the `drone_id` in every telemetry frame, and
  the key on the map; the PX4 instance index and Gazebo model (`x500_<i>`)
  are stored per session in `simulation_drone_instances`.
* **Same command path.** The orchestrator only calls
  `FlightControllerAdapter` (`arm, takeoff, hold, goto, set_mode,
  return_to_launch, land, upload_mission, start_mission, abort`).
  `SimulationAdapter` refuses live missions and real drones;
  `HardwareAdapter` refuses simulation missions/drones and requires explicit
  operator authorization with passed safety checks (no hardware transport is
  configured, so it reports `HARDWARE_UNAVAILABLE` rather than pretending).
* **Same telemetry path.** simhost publishes `drone.{UIN}.telemetry` /
  `drone.{UIN}.alert` to the existing JetStream stream. The existing `ui`
  consumer updates the drone rows and broadcasts `telemetry` frames; the
  existing `telemetry-writer` stores them in TimescaleDB. The orchestrator is
  an extra listener on that same consumer.
* **Simulation is explicit.** Missions carry `execution_mode = simulation`;
  the UI shows a SIMULATION badge in the title bar, mission cards and markers;
  the in-process demo simulator never animates `simulated` drones.

## 3. Running it

Prerequisites: Docker Desktop (Linux containers), the Sprint 4 setup in the
root README.

```bash
# database + NATS (existing) and the simulation host (new)
docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d --build
# first build compiles PX4 SITL: ~20-40 min; later starts are seconds

cd backend
alembic upgrade head            # adds the Sprint 5 tables/columns
uvicorn app.main:app --port 8000
cd ../frontend && npm run dev
```

`GET http://localhost:8100/health` reports the Gazebo and PX4 versions, NATS
connection and capacity (`SIM_MAX_INSTANCES`, default 8 — see Limitations).

In the app: **Missions → name → "New Simulation (Gazebo + PX4 SITL)"**. The
Simulation page walks through:

| Step | What happens |
|---|---|
| 1 Mission task | wizard step 1 template: area survey, search & rescue, agriculture, inspection, custom waypoints |
| 2 AOI | draw polygon/rectangle on the map, or reuse a saved AOI; area in km²; GeoJSON shown; saved as wizard step 2 |
| 3 Drones | 1/2/3/4/8/16 (options above simulator capacity are disabled; the API refuses rather than silently reducing). Places `SIM-xx` drones on launch pads inside the AOI (wizard step 4) |
| 4 Flight parameters | altitude, speed, overlap / lane spacing / SAR cell size, turn behaviour, RTH battery, min launch battery, max flight time, link-loss policy, simulation speed, terrain source → wizard steps 3+5 → **CBBA** |
| 5 World | AOI → DEM → heightmap → Gazebo SDF (terrain source and fallback status shown) |
| 6 Preflight & launch | checklist; **Start simulation** only when every check passes |

While running: pause / resume, 1×/2×/5×/10×, stop, abort; failure injection;
live fleet table and event timeline. When it ends the report replaces the
setup panel (compare with any earlier report; "Re-run" copies the mission for
a comparable run).

## 4. Task-type planning

| Task | Planner | Task unit |
|---|---|---|
| Area survey | lawnmower lanes, spacing = sensor swath × (1 − overlap) | lane segment |
| Mapping | crosshatch (two perpendicular passes) | lane segment |
| Search & rescue | AOI split into square cells (auto ≈ √(area/12), 60–400 m, or set); each cell swept with a creeping line | search cell (completed = cell searched) |
| Agriculture | parallel rows at the **explicit** lane spacing | row segment |
| Inspection | route through targets picked on the map (hover per target) | target |
| Custom | ordered custom waypoints picked on the map | waypoint |

All of them feed the unchanged CBBA engine; search cells pass their real
sweep length (`Task.path_length_m`) so CBBA costs them correctly.

## 5. AOI → Gazebo world

`backend/app/services/simulation/world.py`, per session in
`backend/sim_workspace/<session_id>/` (bind-mounted as `/workspace`):

1. ENU frame at the AOI centroid (see §6).
2. Square extent covering AOI + launch pads + 150 m margin.
3. DEM sampled on a (2ᵏ+1)² grid (65–513 px): **AWS Terrain Tiles**
   (Terrarium PNG; SRTM 30 m over most land, NED/ETOPO1 elsewhere), zoom
   chosen for ≤ 10 m pixels, tiles cached in `backend/sim_cache/dem/`.
   Corrupt tiles are rejected and never cached.
4. Heights relative to the origin elevation → 16-bit heightmap PNG
   (row 0 = north edge; verified in Gazebo by the sloped-terrain test).
5. Texture: hypsometric tint + hillshade with the AOI outline and launch pads.
6. SDF (Harmonic plugins: Physics, UserCommands, SceneBroadcaster, Contact,
   IMU, AirPressure, NavSat, Magnetometer, ApplyLinkWrench, Sensors) with
   `<spherical_coordinates>` = frame origin, the terrain model, AOI boundary
   markers and launch pads.
7. `metadata.json`: frame, DEM provenance, resolution, timings, spawn poses.

**Heightmap placement (measured in gz-sim 8.15).** Heights are stored relative
to the lowest sample (`zmin`). Gazebo Harmonic treats the two heightmap
geometries differently: the *collision* ignores the heightmap `<pos>` but
follows the model pose, while the *visual* (ogre2) ignores the model pose but
follows `<pos>`. The terrain model therefore has pose `z = zmin`, collision
`<pos>0 0 0</pos>` and visual `<pos>0 0 zmin</pos>`. Checked in the
world-loads Gazebo test on sloped fixture terrain: the rest heights of a north
and a south drone differ by 9.69 m, against 9.6 m expected.

**Launch pads.** PX4's preflight attitude check fails when a drone spawns on
a DEM slope, so each drone gets a level static pad (3 m × 3 m collision box)
whose top is 0.05 m above the highest terrain sample under it. The spawn
pose sits on the pad top (`SpawnPose.pad_top_z`).

If no DEM data can be fetched the world is generated on **flat terrain** and
labelled `FALLBACK` everywhere (world panel, report). Tests use a
deterministic analytic fixture; real DEM tests are marked `real_dem`.

## 6. Coordinate frames

`backend/app/services/simulation/geo.py` (also mounted into simhost as
`simhost/enu.py`, so both sides use the same code):

* **Origin:** AOI area centroid (lat₀, lon₀); h₀ = DEM elevation there (m MSL).
* **Axes:** ENU — +X east, +Y north, +Z up (Gazebo world frame,
  `world_frame_orientation = ENU`). PX4 uses NED internally; it receives its
  home from Gazebo's NavSat at the same origin.
* **Units:** metres / degrees, WGS84.
* **Forward:** geodetic → ECEF → rotate into the tangent plane at the origin.
* **Inverse:** ENU → ECEF → geodetic (Bowring start, Newton refinement).
* **Altitudes:** Gazebo z = terrain height − h₀; mission altitudes are
  relative to the launch point (PX4 `GLOBAL_RELATIVE_ALT`).

Round-trip error < 1 mm (tests across 9 regions incl. the antimeridian and
a UTM zone boundary — ENU has no zone boundaries).

## 7. SITL process manager

`sim/simhost/sitl_manager.py`. One `px4 -i N -d -w <session>/run/px4_N`
process per drone with `PX4_GZ_STANDALONE=1` (attach to the session's
Gazebo), `PX4_GZ_MODEL_POSE` from the world generator, `PX4_SIM_MODEL=gz_x500`.

* Instance N ↔ MAVLink API port 14540+N; an index is only allocated if that
  port is free; one index cannot be used by two sessions.
* Monitor thread (4 Hz): an exit not requested by `stop` is a **crash**,
  reported once (`process_exited`, never respawned automatically); a process
  that never becomes ready within the startup timeout is reported too.
* `stop`: SIGTERM to the process group, SIGKILL after 8 s.
* Every PX4 process carries `DM_SIM_SESSION=<id>`; `sweep_orphans()` (run at
  simhost start-up) kills marked processes it does not track;
  `GET /processes` lists them for inspection.
* Capacity: `SIM_MAX_INSTANCES` (refused above it — never silently fewer).
* **Dead flight controller → motors stop.** Gazebo's `MulticopterMotorModel`
  keeps the last motor command it received. When a PX4 process dies
  unexpectedly, simhost publishes zero motor speeds on
  `/x500_N/command/motor_speed`, so the drone falls as a real one would.
  Without this, a drone killed in flight kept its last thrust and flew off.
  In one E2E run it reached x = 5.9 × 10¹⁶ m, and ODE then aborted the
  Gazebo server (`aabbBound` assertion). This is covered by
  `test_airborne_crash_falls_and_world_survives`, which fails with the motor
  cut disabled.
* **World watchdog (1 Hz).** If the Gazebo server exits, the session becomes
  `failed` with the exit code, and the orchestrator ends the run as `failed`
  (`simulator_failed` event). Before this, the session kept reporting
  `running` over a frozen world. A model more than 20 km from the origin is
  removed (`model_removed` event) before it can destabilise the physics.
* Start-up: PX4 is started under `stdbuf -oL -eL` so its log is line-buffered
  into `run/px4_N/px4.log`. An instance that sends no heartbeat within the
  start-up timeout is restarted once (an intermittent PX4 start-up hang seen
  on this host); both attempts are reported in `startup_attempts` and
  `startup_errors`.

### MAVLink bridge (`sim/simhost/bridge.py`)

pymavlink, one `DroneLink` per instance:

* **GCS heartbeat at 10 Hz wall time.** PX4 measures data-link loss in
  *simulated* time, so at 10× a 1 Hz wall heartbeat looks like one every
  10 sim-seconds. `COM_DL_LOSS_T = 10` s.
* **Missions** use the MAVLink mission protocol. `DO_CHANGE_SPEED` and
  `RETURN_TO_LAUNCH` items must use `MAV_FRAME_MISSION`; PX4 rejects them in
  a global frame (`MAV_MISSION_RESULT 3`).
* **Arming** is retried until PX4's preflight checks pass (EKF convergence),
  then MISSION mode is commanded. A mode change only counts once a heartbeat
  *newer than the command* reports it.
* Every upload bumps a **mission generation** token carried in telemetry, so
  a replan never inherits "waypoint reached" from the previous mission.
* **Battery** comes from `SYS_STATUS.battery_remaining`. The session sets
  `BAT1_V_LOAD_DROP = 0` because the simulated battery has no voltage sag, so
  PX4's throttle-based sag compensation would read about 17 points high in
  flight (measured: the charge stopped at 21 % with a 4 % floor).
* A deterministic **link emulator** (seeded) applies latency, jitter, loss and
  "link down" to telemetry, alerts and commands.

## 8. Failure responses

Implemented in `backend/app/services/simulation/orchestrator.py`; every row is
recorded as a `simulation_events` row (timestamp, sim time, type, drone,
previous → new state, response, mission outcome).

| Event | Injection / detection | System response | Outcome |
|---|---|---|---|
| Kill drone / SITL crash | `SIGKILL` of the PX4 process / process monitor | motors cut in Gazebo (the drone falls); drone `FAILED` → unavailable (status critical); its unfinished tasks re-allocated with **CBBA warm-start** to the flying drones; new airborne missions uploaded (HOLD → upload → MISSION) | replanned (aborted when no drone remains) |
| Link degraded | operator sets latency / loss | link emulator delays/drops telemetry, alerts and commands deterministically; status WARNING | continued |
| Link lost | operator "link down" / no telemetry for 3 s | drone `comm_lost`; GCS heartbeats stop, so PX4 runs its own data-link-loss action (`NAV_DLL_ACT` = mission link-loss policy) | continued (replanned if PX4 leaves the mission) |
| Left mission | PX4 in RTL/LAND with unfinished tasks | tasks re-allocated to the remaining fleet | replanned |
| Low battery | PX4 battery simulator forced down (`SIM_BAT_MIN_PCT`, `SIM_BAT_DRAIN`); detection at the RTH reserve. PX4's own failsafe (`COM_LOW_BAT_ACT = 3`) returns at critical (7 %) and lands at emergency (5 %) | RTL commanded through the adapter + tasks re-allocated | replanned |
| GPS drift | bias on PX4's simulated GPS measurement via `SIM_GPS_DRIFT_N/E`. These are parameters added by the image's PX4 patch (`sim/px4_patches/gps_drift.py`); stock PX4 v1.15 + Gazebo has no GPS failure injection. EKF2 follows the biased GPS after 12–15 s of simulated time (innovation gate, then fusion-timeout reset) | true (Gazebo) vs reported (PX4) error tracked; alert above 5 m | continued |
| Obstacle | click on the map → ENU → Gazebo `EntityFactory` | existence and map→Gazebo→map position verified; collisions detected from ground truth (**no obstacle avoidance in the PX4 mission path**) | continued |
| Wind gust | horizontal force on each `base_link` (`ApplyLinkWrench`, drag model) for N s | cross-track deviation from the planned route measured from ground truth | continued |

## 9. Simulation speed and the FPS guardrail

* Speed = Gazebo physics `real_time_factor` (`/world/<w>/set_physics`); PX4 runs
  in lockstep with the Gazebo clock, so the whole simulation speeds up —
  nothing in the browser is time-scaled.
* Pause/resume = `/world/<w>/control`; PX4 stops with the clock.
* **Guardrail:** `sim_fps = 30 × achieved_RTF / requested_speed` (30 frames of
  simulated motion per wall second at the requested speed). Below 10 for
  3 consecutive 1 s samples → the requested speed steps down one level
  (10→5→2→1) and a `guardrail` event is published; at 1× it only warns.
* **Measured RTF on this host (1 drone):** 1× → 1.00, 2× → 2.0, 5× → 3.8–5.0,
  10× → 4.5–5.6 (varies with host load). 10× is therefore not reached; the guardrail steps it down.
* **1× vs 10× equivalence** is checked on task completion, final waypoint
  reached, coverage and battery use with documented tolerances (PX4 + physics
  are not bit-for-bit deterministic across real-time factors). See the test
  report for measured values.

## 10. API

| Method | Path | Role |
|---|---|---|
| GET | `/simulations/fleet` | any |
| POST | `/simulations/missions/{mission_id}/fleet` | ADMIN/OPERATOR |
| POST | `/simulations` | ADMIN/OPERATOR |
| GET | `/simulations[?mission_id=]`, `/simulations/{id}` | any |
| POST | `/simulations/{id}/world/generate` · GET `/world/status` | ADMIN/OPERATOR · any |
| POST | `/simulations/{id}/preflight`, `/start`, `/pause`, `/resume`, `/speed`, `/stop`, `/abort`, `/failures`, `/rerun` | ADMIN/OPERATOR |
| GET | `/simulations/{id}/events`, `/report`, `/report/candidates` | any |
| POST | `/simulations/{id}/report/compare` | any |

Database (migration `c5e8a1f3d210`): `drones.simulated`,
`missions.execution_mode`, `simulation_sessions`,
`simulation_drone_instances`, `simulation_events`, `simulation_reports`,
`simulation_report_comparisons`. No process environment or credentials are
stored or returned.

## 11. Tests

```bash
cd backend
pytest -q                                   # everything that needs no simulator
pytest -q -m real_dem                       # real DEM download (needs internet)
pytest -q -m gazebo -s tests/test_sim_gazebo_integration.py   # real Gazebo + PX4

docker compose -f docker-compose.yml -f docker-compose.sim.yml exec simhost \
    python3 -m pytest -q /opt/simhost/tests  # SITL manager + link emulator (in the container)

cd ../frontend
npx playwright test e2e/sprint5.spec.ts     # full AOI -> Gazebo -> map -> report run
```

Gazebo tests are reported as skipped with `NOT RUN: simulator unavailable`
when simhost is down — never as passed.

## 12. Known limitations

See the "Limitations" section of the test report for the measured state.
Design-level limits:

* The **16-instance target** is specified for a NUC i7 / 64 GB machine; this
  development host (Docker VM 8 GB) is configured for 8.
* No obstacle avoidance in the PX4 mission path; ORCA (Sprint 4) runs on the
  in-process demo fleet only. Obstacles are detected (collision events), not
  avoided.
* Mission altitude is relative to the launch point, not terrain-following.
* Gazebo runs headless; the GUI (screenshots/recordings) uses Mesa software
  rendering on a virtual display.
* Full list with measurements: [performance/limitations.md](performance/limitations.md).
