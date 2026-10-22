# Sprint 5 — Test Report: Gazebo Simulation Integration

Generated 2026-10-22 04:37 UTC by `backend/scripts/generate_sprint5_report.py` from JUnit XML and measurement JSON in `docs/sprint5/performance/`. No test result in this file is typed by hand; only the Limitations section is written by hand (`performance/limitations.md`).

Evidence: [`documents/sprint5/screenshots/`](../../documents/sprint5/screenshots/) · [`documents/sprint5/recordings/`](../../documents/sprint5/recordings/) · technical reference: [`SIMULATION.md`](SIMULATION.md)

## Environment

| Item | Value |
|---|---|
| os | Microsoft Windows 11 Home Single Language 10.0.26200 |
| cpu | AMD Ryzen 5 5600H with Radeon Graphics |
| ram | 15.3 GB |
| docker | 29.5.3 |
| docker_vm | 12 vCPU / 8.0 GB |
| simulator_container | Ubuntu 22.04 (dronematrx/simhost:dev) |
| gazebo | gz-sim 8.15.0 |
| sitl | PX4 v1.15.4 (gz_x500, + Drone MatrX GPS-drift patch) |
| mavlink | pymavlink 2.4.42 (MAVSDK not used: simhost speaks MAVLink directly) |
| ros2 | not used (no ROS 2 in this stack) |
| simhost_max_instances | 8 |
| python_backend | Python 3.10.11 |
| python_simhost | Python 3.10.12 |
| node | v22.17.0 |
| database | PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2) + PostGIS + TimescaleDB |
| nats | nats-server: v2.10.29 |
| browser | Chromium (Version 1.63.0) |
| test_frameworks | pytest 8.3.4, Vitest, Playwright |

## Test summary

| Test | Result | Duration | Evidence |
|---|---|---|---|
| AOI creation & GeoJSON persistence | **PASS** (2 cases) | 290.4 s | 01, 02 |
| Coordinate transform (WGS84 <-> Gazebo ENU) | **PASS** (31 cases) | 0.0 s | - |
| Task-type planning (survey/SAR/agri/inspection/custom) | **PASS** (7 cases) | 5.1 s | 03 |
| 1 km² world generation (fixture + real DEM) | **PASS** (15 cases) | 15.4 s | 04 |
| Gazebo world loading / terrain / spawn geodesy | **PASS** (1 cases) | 31.1 s | 05 |
| SITL process manager (ports, crash, cleanup, orphans) | **PASS** (16 cases) | 49.6 s | 06 |
| CBBA allocation (existing engine) | **PASS** (42 cases) | 6.0 s | 03 |
| PX4 mission execution (waypoints, RTL, landing) | **PASS** (1 cases) | 147.4 s | 07 |
| Telemetry to Leaflet (marker = simulator telemetry) | **PASS** (1 cases) | 289.8 s | 08, 09, 10 |
| Orchestrator responses (crash, battery, comm, stale progress) | **PASS** (10 cases) | 15.5 s | 12, 13 |
| Failure injection on real simulator (kill, obstacle, wind, link loss, GPS drift, battery) | **PASS** (7 cases) | 333.3 s | 12, 13 |
| Simulation controls (pause/resume/speed, guardrail) | **PASS** (5 cases) | 61.9 s | 11 |
| 1x vs 10x equivalence | **PASS** (1 cases) | 206.3 s | - |
| Report generation & comparison | **PASS** (3 cases) | 87.5 s | 14, 15 |
| Simulation/hardware separation & RBAC | **PASS** (2 cases) | 1.0 s | - |
| Full E2E mission (browser -> Gazebo -> report) | **PASS** (1 cases) | 289.8 s | 16 |

Suites: backend `350 passed, 0 failed, 0 not run` · Gazebo/PX4 integration `12 passed, 0 failed, 0 not run` · simhost container `23 passed, 0 failed, 0 not run` · Playwright E2E `2 passed, 0 failed, 0 not run`.

## World generation

| Measure | Value |
|---|---|
| AOI area (E2E) | 0.0644 km² |
| DEM source | AWS Terrain Tiles (Terrarium; SRTM 30 m / NED / ETOPO1 blend) (fallback: False) |
| DEM native resolution | 8.78 m |
| Heightmap | [129, 129] px, 4.531 m spacing |
| Generation time (E2E world, cached tiles) | 0.1927 s |
| 1 km² real-DEM world | measured by `test_real_dem_one_km2_world` (see backend suite; cold download vs cached) |
| Gazebo load + terrain check | terrain Δz 9.691 m vs 9.6 m expected; startup 30.83 s |

## SITL

| Measure | Value |
|---|---|
| Requested / started (E2E) | 3 / 3 |
| Start-up to RUNNING (E2E, incl. world load, EKF, arming) | 52.85 s |
| Instances | SIM-32e36882-01 udp 14540 x500_0, SIM-32e36882-02 udp 14541 x500_1, SIM-32e36882-03 udp 14542 x500_2 |
| Crash detection | SIGKILL detected, returncode -9 |
| Leftover processes after stop | 0 (integration) / 0 (E2E) |
| Capacity on this host | 8 instances (Docker VM 12 vCPU / 8.0 GB) |

## Coordinate synchronization

| Measure | Value |
|---|---|
| Geographic origin (E2E) | {'height_m': 85.9666836330674, 'latitude': 23.240056518739035, 'longitude': 72.65001296996977} |
| Local frame | ENU at the AOI centroid; X east, Y north, Z up; metres; WGS84 |
| Round-trip error | < 1 mm (`test_sim_coordinates`, 9 regions incl. antimeridian) |
| Initial marker vs spawn pose (E2E) | [0.01, 0.061, 0.027] m |
| Rendered marker vs backend telemetry (E2E) | [0, 0, 0] m |
| PX4-reported vs Gazebo ground truth (E2E) | [0.57, 0.27, 0.05] m |
| PX4-reported vs Gazebo ground truth (integration) | [0.25, 0.05] m |
| Altitudes | Gazebo z = terrain − origin elevation; mission altitude relative to launch (PX4 GLOBAL_RELATIVE_ALT) |

## Simulation execution (E2E mission)

| Measure | Value |
|---|---|
| CBBA | {'tasks': 16, 'assigned': 16, 'drones': 3, 'iterations': 10, 'convergence_ms': 25.18} |
| Final state | completed at sim t = 402.656 s |
| Tasks total / assigned / completed / failed | 16 / 16 / 16 / 0 |
| Coverage | 100 % (planned 100 %) |
| Per-drone completed | {'SIM-01': 7, 'SIM-02': 9, 'SIM-03': 0} |
| Injected failure | CBBA re-allocated SIM-03's 5 unfinished tasks across 2 drones (11 assignments changed, 0 infeasible) |
| Battery | {'mean_used_pct': 13.67, 'estimate_basis': '250 Wh pack (estimate)', 'total_used_pct': 41, 'energy_wh_estimate': 102.5} |
| Pause check (sim time) | {'before': 98.624, 'after_3s': 98.624, 'after_resume': 101.492} |
| E2E wall time from Start | 278.67 s |

## Simulation speed

| Requested | Achieved real-time factor (1 drone) |
|---|---|
| 1x | 1.0 |
| 2x | 2.01 |
| 5x | 4.96 |
| 10x | 5.61 |

1x vs 10x (same mission): reached all waypoints True / True; landing error 0.07 m / 0.08 m; simulated flight time 127.3 s / 129.2 s (Δ 1.5 %); wall time 127.5 s / 21.2 s (achieved RTF 5.85).

## Failure injection (real simulator)

| Failure | Measured |
|---|---|
| Kill drone / SITL crash | returncode -9; E2E: CBBA re-allocated SIM-03's 5 unfinished tasks across 2 drones (11 assignments changed, 0 infeasible) |
| Kill in flight (15 m) | motors cut, drone fell and rests at {'x': 0.027, 'y': -0.126, 'z': 0.037} (ground z 0.05); world kept stepping (10.1 s in 10 s) |
| Link loss | PX4 mode after loss: LAND (policy LAND) |
| GPS drift | reported-vs-truth error 0.3 m -> 29.42 m; PX4 followed the biased GPS after 14.3 s sim |
| Obstacle | map -> Gazebo -> map position error 0.0004 m |
| Low battery (forced to 4.0 %) | PX4 failsafe mode: LAND; charge samples [100.0, 73.0, 38.0, 7.0, 5.0, 5.0] % |
| Wind gust (20 m/s from W, 3 s) | displacement east 8.614 m, north -0.133 m |

## Report generation

| Measure | Value |
|---|---|
| Generation time (E2E) | 13.42 ms (target < 30 s) |
| Re-run | same AOI, task type and fleet at 2×, no injected failure; stopped on purpose after > 2 completed tasks (it exercises the diff, it is not a second full flight) |
| Comparison | compatible=True; warnings: Different injected conditions (none vs drone_failures, reallocation_events); Different outcome (stopped vs completed); totals are not over the same part of the mission |
| Coverage Δ / battery Δ / duration Δ | -66.01 / -32 / -297.8 |

## Evidence

- [`01_simulation_setup.png`](../../documents/sprint5/screenshots/01_simulation_setup.png)
- [`02_aoi_geojson.png`](../../documents/sprint5/screenshots/02_aoi_geojson.png)
- [`03_cbba_allocation.png`](../../documents/sprint5/screenshots/03_cbba_allocation.png)
- [`04_world_generation.png`](../../documents/sprint5/screenshots/04_world_generation.png)
- [`05_gazebo_world.png`](../../documents/sprint5/screenshots/05_gazebo_world.png)
- [`06_sitl_fleet.png`](../../documents/sprint5/screenshots/06_sitl_fleet.png)
- [`07_simulation_running.png`](../../documents/sprint5/screenshots/07_simulation_running.png)
- [`08_leaflet_live_drones.png`](../../documents/sprint5/screenshots/08_leaflet_live_drones.png)
- [`09_leaflet_trajectory.png`](../../documents/sprint5/screenshots/09_leaflet_trajectory.png)
- [`10_telemetry_updates.png`](../../documents/sprint5/screenshots/10_telemetry_updates.png)
- [`11_simulation_controls.png`](../../documents/sprint5/screenshots/11_simulation_controls.png)
- [`12_failure_injection.png`](../../documents/sprint5/screenshots/12_failure_injection.png)
- [`13_drone_failure_response.png`](../../documents/sprint5/screenshots/13_drone_failure_response.png)
- [`14_simulation_report.png`](../../documents/sprint5/screenshots/14_simulation_report.png)
- [`15_report_comparison.png`](../../documents/sprint5/screenshots/15_report_comparison.png)
- [`16_end_to_end_pass.png`](../../documents/sprint5/screenshots/16_end_to_end_pass.png)
- recording [`sprint5_e2e_simulation.webm`](../../documents/sprint5/recordings/sprint5_e2e_simulation.webm) (30.4 MB)
- recording [`sprint5_gazebo_run.mp4`](../../documents/sprint5/recordings/sprint5_gazebo_run.mp4) (0.9 MB)

## Limitations (measured)

Development host: AMD Ryzen 5 5600H, 15.3 GB RAM, Windows 11. The simulator
runs in Docker (Ubuntu 22.04, 12 vCPU / 8 GB VM): Gazebo Harmonic (gz-sim
8.15.0) and PX4 SITL v1.15.4. Figures come from `gazebo_integration.json`,
`e2e_results.json` and `environment.json` in this folder.

| # | Limitation | Status | Evidence / detail |
|---|---|---|---|
| 1 | **16 concurrent SITL instances** (target hardware NUC i7, 64 GB) | **NOT VERIFIED** | The VM has 8 GB, so `SIM_MAX_INSTANCES = 8`. Requests above the limit are refused by the API and the UI (`test_fleet_size_limit`), never silently reduced. Largest fleet actually flown: 3 drones (E2E). |
| 2 | **10× speed is not reached** | Measured | Achieved real-time factor at 1 drone: 1× → 0.99–1.00, 2× → 2.00–2.01, 5× → 3.81–4.96, 10× → 4.46–5.61 (4 runs; varies with host load). The FPS guardrail steps the requested speed down. |
| 3 | **5× with 3 drones triggers a PX4 failsafe** | Measured, worked around | PX4 reported `Accel TIMEOUT` and then took its attitude failsafe once the host CPU could not keep sensor rates in lockstep. The E2E comparison re-run therefore uses 2×. Only 1-drone runs were tested at 10×. |
| 4 | 1× vs 10× equivalence holds within tolerance, not bit for bit | Measured | Flight-time difference 1.5–2.3 % over three runs. Landing error ≤ 0.16 m. All waypoints reached in both runs. |
| 5 | No obstacle avoidance in the PX4 mission path | By design for Sprint 5 | Obstacles are placed in Gazebo (position error 0.4 mm) and collisions are detected from ground truth. Sprint 4 ORCA runs only on the in-process demo fleet. |
| 6 | Mission altitude is relative to launch, not terrain-following | By design | PX4 `GLOBAL_RELATIVE_ALT`. Over steep AOIs the height above ground varies with the terrain. |
| 7 | GPS drift needs a PX4 patch | Implemented | Stock PX4 v1.15 + Gazebo has no GPS failure injection. `sim/px4_patches/gps_drift.py` adds `SIM_GPS_DRIFT_N/E`. Measured error rose from about 0.1 m to 29.1 m (bias 25 m N, 15 m E). PX4 follows the biased GPS only after 12–15 s of simulated time: EKF2 rejects the jump at its innovation gate until the GPS-fusion timeout resets its position. A test with a fixed 15 s wait was flaky for this reason; it now waits for the divergence. |
| 8 | PX4 battery estimate | Calibrated | The simulated battery has no voltage sag, so `BAT1_V_LOAD_DROP = 0`. With PX4's default the reading stopped at 21 % with a 4 % floor. PX4's battery simulator resets the charge to 100 % on disarm, so the report uses the last armed sample. The live fleet table shows the PX4 value as is, so it reads 100 % after landing. |
| 9 | MAVLink via pymavlink; no MAVSDK, no ROS 2 | By design | Simhost speaks MAVLink directly (mission protocol, commands, params). |
| 10 | Hardware flight-controller adapter is untested on hardware | **NOT RUN** | `HardwareAdapter` refuses everything (`NOT_AUTHORIZED`, `SAFETY_CHECKS`, `HARDWARE_UNAVAILABLE`). `SimulationAdapter` refuses live missions and real drones. Only these refusals are unit-tested. |
| 11 | Gazebo GUI uses software rendering | Environment | Screenshots and recordings come from the real Gazebo GUI on Xvfb with Mesa llvmpipe. Frame rate is low, and the GUI is evidence only, not part of the control loop. |
| 12 | Start-up time | Measured | Session start (world + SITL + EKF ready) took 27–32 s for 1–2 drones and about 40–60 s for 3. The PX4 image build takes 20–40 min on first build. |
| 13 | Intermittent PX4 start-up hang | Mitigated | Occasionally a PX4 instance never sends a heartbeat. Simhost restarts it once and reports `startup_attempts` and `startup_errors`. Not root-caused. |
| 14 | Data-link loss is timed in simulated seconds | Mitigated | The GCS heartbeat is sent at 10 Hz wall time, with `COM_DL_LOSS_T = 10`. Otherwise PX4 sees false link loss at high speed. |
| 15 | DEM | Labelled | AWS Terrain Tiles (SRTM 30 m over most land). If no tiles can be fetched, the world is flat and labelled `FALLBACK` in the UI and the report. Real-DEM tests need internet (`-m real_dem`). |
| 16 | A Gazebo server crash ends the run | Mitigated, detected | One E2E run lost the Gazebo server: ODE aborted on a drone killed in flight that kept its last thrust. Fixed by cutting motors on PX4 exit, with a watchdog that removes runaway models; checked on the real simulator (`test_airborne_crash_falls_and_world_survives`, fails without the fix). Any other server death is now reported as a `failed` session, not a frozen `running` one; a run cannot recover from it. |
