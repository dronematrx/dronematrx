# Sprint 6 — Mission Control

A three-panel live dashboard at **`/mission-control`**:

- **Left:** the fleet. Summary counts, search and filters, then the selected drone: binding, pre-arm checklist, quick actions and command log.
- **Centre:** the shared Leaflet map. Drones, tracks, geofences and no-fly zones, accepted detections, and the heatmap.
- **Right:** the video wall (1, 4 or 9 feeds) and the detection feed.

Selecting a drone in any panel selects it in all three.

It runs on the existing stack. There is one fleet store and one WebSocket. The backend is the only authorization and validation boundary; the browser never talks to a flight controller, the database or the media relay's control ports.

```
 browser ──REST/WS──> FastAPI ── NATS JetStream (drone.{UIN}.telemetry|state|alert|command|detection)
   │                    │  │
   │ WHEP offer         │  └── simhost (Docker) ── Gazebo Harmonic + PX4 v1.15.4 SITL bench (MAVLink)
   └─WebRTC media──> MediaMTX <──RTSP── simhost: SITL drone cameras, TEST PATTERN sources
                        └── recording (fMP4) + playback API (DVR), proxied by the backend
```

## 1. Where data comes from

Every drone carries a **telemetry source**, shown as a badge everywhere:

| Source | Meaning | Commands |
|---|---|---|
| `SITL` | A PX4 SITL flight controller bound to this drone record (simhost bench) | ARM, DISARM, TAKEOFF, HOLD, RTL, LAND, GOTO; acknowledged by PX4 `COMMAND_ACK` |
| `KINEMATIC DEMO` | The Sprint 4 in-process kinematic simulator: no flight controller | HOLD, RTL, RESUME; the NATS publish ack only, then confirmed from telemetry |
| `HARDWARE` | A hardware controller bound from the approved endpoint list | None: the hardware adapter is locked (`HARDWARE_UNAVAILABLE`) |
| `NO SOURCE` | Nothing produces telemetry for this drone | None |

**Freshness.** A drone is CONNECTED while its last telemetry is younger than `MC_TELEMETRY_STALE_S` (5 s), STALE until `MC_TELEMETRY_OFFLINE_S` (60 s), then OFFLINE.

- The browser recomputes this every second against the server clock (`server_time` offset), so a stalled stream turns stale even with no new message.
- Out-of-order frames (older than the last applied one) are dropped by the `TelemetryManager`.
- After a disconnect nothing is extrapolated or randomised.

## 2. Flight-controller binding

**Discovery** (`POST /mission-control/fc/discover`) never scans a network. It lists:

- the PX4 instances of SITL **bench** sessions on `SIMHOST_URL`;
- the entries of `FC_HARDWARE_ENDPOINTS` (an explicit allow-list), shown as *not probed*.

A **SITL bench** (`POST /mission-control/bench`) is a flat Gazebo test field with N PX4 x500s carrying downward cameras, plus red ground markers for the detector. It registers its own inclusion geofence ("SITL Bench Test Field …") as its operating area.

**Binding.** A bench controller publishes nothing until bound. Binding routes its MAVLink telemetry (and detections) to the chosen drone record. Unbinding silences it again. One active binding per controller identity and per drone, enforced by partial unique indexes.

Lifecycle: `binding → bound → unbound`, or `connection_failed → (reconnect) → bound`.

A background reconcile (every 10 s) marks bindings `connection_failed` when their bench disappears, and re-routes them when simhost restarted.

**Safety rules:**

- SITL controllers bind only to simulation drone records, hardware controllers only to real-aircraft records.
- Binding never arms.

**QR payload v1** (`services/mission_control/qr.py`):

```
DMXFC1:{"v":1,"fc":"sitl:bench-1a2b3c:FC-1a2b3c-02","proto":"mavlink2","conn":"sitl","sysid":2,"ref":"FC-1a2b3c-02"}
```

- It contains identity only.
- Unknown fields, and any key that looks like a credential (`password`, `token`, `secret`, `key`, `auth`…), are rejected.
- Other versions are rejected with `QR_UNSUPPORTED_VERSION`.

The UI scans with the camera (jsQR on `getUserMedia` frames), shows the identity and discovery state, and binds on confirmation. Paste-in and manual entry are the fallbacks; manual endpoints are validated server-side against fixed formats.

## 3. Pre-arm checklist and arming

`GET /mission-control/drones/{id}/prearm?expected_mode=sitl` returns PASS / FAIL / UNKNOWN / WARNING per check. Each check reads what the controller actually reports:

| Check | Source |
|---|---|
| Flight controller connected | Binding state + PX4 heartbeat age |
| Telemetry fresh | Pipeline timestamp |
| Correct identity | The controller answering is routed to this drone, same MAVLink sysid |
| Appropriate flight mode / on the ground | PX4 heartbeat mode, `EXTENDED_SYS_STATE` |
| Battery | `SYS_STATUS.battery_remaining` ≥ `MC_PREARM_MIN_BATTERY_PCT` |
| GNSS fix + home | `GPS_RAW_INT`, `HOME_POSITION` |
| Estimator healthy | `SYS_STATUS` AHRS health bit |
| Firmware pre-arm checks | PX4 sets `MAV_SYS_STATUS_PREARM_CHECK` in the *health* mask exactly when it can arm |
| Geofence / NFZ | PostGIS: inside an inclusion zone, outside NFZ/exclusion zones |
| Failsafes configured | PX4 params `NAV_DLL_ACT`, `COM_LOW_BAT_ACT` |
| Mode confirmed | The operator states SITL vs hardware; a mismatch fails |

A required FAIL or UNKNOWN blocks arming. The ARM command re-runs the checklist on the backend immediately before dispatch, so a stale browser cannot bypass it. Arming is a single `MAV_CMD_COMPONENT_ARM_DISARM`: PX4's own checks still apply. The drone shows ARMED only when telemetry says so.

## 4. Commands

`POST /mission-control/drones/{id}/commands`, and `POST /mission-control/fleet-commands` (after `/preview`).

**Lifecycle:** `requested → dispatched` (bus ack) or `accepted` / `rejected` (PX4 `COMMAND_ACK`) `→ confirmed | timed_out`. The last step is decided from telemetry only. Other end states: `failed`, `unsupported`, `cancelled` (superseded).

**Validation:**

- Every request is checked for auth, RBAC, organisation, binding/adapter, capability, a fresh link and the drone's state.
- GOTO also needs valid coordinates, an altitude of 5–120 m, and a destination under 5 km away. The destination point and the straight path to it are checked against NFZ/exclusion zones, and the point against inclusion zones.

**Duplicates and replays:**

- An `idempotency_key` replays the original result.
- The same command already in flight is refused as `DUPLICATE_COMMAND`.
- A different command supersedes the in-flight one.

**Fleet-wide:**

- The preview lists affected drones, and separately the offline, stale, unsupported and not-flying ones.
- Execution requires `confirm: true` plus the exact target list from the preview (`TARGETS_CHANGED` otherwise). LAND ALL also needs the typed text `LAND ALL`.
- Results are per drone, and partial failures are reported.

Every command, refusal and outcome is written to `drone_commands`, recording the user, role, mode, adapter, timings, ack and failure reason. It is also written to `audit_logs` with the correlation id. `GET /mission-control/audit` reads the trail.

**Latency boundaries** (`drone_commands.timings`, ms):

| Boundary | Measured how |
|---|---|
| `backend_pre_dispatch_ms` | Route entry → adapter call (validation, DB writes) |
| `http_one_way_est_ms` | (HTTP round trip − simhost handler time) / 2, backend ↔ simhost |
| `simhost_recv_to_mavlink_send_ms` | simhost receipt → `COMMAND_LONG/INT` written to PX4's socket |
| `request_to_fc_dispatch_ms` | Sum of the three above: "reaches the flight controller" |
| `fc_ack_ms` | MAVLink send → PX4 `COMMAND_ACK`, one clock |
| `observed_state_ms` | Dispatch → first telemetry showing the commanded state |

## 5. Video wall

**Sources** (`video_sources`):

- **SITL camera:** the Gazebo camera on a bench drone (256×192, 5 Hz simulated time).
- **TEST PATTERN 01–09:** generated in simhost; the label is burnt into the picture.

Each source is published in two renditions (`…/hi`, `…/lo`). With no camera source configured the wall says *Camera source not configured*.

**Signalling.** WHEP goes through the backend: `POST /video/feeds/{id}/whep` with the operator's token. MediaMTX credentials never reach the browser. Media is WebRTC from MediaMTX on UDP/TCP 8189, with ICE host candidates from `MEDIAMTX_WEBRTC_HOSTS`. Browsers gather on LAN interfaces, so the machine's LAN IP must be listed next to 127.0.0.1.

**Failures** are classified in the tile:

- *SIGNALLING FAILED*: backend or relay unreachable.
- *SOURCE OFFLINE*: nothing is publishing.
- *VIDEO LINK LOST*: no decoded frames for 1.5 s.

Retry uses back-off of 1, 2, 4, 8, 8, 8 s, then FAILED with a manual retry. The layout is preserved throughout.

**Quality:**

- Real `getStats()` values: resolution, fps, bitrate, RTT, jitter, loss, dropped frames.
- **Auto** steps down to the `lo` rendition after 3 bad samples (loss > 5 %, RTT > 400 ms, or fps below half the feed's own peak), and back up after 10 good ones. Operator overrides: `hi`, `lo`.

**Other controls:**

- **Fullscreen:** element fullscreen on the tile.
- **Picture-in-Picture:** the browser's API; disabled with the reason when unsupported.
- **DVR:** MediaMTX records the path in fMP4. `GET /video/feeds/{id}/recordings` gives the segments, and `/playback?start&duration_s` gives an MP4 clip (authenticated, played from a blob URL).
  - The timeline seeks within a loaded clip, or loads the clip that starts at the chosen time.
  - On test patterns, the barcode in the frame verifies that a seek lands on the recorded moment.

**Latency method.** Every TEST PATTERN frame carries a 64-cell barcode: capture time in epoch ms (simhost clock) plus a frame counter. The browser decodes it from each presented frame (`requestVideoFrameCallback` + canvas). Latency = presentation time − (capture time − clock offset), where the simhost-to-backend clock offset is measured NTP-style (`GET /video/clock-offset`). The result is glass-to-glass minus the display scan-out.

## 6. Detections

**Providers**, on NATS `drone.{UIN}.detection`:

- **`sim_colour_detector`** (simhost): classical colour segmentation of SITL camera frames.
  - It finds the bench's red markers and geolocates them from the drone's Gazebo pose, using the nadir camera model.
  - It is not a trained AI model, and is labelled *SITL CV DETECTOR*.
  - The same marker is re-reported by the same drone at most every 2 min.
- **`test_fixture`** (`POST /detections/test-fixtures`): admin-only, enabled by `DETECTION_TEST_FIXTURES`, labelled *TEST FIXTURE*. Deterministic values, with valid, missing or invalid locations.

**Ingest** is idempotent on `event_id`. Coordinates are validated: an invalid or missing location is stored as `location_error`, never invented. Thumbnails must be PNG or JPEG and at most 200 KB.

**Review** (`POST /detections/{id}/review`) runs in one transaction with a row lock. It records the reviewer, the time and an optional reject reason, and writes the audit row. Repeating the same decision is a no-op; a different decision on an already reviewed detection gives 409.

**Accepting** a detection with a valid location writes `accepted_location` (PostGIS `geography(Point, 4326)`, GiST index). Rejected detections stay in the table and are excluded from the accepted layer.

**Heatmap** (`GET /detections/heatmap`): accepted points snapped to a Web-Mercator grid with `ST_SnapToGrid`, counted once per detection row, with an optional mission filter.

## 7. RBAC

| Action | ADMIN | OPERATOR | ANALYST | OBSERVER |
|---|---|---|---|---|
| View dashboard, video, detections | ✓ | ✓ | ✓ | ✓ |
| Discover / bind / unbind, SITL bench | ✓ | ✓ | – | – |
| Commands incl. arm, fleet-wide | ✓ | ✓ | – | – |
| Start/stop recording, test sources | ✓ | ✓ | – | – |
| Review detections | ✓ | ✓ | ✓ | – |
| Emit test fixtures (test mode) | ✓ | – | – | – |

Enforced on the backend (`RequireRole`). The UI only hides what the role cannot do.

## 8. Running it

```bash
# containers (db, NATS, simhost, MediaMTX); list this machine's LAN IP for WebRTC
MEDIAMTX_WEBRTC_HOSTS=127.0.0.1,<LAN-IP> docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d
cd backend && alembic upgrade head && uvicorn app.main:app --port 8000
cd frontend && npm run dev
```

Then:

1. Open `http://localhost:5173/mission-control` as an operator.
2. Click *Flight controllers*, *Start SITL bench*, then *Auto-discover*.
3. Bind each controller to a SIM drone.
4. Use *Arm* (checked, confirmed), then *Take off*.

## 9. Tests

```bash
cd backend
pytest -q tests/test_mission_control.py                       # API, RBAC, PostGIS, commands (fakes)
pytest -q -s -m gazebo tests/test_mc_sitl_integration.py      # real PX4 SITL bench + relay + detector
docker exec -w /opt/simhost dronematrx_simhost python3 -m pytest -q /opt/simhost/tests
cd ../frontend
npx vitest run
npx playwright test -c playwright.sprint6.config.ts           # full browser E2E (starts its own bench)
```

Measured results: [`SPRINT_6_TEST_REPORT.md`](SPRINT_6_TEST_REPORT.md) and [`PERFORMANCE_RESULTS.md`](PERFORMANCE_RESULTS.md).
