# Sprint 6 — Test Report: Mission Control

Generated 2026-10-27 13:30 UTC by `backend/scripts/generate_sprint6_report.py` from JUnit XML and measurement JSON in `docs/sprint6/performance/`. No test result in this file is typed by hand; only the Limitations section is (`performance/limitations.md`).

Evidence: [`docs/sprint6/evidence/`](evidence/) (also in [`documents/sprint6/`](../../documents/sprint6/)) · reference: [`MISSION_CONTROL.md`](MISSION_CONTROL.md) · performance: [`PERFORMANCE_RESULTS.md`](PERFORMANCE_RESULTS.md)

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
| database | PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2) + PostGIS + TimescaleDB |
| nats | nats-server: v2.10.29 |
| python_backend | Python 3.10.11 |
| node | v22.17.0 |
| sitl | PX4 v1.15.4 SITL (x500 + Drone MatrX nadir camera x500_dm_cam), simhost bench |
| media_relay | MediaMTX 1.9.3 (RTSP in, WebRTC/WHEP out, fMP4 recording + playback API) |
| browser | Chromium (Playwright Version 1.63.0) |
| frontend | React 19 + Vite + react-leaflet; jsQR 1.4.0, qrcode 1.5.4 |
| test_frameworks | pytest 8.3.4, Vitest 5.0.3, Playwright 1.63.0 |

## Test summary

| Area | Result | Evidence |
|---|---|---|
| Dashboard rendering | **PASS** | 01 |
| Live telemetry | **PASS** | 02, 20, 21 |
| Leaflet synchronization | **PASS** | 03 |
| FC discovery | **PASS** | 04 |
| FC binding (discovery, QR camera, manual) | **PASS** | 05 |
| Pre-arm checklist | **PASS** | 06 |
| Arm via SITL, confirmed by telemetry | **PASS** | – |
| Video layouts (1/4/9, fullscreen) | **PASS** | 07–10 |
| Picture-in-Picture | **PASS** | – |
| WebRTC link loss + reconnection | **PASS** | 11 |
| DVR (record, play, seek) | **PASS** | 12 |
| Quick commands (HOLD, RTL, GOTO, reject NFZ) | **PASS** | 03, 14 |
| Assign task | **PASS** | – |
| Fleet-wide commands (confirm, cancel, per-drone) | **PASS** | 13, 14 |
| Detection review | **PASS** | 15, 16 |
| PostGIS persistence + heatmap | **PASS** | 17, 18 |
| RBAC | **PASS** | 19 |
| Stale telemetry + recovery | **PASS** | 20, 21 |
| Cleanup (LAND ALL, no processes left) | **PASS** | – |
| Full E2E workflow | **PASS** | 22 |

| Suite | Kind | Result |
|---|---|---|
| Backend (`pytest -m 'not gazebo'`) incl. `test_mission_control.py` | unit + API + DB/PostGIS (fakes for simhost/runtime) | 391 passed, 0 failed, 0 not run |
| Sprint 6 SITL integration (`-m gazebo tests/test_mc_sitl_integration.py`) | real PX4 SITL + Gazebo + MediaMTX | 6 passed, 0 failed, 0 not run |
| simhost container tests | units (detector, barcode, binding routing, SITL manager) | 33 passed, 0 failed, 0 not run |
| Playwright E2E (`playwright.sprint6.config.ts`) | browser, full stack, SITL bench | 1 passed, 0 failed, 0 not run |
| Regression: Sprint 5 Gazebo (`-m gazebo tests/test_sim_gazebo_integration.py`) | real PX4 SITL + Gazebo | 12 passed, 0 failed, 0 not run |
| Regression: earlier-sprint Playwright (`playwright.config.ts`) | browser, Sprints 1-5 | 16 passed, 0 failed, 1 not run |

Sprint 6 backend tests: 41/41 passed (dashboard, binding + QR, pre-arm, commands + fleet commands, detections + PostGIS + heatmap, video signalling, RBAC).

## Real-simulator results (PX4 SITL bench)

| Measure | Value |
|---|---|
| Discovery (bench of 2) | 4.5 ms; bench start-up 33.6 s |
| PX4 health reported | SYS_STATUS present 0x220800d, health 0x2220803f (AHRS + pre-arm bits set); GNSS fix 3, 10 sats; failsafes {'NAV_DLL_ACT': 2, 'COM_LOW_BAT_ACT': 3, 'GF_ACTION': 2} |
| ARM | simhost receipt → MAVLink send 0.138 ms; send → PX4 COMMAND_ACK 6.415 ms |
| TAKEOFF | simhost receipt → MAVLink send 0.029 ms; send → PX4 COMMAND_ACK 15.14 ms |
| GOTO | simhost receipt → MAVLink send 0.029 ms; send → PX4 COMMAND_ACK 13.474 ms |
| HOLD | simhost receipt → MAVLink send 0.088 ms; send → PX4 COMMAND_ACK 6.733 ms |
| Detector (colour segmentation on the SITL camera) | marker geolocated within 2.08 m from 19.33 m height, confidence 0.691 |
| SITL cameras | FC-f0f8b0-01: 256x192, 149 frames, FC-f0f8b0-02: 256x192, 149 frames |

## E2E measurements

| Measure | Value |
|---|---|
| SITL bench | `{"session_id": "bench-6efcb0", "controllers": ["FC-6efcb0-01", "FC-6efcb0-02", "FC-6efcb0-03"], "startup_s": 40.57, "wall_s": 40.694}` |
| Bindings | `[{"drone": "SIM-03", "state": "bound", "via": "manual"}, {"drone": "SIM-02", "state": "bound", "via": "qr"}, {"drone": "SIM-01", "state": "bound", "via": "discovery"}]` |
| Arm attempts (backend refusals recorded) | `[{"refused": null}]` |
| Pre-arm block (NFZ over pad) | `{"blocking": ["operating_area"]}` |
| Prohibited GO TO | `"GOTO rejected: destination inside Airport Approach NFZ"` |
| Nine distinct feeds | `["mc-tile-sitl/FC-6efcb0-01/hi", "mc-tile-test/01/hi", "mc-tile-test/02/hi", "mc-tile-test/03/hi", "mc-tile-test/04/hi", "mc-tile-test/05/hi", "mc-tile-test/06/hi", "mc-tile-test/07/hi", "mc-tile-test/08/hi"]` |
| Picture-in-Picture | `{"enabled": true}` |
| Link-loss overlay after source stop (ms) | `3224` |
| Feed back after source restart (ms) | `9415` |
| DVR seek (position vs recorded barcode time) | `{"before": {"t": 0, "captureMs": 1793106199801}, "after": {"t": 19.5316, "captureMs": 1793106219253}, "positionDeltaS": 19.5316, "recordedTimeDeltaS": 19.452}` |
| HOLD ALL | `{"targets": 10, "results": ["DRONE\tRESULT\tDETAIL", "DMX-001\tCONFIRMED\tconfirmed by telemetry", "DMX-002\tCONFIRMED\tconfirmed by telemetry", "DMX-003\tCONFIRMED\tconfirmed by telemetry", "DMX-004\tCONFIRMED\tconfirmed by telemetry", "DMX-005\tCONFIRMED\tconfirmed by telemetry", "DMX-006\tCONFIRMED\tconfirmed by telemetry", "DMX-007\tCONFIRMED\tconfirmed by telemetry", "DMX-008\tCONFIRMED\tconf` |
| Assign task | `{"drone": "DMX-001", "task": "T005", "persisted": true}` |
| Test fixtures delivered via | `"nats"` |
| SITL detector card shown | `true` |
| Heatmap | `{"cells": 3, "total": 3, "max": 1}` |
| Observer requests (HTTP) | `{"command": 403, "fleet": 403, "discover": 403, "review": 403, "recording": 403}` |
| STALE shown after link down (ms) | `5246` |
| CONNECTED again after restore (ms) | `507` |
| Commands still in flight at the end | `0` |
| PX4 processes after cleanup | `0` |

## Screenshots

- [`01_mission_control_dashboard.png`](evidence/01_mission_control_dashboard.png)
- [`02_live_fleet.png`](evidence/02_live_fleet.png)
- [`03_leaflet_drone_tracking.png`](evidence/03_leaflet_drone_tracking.png)
- [`04_flight_controller_discovery.png`](evidence/04_flight_controller_discovery.png)
- [`05_flight_controller_binding.png`](evidence/05_flight_controller_binding.png)
- [`06_prearm_checklist.png`](evidence/06_prearm_checklist.png)
- [`07_video_wall_single.png`](evidence/07_video_wall_single.png)
- [`08_video_wall_four.png`](evidence/08_video_wall_four.png)
- [`09_video_wall_nine.png`](evidence/09_video_wall_nine.png)
- [`10_video_fullscreen.png`](evidence/10_video_fullscreen.png)
- [`11_video_link_loss.png`](evidence/11_video_link_loss.png)
- [`12_video_dvr.png`](evidence/12_video_dvr.png)
- [`13_quick_action_confirmation.png`](evidence/13_quick_action_confirmation.png)
- [`14_command_result.png`](evidence/14_command_result.png)
- [`15_detection_feed.png`](evidence/15_detection_feed.png)
- [`16_detection_accepted.png`](evidence/16_detection_accepted.png)
- [`17_detection_map_overlay.png`](evidence/17_detection_map_overlay.png)
- [`18_detection_heatmap.png`](evidence/18_detection_heatmap.png)
- [`19_rbac_read_only.png`](evidence/19_rbac_read_only.png)
- [`20_stale_telemetry.png`](evidence/20_stale_telemetry.png)
- [`21_mission_control_recovered.png`](evidence/21_mission_control_recovered.png)
- [`22_e2e_test_summary.png`](evidence/22_e2e_test_summary.png)
- recording [`sprint6_e2e_mission_control.webm`](../../documents/sprint6/recordings/sprint6_e2e_mission_control.webm) (24.2 MB)
- recording [`sprint6_gazebo_bench.mp4`](../../documents/sprint6/recordings/sprint6_gazebo_bench.mp4) (1.0 MB)

## Defects found and fixed during testing

Real defects that the E2E and regression runs exposed. Each one was fixed in the product (not worked around in a test) before the reported runs.

| # | Found by | Defect | Fix |
|---|---|---|---|
| 1 | E2E | Telemetry freshness used the producer's timestamp, so a drone whose clock ran ahead or behind was shown as live or stale wrongly. | Freshness is measured by backend receive time (`received_at`), sent with every WebSocket frame. |
| 2 | E2E | The overview loaded up to 500 detections including thumbnails, and synchronous drone-row writes delayed the telemetry stream. | Pending count only, thumbnails fetched on demand, drone-row writes coalesced in the background. |
| 3 | E2E | `ConnectionManager` dropped a socket whose send failed but never closed it: the browser stayed connected to a stream that no longer sent anything. | Per-connection send locks, a 5 s send timeout, and failed sockets are closed so the client reconnects. |
| 4 | E2E | Command states and detections pushed while the WebSocket was reconnecting were lost. | The dashboard re-reads them after every reconnect and polls in-flight commands every 2 s. |
| 5 | E2E | The WHEP player could stay in RECONNECTING after a media failure (an early return skipped the retry). | The retry always runs, guarded only against a superseded peer connection. |
| 6 | E2E | PX4 auto-disarms ~10 s after arming without take-off, so ARM looked like it had failed. | ARM confirmation offers "take off once armed"; the disarm is explained in the UI. |
| 7 | E2E | "Already assigned" from assign-task was shown as an error. | Shown as an informational note (`changed: false`). |
| 8 | SITL integration | A 60 s start-up timeout declared a still-booting PX4 crashed and closed its MAVLink links. | 180 s timeout; on timeout the instance is killed before it is reported; start retries if a link closed. |
| 9 | SITL integration | Bench-only spawn fields (e.g. `ground_z`) were sent to PX4 as parameters. | Only names matching PX4's parameter pattern are sent. |
| 10 | SITL integration | The bench lay outside every inclusion geofence, so pre-arm correctly refused it. | The bench registers (and on stop deactivates) its own inclusion zone. |
| 11 | Sprint 5 regression | Stopping a session left ffmpeg processes running: a stopped SITL camera re-created its encoder on the next Gazebo frame, and a new Gazebo screen recording orphaned the previous one. After many benches the leaked encoders loaded the simulator enough that two Sprint 5 Gazebo tests failed (10x-speed equivalence, link-loss failsafe). | A stopped camera never re-creates its encoder (regression test `test_stopped_camera_never_recreates_its_encoder`); a session stop ends its screen recording; only one recording runs at a time. Both Sprint 5 tests pass again on a clean simulator. |


## Limitations and blocked items

| # | Item | Status | Detail |
|---|---|---|---|
| 1 | Real flight controllers (discovery, binding, arming, commands on hardware) | **NOT RUN** | No aircraft or flight controller is connected. Hardware endpoints come only from `FC_HARDWARE_ENDPOINTS`; they are listed but never probed. Binding one records `connection_failed: HARDWARE_UNAVAILABLE`, and the hardware adapter refuses every command. All flight-controller behaviour here is PX4 v1.15.4 SITL. |
| 2 | Real drone cameras | **NOT RUN** | The video wall shows SITL drone cameras (Gazebo, software-rendered) and labelled TEST PATTERN sources. No physical camera is configured. |
| 3 | Video latency "< 300 ms on Wi-Fi" | Measured on localhost only | Glass-to-glass is measured with barcoded test patterns: capture time vs browser presentation, with the NTP-style clock offset. Browser, relay and publisher run on one machine, so no Wi-Fi hop is in the path. |
| 4 | LTE degradation | Partly verified | The client switches rendition (hi → lo) from measured `getStats()` loss, RTT and fps, and both renditions are published. This machine has no controllable network shaping between the Windows browser and Docker Desktop, so an automated degraded-LTE run was not performed. The ladder logic is in `frontend/src/lib/whep.ts`. |
| 5 | Picture-in-Picture | Browser-dependent | The real `requestPictureInPicture` is used. Headless Chromium in the E2E reports its availability, and the result is recorded in `e2e_results.json` (`pip`). Where PiP is unavailable the control is disabled with an explanation. |
| 6 | SITL camera frame rate | Measured, low | Software rendering (Mesa llvmpipe) with three camera drones gives a real-time factor of 0.67, i.e. ~3 fps of wall time per SITL camera (256×192 @ 5 Hz simulated). Test patterns run at 15 fps. |
| 7 | Nine feeds | Mixed sources | The 3×3 layout shows nine distinct sources. In the reported E2E run that is 1 SITL drone camera and 8 test patterns: with three camera drones the real-time factor fell to 0.67 and PX4 estimator flicker made arming unreliable, so the E2E bench uses one camera drone (RTF ≈ 1.0). A two-camera bench is exercised by `test_mc_sitl_integration.py` (both cameras delivered 153 frames to the relay). Nine simultaneous SITL cameras exceed this 8 GB simulator VM. |
| 8 | Detector | Classical CV, not AI | `sim_colour_detector` segments the bench's red markers in SITL camera frames and geolocates them (measured error 2.6 m from 19 m height). No trained inference model exists in this repository. Fixture detections are labelled TEST FIXTURE and are admin/test-mode only. |
| 9 | PX4 SITL estimator flicker | Observed | Under this host's load, PX4 occasionally reported "Preflight Fail: height estimate not stable". The backend's pre-arm re-check then refused ARM, correctly. The E2E arms as an operator would: it waits for a stable READY and retries, and every refusal is recorded (`arm_attempts`). |
| 10 | PX4 preflight auto-disarm | By design (PX4) | PX4 disarms ~10 s after arming without take-off (`COM_DISARM_PRFLT`). The UI warns about this; the parameter is left at the PX4 default. |
| 11 | WebSocket drops under load | Observed, mitigated | The browser's shared WebSocket reconnected occasionally under heavy load; the cause was not identified. Events sent during a gap are lost, so the dashboard re-reads command state and detections after every reconnect, and polls in-flight commands every 2 s. |
| 12 | WebRTC on Docker Desktop | Configuration | Browsers gather ICE candidates on LAN interfaces, not loopback. MediaMTX must advertise the machine's LAN IP (`MEDIAMTX_WEBRTC_HOSTS`), and the media port 8189 is published on all interfaces. Media flows only for sessions the authenticated backend negotiated. |
| 13 | Command latency target (< 50 ms to the flight controller) | **Not met** (environment + validation) | Single-drone commands reached PX4 in 55–215 ms (median 110 ms). The parts are backend validation (30–190 ms: it re-reads the controller from the simulator over HTTP, and ARM runs the full pre-arm re-check), the Windows-host → Docker Desktop HTTP hop (≈ 25–40 ms one way measured), and simhost → MAVLink send (< 1 ms). In HOLD ALL (10 drones in one request) each target is validated against fresh state, and per-command validation rose to ≈ 830 ms. On a host where backend and simhost share a network namespace the hop is ~1 ms, but validation would still exceed 50 ms for ARM. Measured values: `PERFORMANCE_RESULTS.md`. |
| 14 | Emergency LAND ALL | Policy | LAND ALL is offered only where the adapter supports LAND (PX4 SITL), and needs the typed confirmation `LAND ALL`. Landing in place is not always the safest action, and the dialog says so. The kinematic demo drones are excluded (no LAND capability). |
| 15 | Laptop sleep during long runs | Test environment | Long E2E runs were interrupted twice by Windows sleep (NATS and WebSocket timeouts, a 12 h clock jump). The reported runs were done with a temporary keep-awake helper (`SetThreadExecutionState`), with no power settings changed. |
