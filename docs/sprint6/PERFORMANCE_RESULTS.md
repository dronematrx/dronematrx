# Sprint 6 — Performance results

Generated 2026-10-27 13:30 UTC from `docs/sprint6/performance/*.json` (Playwright E2E + SITL integration). Pass/fail is computed from the numbers below.

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
| Drones during the run | 8 kinematic demo + 3 PX4 SITL (bench) |
| Video feeds | 9 on screen (1 SITL camera 256x192 @ 5 Hz sim, 8 test patterns 480x270 @ 15 fps) |
| Network | localhost (browser, backend and Docker Desktop on one machine); no Wi-Fi or LTE in the path |

| Metric | Samples | Median | P95 | Max | Target | Result | Method |
|---|---|---|---|---|---|---|---|
| Dashboard initial load (navigation → fleet rendered) | 1 | 369.3 ms | 369.3 ms | 369.3 ms | < 2000 ms | **PASS** | browser `performance.now()` at first fleet render after a full reload |
| Telemetry update interval (SITL drones, per drone) | 1182 | 502 ms | 650 ms | 6330 ms | ≤ 1000 ms (1 Hz) | **PASS** | arrival times of new `last_telemetry_at` values in the browser store |
| Rendering frame rate (dashboard + 4 live video tiles) | 172 frames / 5.0 s | 34.4 fps | – | worst frame 150 ms | 60 fps | **FAIL** | `requestAnimationFrame` count over 5 s, headless Chromium |
| FC discovery (click → controller listed) | 1 | 621 ms | 621 ms | 621 ms | < 5000 ms | **PASS** | UI timing; API discovery itself 54.1 ms |
| Video glass-to-glass latency (TEST PATTERN) | 726 | 98.0 ms | 138.7 ms | 185.6 ms | < 300 ms (Wi-Fi) | **PASS (localhost)** | capture time barcoded in each frame vs browser presentation time, clock offset measured NTP-style; localhost, not Wi-Fi |
| Detection: inference timestamp → card visible | 4 | 268.0 ms | 323 ms | 323 ms | < 1000 ms | **PASS** | fixture `inferred_at` (backend clock = browser clock) to the card's first painted frame |
| Link-loss overlay after the source stops | 1 | 3224 ms | 3224 ms | 3224 ms | < 2000 ms after loss is detectable | **FAIL** | harness stops the publisher → overlay visible (includes relay teardown + 1.5 s frame-stall detection) |

### Command latency by boundary: single-drone commands (ARM, TAKEOFF, GOTO, HOLD, RTL)

| Boundary | Samples | Median | P95 | Max | Target | Result |
|---|---|---|---|---|---|---|
| Request received → adapter dispatch (backend) | 7 | 83.4 ms | 187.5 ms | 187.5 ms | – | **measured** |
| simhost receipt → MAVLink written to PX4 | 7 | 0.1 ms | 0.5 ms | 0.5 ms | – | **measured** |
| Request → command reaches the flight controller | 7 | 109.7 ms | 215.0 ms | 215.0 ms | < 50 ms | **FAIL** |
| MAVLink send → PX4 COMMAND_ACK | 7 | 17.4 ms | 49.1 ms | 49.1 ms | – | **measured** |
| NATS publish → JetStream ack (kinematic demo) | 0 | – | – | – | – | NOT MEASURED |
| Request → PX4 ack known in the backend | 7 | 168.0 ms | 254.7 ms | 254.7 ms | – | **measured** |
| Dispatch → telemetry shows the commanded state | 7 | 1566.7 ms | 14079.5 ms | 14079.5 ms | – | **measured** |

### Command latency by boundary: fleet-wide commands (HOLD ALL / LAND ALL: every target validated in one request)

| Boundary | Samples | Median | P95 | Max | Target | Result |
|---|---|---|---|---|---|---|
| Request received → adapter dispatch (backend) | 12 | 823.3 ms | 850.8 ms | 850.8 ms | – | **measured** |
| simhost receipt → MAVLink written to PX4 | 4 | 0.0 ms | 0.3 ms | 0.3 ms | – | **measured** |
| Request → command reaches the flight controller | 4 | 608.1 ms | 933.6 ms | 933.6 ms | < 50 ms | **FAIL** |
| MAVLink send → PX4 COMMAND_ACK | 4 | 18.6 ms | 56.2 ms | 56.2 ms | – | **measured** |
| NATS publish → JetStream ack (kinematic demo) | 8 | 674.1 ms | 831.1 ms | 831.1 ms | – | **measured** |
| Request → PX4 ack known in the backend | 4 | 676.8 ms | 1072.4 ms | 1072.4 ms | – | **measured** |
| Dispatch → telemetry shows the commanded state | 12 | 313.1 ms | 934.0 ms | 934.0 ms | – | **measured** |

The 50 ms target applies to *request → reaches the flight controller* (sum of backend validation, one-way backend→simhost HTTP estimate ((RTT − handler)/2) and simhost receipt → MAVLink send). PX4 acknowledgement and the observed state change are reported separately and are not claimed against 50 ms. Kinematic-demo drones have no flight controller: their dispatch boundary is the NATS publish acknowledgement.
