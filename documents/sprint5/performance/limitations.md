# Sprint 5 — known limitations (measured)

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
