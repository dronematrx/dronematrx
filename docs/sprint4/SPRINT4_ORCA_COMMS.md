# Sprint 4: ORCA Engine & Communication Stack

Technical reference for what Sprint 4 added on top of Sprints 1-3 (fleet,
map/AOI, telemetry, CBBA mission planning). Measured results and evidence
screenshots are in [`SPRINT_4_TEST_REPORT.md`](SPRINT_4_TEST_REPORT.md).

## 1. Architecture

```
 simulator (10 Hz motion)            ORCA runtime (10 Hz)
 app/services/telemetry/simulator ── velocity_filter ──> app/services/orca/runtime.py
        │ telemetry 1 Hz, state 5 Hz, alerts            (bands, RTH queue, events)
        ▼
 CommunicationManager (2 Hz link monitor)   app/services/comms/
        │ picks the active transport: Wi-Fi ─> LTE ─> LoRa
        ▼
 outbox queue ─> DroneBus.publish ──> NATS JetStream stream "DRONES"
                                       subjects drone.{UIN}.telemetry|state|alert|command
                                        │
          ┌──────────────────┬──────────┴─────────┬────────────────────┐
          ▼                  ▼                    ▼                    ▼
   durable "orchestrator"  durable "ui"   durable "telemetry-writer"  durable "command-dispatch"
   latency stats,          TelemetryManager  TelemetryIngestor         applies HOLD/RESUME/RTH
   recent-message view     -> WebSocket +     -> TimescaleDB           to the simulator
                           Drone table        hypertable drone_telemetry
```

`app/services/runtime.py` (`FleetRuntime`) owns all of this and is started by
the FastAPI lifespan when the simulator is enabled. The control loop never
awaits the network: messages go to an in-process outbox and a separate
publisher task drains it. The publisher and the consumers use **separate NATS
connections** (sharing one connection added ~40 ms of head-of-line delay
under load).

The WebSocket (`/ws/telemetry`) carries the existing `telemetry` messages plus
`orca` and `comms` snapshots at 5 Hz and `drone_alert` / `comms_event`
messages as they happen.

**Degraded mode**: if NATS is unreachable at start-up, the runtime logs
`runtime.degraded` and feeds telemetry directly to the WebSocket/UI;
TimescaleDB ingestion, replay and commands are unavailable until NATS is back.
`GET /health` reports `nats_connected` and `orca_enabled`.

CBBA is unchanged: CBBA assigns tasks (where each drone should go); ORCA only
adjusts each drone's velocity locally so that it gets there without coming
too close to another drone.

## 2. ORCA (`backend/app/services/orca/`)

| Module | Role |
|---|---|
| `solver.py` | RVO2 port: ORCA half-plane per neighbour, `linear_program1/2/3` (2-D LP with 3-D fallback when infeasible) |
| `engine.py` | `OrcaEngine.step(agents, sim_time)`: validate, predict conflicts, build constraints, solve per agent, emit events |
| `deconfliction.py` | time-to-collision maths, `AltitudeBandManager`, `RTHPolicy` + `RTHPriorityQueue` |
| `simulation.py` | offline simulator for tests/benchmarks (metrics, deadlock policy) |
| `scenarios.py` | circle swap, crossing streams, converging, random box, head-on lanes; `eight_drone_run(i)` |
| `runtime.py` | live supervision for the simulator: bands, RTH, timing stats, event feed |
| `frames.py` | local ENU metres <-> WGS84 |

### Parameters (`OrcaParams`)

| Parameter | Default | Meaning |
|---|---|---|
| `agent_radius_m` | 1.0 | physical radius |
| `safety_margin_m` | 2.0 | added to the radius; the protected envelope between two drones is 2 x (1 + 2) = 6 m |
| `time_horizon_s` | 5.0 | ORCA looks this far ahead when building constraints |
| `timestep_s` | 0.1 | 10 Hz |
| `max_speed_mps` | 15.0 | upper bound on the solved velocity (per-drone value wins if lower) |
| `neighbor_radius_m` / `max_neighbors` | 150 / 10 | neighbour search |
| `altitude_separation_m` | 8.0 | drones further apart vertically are not neighbours |
| `prediction_horizon_s` | 10.0 | conflicts are predicted (and shown amber in the UI) up to 10 s ahead |
| `degraded_uncertainty_s` / `max_uncertainty_m` | 1.0 / 10.0 | radius inflation for a neighbour whose link is degraded |

**Responsibility**: normally each drone takes half of the avoidance. A
neighbour on a degraded link (LoRa or lost) cannot be relied on to react, so
the healthy drone takes full responsibility and treats it as larger (position
uncertainty = speed x 1 s, capped at 10 m). Drones at identical positions are
separated deterministically by id.

**Prediction**: time-to-collision is computed on each pair's *preferred*
velocities (the plan), capped by each drone's remaining plan horizon so that
parallel lanes that end before they meet are not flagged.

**Deadlock**: if a drone more than 10 m from its goal has been nearly
stopped (< 15 % of its speed) under ORCA constraints for 2 s, its preferred
velocity is rotated 60 degrees to the right for 3 s. Every drone uses the same
give-way-right rule, so a symmetric stand-off turns into circulation instead
of mirrored stalling; ORCA still guarantees separation during the detour.

**Events** (`GET /orca/events`, map timeline): `conflict_predicted`,
`constraint_generated`, `avoidance_applied`, `avoidance_released`,
`conflict_cleared` (with the minimum separation reached), plus RTH and band events.

### Altitude bands

Band 0 (30 m) is reserved for drones returning home. Cruise bands 1-4 fly at
40 / 50 / 60 / 70 m with at most 8 drones each; when every band is full a
drone is told to hold. Because bands are 10 m apart and the vertical
separation threshold is 8 m, drones in different bands never constrain each other.

### Return-to-home priority

1. critical battery (SOC <= 15 %)
2. emergency / fault
3. communication degraded
4. normal (mission complete, operator command)

Ties: lower SOC first, then earlier request, then drone id. At most **2**
drones fly home at once; queued drones hold position and are released one
slot at a time, so return paths do not converge on the home point together.

## 3. NATS JetStream (`backend/app/messaging/`)

| Setting | Value |
|---|---|
| Stream | `DRONES` (`NATS_STREAM`) |
| Subjects | `drone.{UIN}.telemetry`, `.state`, `.alert`, `.command`; the PRD's `drone/{UIN}/telemetry` is the same topic in NATS `.` notation. UINs must match `^[A-Za-z0-9_-]{1,64}$` |
| Retention | limits, file storage, `max_age` 24 h (the replay window), duplicate window 120 s |
| De-duplication | every publish sets `Nats-Msg-Id`; JetStream drops repeats inside the window |
| Headers | `Dm-Published-Ns` (latency measurement), `Dm-Link` (transport used) |
| Consumers | durable push consumers with a queue group each: `orchestrator` (telemetry, state, alert, command), `ui` (telemetry, state, alert), `telemetry-writer` (telemetry), `command-dispatch` (command). Explicit ack; a handler exception NAKs the message so it is redelivered |
| Replay | `DroneBus.replay(kind, uin, start_time | start_seq, limit)`: an ephemeral pull consumer with `BY_START_TIME` / `BY_START_SEQUENCE`; does not disturb the durables |

A durable consumer that was stopped resumes exactly where it left off when it
reconnects (tested: `test_consumer_restart_resumes_without_loss`).

## 4. Communication fallback (`backend/app/services/comms/`)

Chain: **Wi-Fi -> LTE -> LoRa**, monitored at 2 Hz (`COMMS_POLL_HZ`). A link
is *down* when any breach threshold is crossed and *good* again only when all
recover thresholds are met:

| Link | Breach: RSSI / loss / latency | Recover: RSSI / loss / latency |
|---|---|---|
| Wi-Fi | < -75 dBm / > 10 % / > 150 ms | >= -67 dBm / <= 3 % / <= 80 ms |
| LTE | < -105 dBm / > 15 % / > 300 ms | >= -95 dBm / <= 5 % / <= 150 ms |
| LoRa | < -125 dBm / > 30 % / > 2000 ms | >= -118 dBm / <= 10 % / <= 1200 ms |

Hysteresis: falling back is immediate (next poll, <= 500 ms); returning to a
better link requires it to be good for **3 s** and at least **2 s** on the
current link, which prevents flapping. If every link is down a
`all_links_lost` alert is raised and the last transport is kept.

The transports really change behaviour: LoRa sends compact frames and drops
state messages over 255 bytes (telemetry and alerts still get through), and
every published message records its link in the `Dm-Link` header, which is
how the UI shows the live link per message.

The radio environment is simulated (`LinkEnvironment`): an operator can
degrade / restore a link per drone from the Fleet wall or
`POST /comms/{id}/degrade|restore` (ADMIN/OPERATOR, audited).

## 5. TimescaleDB telemetry (`backend/app/services/telemetry/`)

Migration `b7d3c1e9a402` creates the `timescaledb` extension and the
`drone_telemetry` hypertable:

| Column | Type | Notes |
|---|---|---|
| `timestamp` | timestamptz | partition column, 1-day chunks |
| `drone_id` | uuid | FK to `drones` |
| `lat`, `lon` | double | CHECK: lat -90..90, lon -180..180 |
| `alt`, `speed`, `heading`, `soc`, `rssi` | double | CHECK: speed >= 0, 0 <= heading < 360, soc 0-100, rssi -150..0 or NULL |
| `link_type` | text | wifi / lte / lora |

Primary key `(timestamp, drone_id)` and `ON CONFLICT DO NOTHING`, so a
redelivered NATS message never creates a duplicate row; index
`ix_drone_telemetry_drone_time (drone_id, timestamp DESC)`.

`TelemetryIngestor` consumes `telemetry-writer`, validates each record
(unknown drone, future timestamp, out-of-range values are rejected and
TERM'd), batches inserts with `execute_values` every 0.2 s, and acks only
after the commit. A drone registered after start-up is picked up on the first
cache miss.

**Retention**: 30 days. The Apache-2.0 TimescaleDB build available from PGDG
does not include `add_retention_policy`, so the runtime calls
`drop_chunks(older_than => 30 days)` once an hour instead.

## 6. API (all endpoints require a JWT)

| Method | Path | Roles | Purpose |
|---|---|---|---|
| POST | `/orca/step` | any | one ORCA step for the posted agents -> safe velocities |
| POST | `/orca/simulate` | any | run a named or custom scenario, returns metrics + events |
| GET | `/orca/state` | any | live ORCA view: drones, predictions, timing, RTH queue (org-scoped) |
| GET | `/orca/events` | any | event timeline, `?since=&limit=` (org-scoped) |
| GET | `/orca/demo` | any | demo scenario status |
| POST | `/orca/demo/start`, `/orca/demo/stop` | ADMIN, OPERATOR | 8-drone demo scenario |
| POST | `/orca/command` | ADMIN, OPERATOR | HOLD / RESUME / RTH via `drone.{UIN}.command` (202) |
| POST | `/orca/fleet/reset` | ADMIN | reset the simulated fleet to its seeded state |
| GET | `/comms/links` | any | per-drone link state (org-scoped) |
| GET | `/comms/events` | any | breaches, fallbacks, recoveries with timings (org-scoped) |
| POST | `/comms/{id}/degrade`, `/comms/{id}/restore` | ADMIN, OPERATOR | simulated radio degradation (audited) |
| GET | `/telemetry/drones/{id}` | any | one drone's window (default 1 h) from the hypertable |
| GET | `/telemetry/fleet` | any | the org's fleet window, columnar |
| GET | `/telemetry/stats` | any | rows per drone, ingestor counters (org-scoped) |
| GET | `/messaging/status` | any | stream/consumer state, delivery latency, recent messages (org-scoped) |
| POST | `/messaging/replay` | any | replay from JetStream by time or sequence (org-scoped; foreign UIN -> 404) |

Live endpoints return **503** when the runtime is not running (e.g. the test
app, or `TELEMETRY_SIMULATOR_ENABLED=false`).

## 7. Frontend

- **Flight Plan map**: ORCA layer toggle; per drone the velocity vector
  (white), planned direction (dashed), ORCA-safe velocity when avoiding (cyan)
  and the predicted conflict point (amber, with TTC). Side panels: amber
  *Collision predicted* banner, 8-drone demo controls, ORCA engine stats
  (rate, missed cycles, compute, resolution), RTH queue & bands, event
  timeline; *Conflict zone* / *All drones* framing buttons.
- **Fleet**: *Fleet wall* with one card per drone (active link pill, RSSI,
  latency, loss, every link's quality, degrade/restore buttons) and a Link
  column in the fleet table.
- **Analyse** (`/analytics`): *Telemetry* tab (rows per drone, ingestor health,
  1-hour query with timings) and *Message bus* tab (stream, consumers,
  latency, live message flow, JetStream replay).

New colour meanings got new tokens in `index.css` (`--dm-orca-*`,
`--dm-link-wifi|lte|lora`), per design spec 2.6.

## 8. Configuration (`backend/.env`)

| Variable | Default | |
|---|---|---|
| `NATS_ENABLED` | `true` | `false` runs without NATS (degraded mode) |
| `NATS_URL` | `nats://localhost:4222` | put credentials here (or in the URL's userinfo) in non-local deployments; never commit them |
| `NATS_STREAM` | `DRONES` | |
| `NATS_MAX_AGE_HOURS` | `24` | replay window |
| `NATS_CONNECT_TIMEOUT_S` | `2` | start-up is bounded; after that the runtime goes degraded |
| `NATS_MONITOR_URL` | `http://localhost:8222` | |
| `ORCA_ENABLED` / `ORCA_RATE_HZ` | `true` / `10` | |
| `COMMS_POLL_HZ` | `2` | |

Secrets: the JWT used by the WebSocket travels as `?token=`; a log filter
(`app/core/logging.py`) redacts `token`, `access_token`, `refresh_token` and
`password` query values in uvicorn's access/handshake logs.

## 9. Running and testing

```bash
docker compose up -d --build        # rebuilds the DB image (adds TimescaleDB) and starts NATS JetStream
cd backend
alembic upgrade head                # adds the drone_telemetry hypertable
python scripts/seed.py
uvicorn app.main:app --port 8000

pytest -q                                   # 277 tests (needs Docker DB + NATS)
python scripts/benchmark_sprint4.py         # ORCA / NATS / fallback / TimescaleDB -> docs/sprint4/performance/*.json
python scripts/benchmark_sprint4.py orca    # one section only (orca | nats | fallback | timescale)
python scripts/generate_sprint4_report.py   # rebuilds SPRINT_4_TEST_REPORT.md from the JSON files

cd ../frontend
npx playwright test e2e/sprint4.spec.ts     # TEST 1-10, screenshots -> docs/sprint4/evidence/
```

Existing installations must rebuild the database image once
(`docker compose up -d --build`): TimescaleDB has to be in
`shared_preload_libraries`, which the compose file now sets.

## 10. Known limitations

- The radio environment is simulated; the fallback logic, thresholds,
  hysteresis and transport switching are real.
- CBBA and ORCA run centrally in the backend for the simulated fleet;
  on-board/edge execution is future work.
- The telemetry WebSocket broadcast is not organization-scoped (unchanged
  since Sprint 2); all Sprint 4 REST endpoints are.
- On a heavily loaded host the live 10 Hz loop occasionally misses a single
  cycle (about 1 in 700 during the full E2E run); the average rate stays 10 Hz.
- 24 h replay is verified by configuration (`max_age`) plus functional
  replay tests, not by a 24-hour soak.
- `/messaging/replay` takes about 1 s because the ephemeral consumer waits out
  its fetch timeout on the last batch.
- Map tiles need internet (OpenStreetMap).
