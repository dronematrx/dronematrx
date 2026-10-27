# Sprint 6 — Implementation plan

This plan comes from an audit of the repository at `5a1df51` (Sprint 5 on
`main`), done before any Sprint 6 code was written.

## 1. Audit findings

| Area | What exists | Sprint 6 use |
|---|---|---|
| Frontend | React 19 + Vite + TypeScript, react-router, Zustand, react-leaflet. Routes `/missions`, `/missions/:id/plan`, `/missions/:id/simulate`, `/map` (Flight Plan), `/fleet`, `/analytics`, `/settings`. `AppShell` + `SidebarNav` | New route `/mission-control`, added to the sidebar. Built on `AppShell` |
| Map | `MissionMap` (one shared Leaflet map), `DroneMarker`, `GeofenceLayers`, `CbbaLanes`, `OrcaLayer`, Sprint 5 `SimMapLayers` | Reused for the centre panel |
| Drone state | `useDroneStore` (the single fleet store) fed by one `WebSocketTelemetryProvider`. The `realtimeEvents` bus fans out the other WS message types | Reused. **No second fleet store** |
| Orphan demo | `components/MissionControl.tsx`, `DigitalTwinSimulator.tsx`, `AnalyticsDashboard.tsx` + `store/useSwarmStore.ts`: a client-side demo with its own fake drones, not routed and not connected to the backend | **Not used**; it is exactly the "disconnected mock dashboard" the brief rules out. Left untouched and flagged as dead code |
| Backend | FastAPI, SQLAlchemy 2 + GeoAlchemy2, Alembic, PostGIS + TimescaleDB, NATS JetStream `DRONES` (`drone.{UIN}.telemetry|state|alert|command`) | Extended |
| RBAC | `RequireRole` gates in `api/deps.py`; roles `ADMIN`, `OPERATOR`, `OBSERVER`, `ANALYST` | Reused; see §6 |
| Audit | `audit_logs` table + `AuditRepository.record` | Reused for every command, binding and review |
| Telemetry | Sprint 4 runtime: in-process **kinematic** simulator (8 seeded drones, no flight controller) → NATS → `TelemetryManager` → WS `telemetry` + `drones` row + TimescaleDB. Sprint 5 simhost publishes **PX4 SITL** telemetry on the same subjects | Reused unchanged. The source of each drone's telemetry is computed and shown (§3) |
| Commands | Sprint 4 `POST /orca/command` (HOLD / RTH / RESUME) to the kinematic simulator over the NATS command topic. Sprint 5 simhost `/sessions/{id}/command` (arm, disarm, takeoff, hold, rtl, land, goto, mode) to **real PX4** with `COMMAND_ACK` | Both wrapped behind the flight-controller adapter (§4) |
| Flight-controller adapter | Sprint 5 `services/simulation/adapters.py`: `SimulationAdapter` (PX4 SITL via simhost), `HardwareAdapter` (locked: refuses everything) | Extended with a bench-SITL adapter and a kinematic-demo adapter |
| SITL | Sprint 5 simhost container: Gazebo Harmonic + PX4 v1.15.4, MAVLink bridge, link emulator, watchdog | Reused for the SITL **bench**: PX4 instances that mission control discovers and binds |
| Video / recording | **None** | New (§5) |
| Detection / inference | **None** | New (§7) |
| E2E | Playwright (Chromium) with specs for Sprints 3–5 | New `e2e/sprint6.spec.ts` |

**Baseline before Sprint 6** (commit `5a1df51`): backend 350 passed,
Gazebo/PX4 12 passed, simhost 23 passed, Sprint 5 E2E 2 passed, Sprints 3–4
E2E 14 passed + 1 opt-in skip, frontend lint/typecheck/vitest 8/build OK.

## 2. Gaps and decisions

| Gap | Decision |
|---|---|
| No telemetry-source labelling: the Sprint 4 kinematic drones look like real aircraft | The fleet API returns `telemetry_source` per drone: `sitl` (PX4 via simhost), `kinematic_demo` (Sprint 4 in-process simulator; not a flight controller), `hardware` (adapter locked; none connected), or `none`. Shown as a badge everywhere |
| No flight controllers to discover on this host | A **SITL bench**: a simhost session of N PX4 `x500` instances (with downward cameras) that is *not* a mission simulation. Its instances publish nothing until bound. Hardware endpoints come only from an explicit allow-list (`FC_HARDWARE_ENDPOINTS`); they are listed, never scanned, and never armed (hardware adapter stays locked) |
| No video service | **MediaMTX** container: RTSP in, WebRTC (WHEP) out, recording + playback API for DVR. The backend is the signalling and authorization boundary |
| No camera sources | (a) **SITL drone cameras**: a Gazebo camera on each bench x500 → simhost → ffmpeg H.264 → MediaMTX. (b) **Deterministic test patterns** `TEST-01..09`: frame counter + wall-clock barcode, labelled TEST PATTERN. Used for latency, nine-feed layout and DVR seek checks |
| No inference pipeline | A `DetectionProvider` interface with two providers: (a) a **classical colour-segmentation detector** running on the SITL camera frames in simhost, geolocated from the drone pose and the nadir camera model (labelled as a CV detector, not a trained AI model); (b) a **deterministic test fixture** provider, admin-only and labelled TEST FIXTURE, for CI. Both publish through NATS `drone.{UIN}.detection` |
| No QR libraries | `qrcode` (render the binding label) and `jsqr` (decode camera frames) via npm |

## 3. Planned changes

### Backend
- `models/mission_control.py`: `FlightControllerBinding`, `DroneCommand`, `VideoSource`, `Detection` (PostGIS `geography(Point, 4326)`, nullable). One Alembic migration.
- `services/mission_control/`:
  - `fleet.py`: overview, staleness, telemetry source;
  - `discovery.py`, `binding.py`, `qr.py`: QR v1 payload validation;
  - `prearm.py`: checklist;
  - `adapters.py`: `BenchSitlAdapter`, `KinematicDemoAdapter`, `HardwareAdapter` (reused);
  - `commands.py`: lifecycle, idempotency, confirmation watcher, audit;
  - `video.py`: MediaMTX client, WHEP proxy, DVR;
  - `detections.py`: ingest, review, heatmap.
- `api/routes/mission_control.py`, `video.py`, `detections.py`.
- `messaging/subjects.py`: add the `detection` kind (the stream is updated at startup).
- Runtime: a listener for `detection` messages; WS events `command`, `detection`, `binding`.

### Simhost
- Bench session support:
  - camera model override (downward, 320×240, 10 Hz, per-instance topic);
  - `bind` / `unbind` (routes an instance's telemetry to a drone record, or stops it);
  - prearm health (`SYS_STATUS` sensor health incl. the PX4 pre-arm bit, EKF flags, failsafe params);
  - command timing (receipt → MAVLink send → `COMMAND_ACK`).
- Video publisher: camera frames → ffmpeg → RTSP; test-pattern publishers.
- Colour-target detector + geolocation.

### Frontend
- `pages/MissionControlPage.tsx`, `components/missioncontrol/`:
  - `FleetPanel`, `McMap`, `VideoWall`, `VideoTile`, `DvrBar`;
  - `DetectionFeed`, `BindingDialog` (discover / QR / manual), `PrearmChecklist`;
  - `CommandBar`, `FleetCommandDialog`.
- `store/useMissionControlStore.ts`: selection, commands, detections and video state. It references drones by id from `useDroneStore`.

### Infrastructure
- `docker-compose.sim.yml`: `mediamtx` service (WebRTC UDP/TCP 8189 + `webrtcAdditionalHosts: 127.0.0.1` for Docker Desktop).

## 4. Command path and safety

```
UI -> POST /mission-control/commands {drone_id, command, params, idempotency_key, confirm_mode}
   -> auth + RBAC -> org + drone -> binding/mode check -> state check (+ geofence/NFZ for GO_TO, prearm for ARM)
   -> DroneCommand(requested) -> adapter.dispatch() -> dispatched (timings) -> COMMAND_ACK: accepted|rejected
   -> watcher: telemetry shows the expected state -> confirmed | timed_out
   -> WS `command` events + audit_logs row
```

- Arming is possible only for **SITL-bound** drones and only after the backend re-runs the pre-arm checklist. Any required FAIL or UNKNOWN check blocks it.
- The request must restate the operating mode (`sitl`); a mismatch is rejected.
- No automatic arming after binding.
- The hardware adapter stays locked; automated tests never reach it.
- The kinematic demo supports only HOLD / RTL / RESUME, as in Sprint 4. Unsupported commands return a per-drone `UNSUPPORTED` result, never a fake success.
- Fleet commands list affected, offline and unsupported drones before confirmation, return per-drone results, and report partial failure.
- Emergency LAND ALL is offered only where the adapter supports LAND, and needs a typed confirmation.

## 5. Video

- Browser ⇄ backend `POST /video/feeds/{id}/whep` (auth, org, RBAC) ⇄ MediaMTX WHEP. Media flows browser ⇄ MediaMTX.
- Link loss is detected from decoded-frame progress (`getStats` `framesDecoded`) and connection state. Signalling failures, media stalls and a missing source (no publisher) are reported separately. Reconnect uses exponential back-off (1, 2, 4, 8 s, max 6 tries), then the feed shows FAILED with a manual retry.
- Quality comes from WebRTC `getStats`: RTT, jitter, loss, fps, resolution, bitrate, dropped frames. Each source is published in two renditions (`hi`, `lo`), and the client switches down or up with hysteresis based on measured loss, RTT and fps. Degradation is injected with `tc netem` on the MediaMTX network namespace.
- DVR uses MediaMTX recording (fMP4 segments) and its playback API, proxied by the backend with auth. Seeking is verified by reading the barcode timestamp of the decoded frame.
- Latency: the test-pattern barcode carries the wall-clock time at capture. The browser decodes it from the rendered `<video>` frame and compares it with `Date.now()` on the same machine (glass-to-glass minus display scan-out).

## 6. RBAC (smallest extension of the existing matrix)

| Action | ADMIN | OPERATOR | ANALYST | OBSERVER |
|---|---|---|---|---|
| View mission control, video, detections | ✓ | ✓ | ✓ | ✓ |
| Discover / bind / unbind controllers, SITL bench | ✓ | ✓ | – | – |
| Start/stop recordings | ✓ | ✓ | – | – |
| Single-drone commands, arm/disarm | ✓ | ✓ | – | – |
| Fleet-wide commands (incl. emergency land) | ✓ | ✓ | – | – |
| Review detections | ✓ | ✓ | ✓ | – |
| Assign tasks | ✓ | ✓ | – | – |
| Emit test-fixture detections | ✓ (test mode only) | – | – | – |

## 7. Detections

- Each NATS `detection` event carries:
  - `event_id` (idempotency), `inferred_at` and drone;
  - class, confidence and bbox;
  - thumbnail (JPEG, base64), plus `lat`/`lon` when geolocated;
  - provider and mission.
- Ingest is `INSERT ... ON CONFLICT (event_id) DO NOTHING`, then a WS `detection` event.
- Review is one transaction: lock the row, set status, reviewer, time and reason, write the audit row. A repeat of the same decision is idempotent; a conflicting re-review is 409.
- Geometry is written only for valid coordinates.
- The heatmap comes from PostGIS grid aggregation (`ST_SnapToGrid`) over accepted detections, filtered by mission, one count per detection row.

## 8. Test strategy

- **Unit (mocked)**: QR validation, prearm rules, command state machine, idempotency, staleness, heatmap bucketing, video state machine (Vitest).
- **Backend API + DB integration**: all endpoints with RBAC, PostGIS geometry, transactions, audit.
- **Simhost (container)**: bench binding, prearm health, command timing, detector geolocation maths.
- **SITL integration** (`-m gazebo`): discovery within 5 s, bind, arm with a confirmed state, HOLD/RTL/GoTo with `COMMAND_ACK` and telemetry, camera frames reach MediaMTX, detector event.
- **Browser E2E** (Playwright, SITL bench + test patterns + fixtures): the 59-step workflow, 22 screenshots in `docs/sprint6/evidence/`.
- **Performance**: dashboard load, telemetry intervals, rAF frame rate, discovery time, video latency, detection latency, command latency per boundary. Written to `docs/sprint6/PERFORMANCE_RESULTS.md`.
- **Regression**: all earlier suites rerun.

## 9. Expected blocked / limited items (to be confirmed by measurement)

- **Real hardware**: no flight controller or camera is attached. Hardware discovery, binding and arming are NOT RUN, and the adapter stays locked.
- **Wi-Fi**: latency is measured on localhost (browser and MediaMTX on one machine), not over Wi-Fi.
- **LTE**: degradation is emulated with `netem`, not a real LTE link.
- **Nine feeds**: nine simultaneous SITL cameras are beyond this 8 GB simulator VM. The nine-feed layout uses nine independent test-pattern sources, labelled as such.
- **Picture-in-Picture** may be unavailable in headless Chromium. If so it is disabled with an explanation and reported as BLOCKED for automation.
- **Detector**: classical computer vision on simulated imagery, not a trained AI model.
