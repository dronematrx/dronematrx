# Drone MatrX

Fleet management, live map/AOI planning, real-time telemetry, CBBA-based
multi-drone mission planning, ORCA collision avoidance with a
NATS JetStream / TimescaleDB communication stack, and Gazebo + PX4 SITL
mission simulation for a drone operations platform. This repo contains a FastAPI + PostGIS backend and a React +
Leaflet frontend.

- Sprint 3 (CBBA engine & mission wizard):
  [`documents/SPRINT3_CBBA_MISSION_PLANNING.md`](documents/SPRINT3_CBBA_MISSION_PLANNING.md)
- Sprint 4 (ORCA engine & communication stack):
  [`docs/sprint4/SPRINT4_ORCA_COMMS.md`](docs/sprint4/SPRINT4_ORCA_COMMS.md);
  test report with measured results and screenshots:
  [`docs/sprint4/SPRINT_4_TEST_REPORT.md`](docs/sprint4/SPRINT_4_TEST_REPORT.md)
- Sprint 5 (Gazebo + PX4 SITL mission simulation):
  [`docs/sprint5/SIMULATION.md`](docs/sprint5/SIMULATION.md); test report:
  [`docs/sprint5/SPRINT_5_TEST_REPORT.md`](docs/sprint5/SPRINT_5_TEST_REPORT.md);
  screenshots and recordings: [`documents/sprint5/`](documents/sprint5/)

## Stack

- **Backend**: FastAPI, SQLAlchemy 2.0, GeoAlchemy2/PostGIS, Alembic, JWT auth, pytest
- **Frontend**: React 19, TypeScript, Vite, Zustand, react-leaflet, react-router
- **Database**: PostgreSQL 16 + PostGIS + TimescaleDB (Docker), host port `5433` (not the default 5432, so it won't clash with a local Postgres install)
- **Messaging**: NATS 2.10 with JetStream (Docker), client port `4222`, monitoring `8222`
- **Live telemetry**: 10 Hz simulator -> ORCA -> comms link (Wi-Fi/LTE/LoRa) -> NATS JetStream -> WebSocket, TimescaleDB and the orchestrator. The WebSocket contract from Sprint 2 is unchanged.

## Prerequisites

- Docker Desktop
- Python 3.10+
- Node.js 18+

## 1. Start the database

```bash
docker compose up -d --build
```

This builds a Postgres 16 + PostGIS + TimescaleDB image and exposes it on
`localhost:5433` (local development credentials `dronematrx`/`dronematrx`/`dronematrx`;
use your own in any shared deployment), and starts NATS JetStream on
`localhost:4222`. Upgrading from Sprint 3: the `--build` is required once,
because TimescaleDB must be preloaded by the database server.

## 2. Backend

```bash
cd backend
python -m venv venv
./venv/Scripts/activate        # Windows; use `source venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
copy .env.example .env         # or `cp .env.example .env`

alembic upgrade head
python scripts/seed.py

uvicorn app.main:app --reload --port 8000
```

The API is now at `http://localhost:8000` (interactive docs at `/docs`), and
the WebSocket telemetry stream at `ws://localhost:8000/ws/telemetry?token=<jwt>`.

Seeded accounts (password for all: `DroneMatrx@2026`):

| Email | Role |
|---|---|
| admin@dronematrx.com | ADMIN |
| operator@dronematrx.com | OPERATOR |
| observer@dronematrx.com | OBSERVER |
| analyst@dronematrx.com | ANALYST |

Run the test suite (spins up its own `dronematrx_test` database on the same
Postgres instance):

```bash
pytest -q
```

Run the Sprint 4 benchmarks (ORCA, NATS, comms fallback, TimescaleDB; needs
Docker; writes `docs/sprint4/performance/*.json` and exits non-zero if an
acceptance target is missed), then rebuild the test report from them:

```bash
python scripts/benchmark_sprint4.py             # or a subset: orca nats fallback timescale
python scripts/generate_sprint4_report.py
```

Run the CBBA benchmark (no database needed; exits non-zero if any scenario
misses the < 2 s target):

```bash
python scripts/benchmark_cbba.py            # 8 drones x 20/30/50 tasks, plus add/remove re-allocation
python scripts/benchmark_cbba.py --tasks 20 50 --repeat 10 --json
```

## 3. Frontend

```bash
cd frontend
npm install
copy .env.example .env         # or `cp .env.example .env`
npm run dev
```

Open `http://localhost:5173`. Log in at `/login`, then use the sidebar
(hamburger icon, top-left) to reach **Mission** (mission list + 6-step
planning wizard), **Flight Plan** (map with the ORCA layer and 8-drone
demo), **Fleet** (fleet wall with per-drone links), **Analyse** (TimescaleDB
telemetry and the NATS message bus), and **Settings** (geofence library).

Frontend checks:

```bash
npm run lint
npm run typecheck
npm test              # Vitest unit tests (planning logic)
npm run build
npx playwright install chromium   # once
npm run test:e2e      # Playwright E2E; needs the DB migrated + seeded; starts backend + vite if not running
npx playwright test e2e/sprint4.spec.ts   # Sprint 4 TEST 1-10; screenshots -> docs/sprint4/evidence/
npx playwright test e2e/sprint5.spec.ts   # Sprint 5 AOI -> Gazebo -> map -> report; needs simhost (below)
```

## 4. Simulation host (Sprint 5)

Gazebo Harmonic + PX4 SITL v1.15.4 run in a Linux container, so this works
on Windows without WSL. The first build compiles PX4 (20–40 min):

```bash
docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d --build simhost
curl http://localhost:8100/health      # versions + capacity (SIM_MAX_INSTANCES, default 8)
```

The backend reaches it at `SIMHOST_URL` (default `http://localhost:8100`).
Then go to **Mission → New Simulation**, draw the AOI, pick the drone count,
task type and flight parameters, and run. The CBBA plan is flown by real PX4
SITL drones over a Gazebo world generated from the AOI's DEM. Tests:

```bash
cd backend
pytest -q -m gazebo -s tests/test_sim_gazebo_integration.py   # real Gazebo + PX4
python scripts/generate_sprint5_report.py                     # rebuild docs/sprint5/SPRINT_5_TEST_REPORT.md
```

Details, architecture and limitations: [`docs/sprint5/SIMULATION.md`](docs/sprint5/SIMULATION.md).

## What's implemented

- **Auth**: JWT access/refresh tokens, role-based access control (ADMIN,
  OPERATOR, OBSERVER, ANALYST) enforced server-side on every mutating route.
- **Fleet management**: CRUD for drones, payloads, and maintenance records;
  live telemetry merged into the fleet table and drone detail panel over
  WebSocket.
- **Map / Flight Plan**: Leaflet map with live drone markers, geofence and
  no-fly-zone polygons, and click-to-draw AOI capture saved against a
  mission via PostGIS geography columns.
- **Telemetry simulator**: each active drone patrols a randomized route
  around its home position, drains/recharges battery, and cycles flight
  modes — enough to exercise the full realtime pipeline without real
  hardware. Offline / in-maintenance drones stay parked.
- **CBBA task allocation (Sprint 3)**: real Consensus-Based Bundle Algorithm
  (bundle construction, bid warping, full consensus decision table,
  convergence detection) behind a pluggable `CoordinationAlgorithm`
  interface; deterministic; 8 drones / 50 tasks allocate in ~0.3 s;
  warm-started re-allocation on drone add/remove.
- **6-step mission wizard (Sprint 3)**: Template → AOI → Flight → Sensors →
  Constraints → Validate, auto-saved to the database per step and resumable
  after refresh; coloured CBBA lanes per drone with ETA labels, battery
  indicators, and coverage %; ETAs update live when flight parameters change.
- **Mission state machine (Sprint 3)**: DRAFT → PLANNING → VALIDATING → ARMED
  → ACTIVE → COMPLETED / ABORTED with server-side guards, state history, and
  audit logs; invalid transitions are rejected.
- **Geofences & NFZ enforcement (Sprint 3)**: inclusion / exclusion / NFZ
  zones, GeoJSON + KML import/export, PostGIS checks of AOI, routes, and
  launch points; an NFZ conflict blocks arming in the UI *and* the API.
- **ORCA collision avoidance (Sprint 4)**: RVO2-based ORCA at 10 Hz with
  conflict prediction up to 10 s ahead, altitude bands (band 0 reserved for
  return-to-home) and an RTH priority queue; map layer with velocity /
  avoidance vectors, amber collision alerts, event timeline, and an 8-drone
  demo scenario. 50 consecutive 8-drone runs with zero collisions.
- **NATS JetStream (Sprint 4)**: `drone.{UIN}.telemetry|state|alert|command`,
  durable consumers (orchestrator, UI, telemetry writer, commands), 24 h
  replay window, ~1 ms delivery; Message bus tab under **Analyse**.
- **Communication fallback (Sprint 4)**: Wi-Fi -> LTE -> LoRa monitored at
  2 Hz with hysteresis, fallback < 1 s; per-drone link on the Fleet wall and
  fleet table.
- **TimescaleDB telemetry (Sprint 4)**: `drone_telemetry` hypertable fed at
  1 Hz from NATS, no data loss for 16 drones, 1-hour queries < 500 ms;
  Telemetry tab under **Analyse**.

## Repo layout

```
backend/    FastAPI app, Alembic migrations, seed script, pytest suite
frontend/   React app (Vite)
db/         PostGIS + TimescaleDB Docker image build context
documents/  Product/design reference docs + Sprint 3 technical reference
docs/sprint4/  Sprint 4 technical reference, test report, evidence/ screenshots, performance/ JSON
docs/sprint5/  Sprint 5 simulation reference, test report, performance/ JSON + JUnit
documents/sprint5/  Sprint 5 screenshots/ and recordings/
sim/        simulation host image: Gazebo + PX4 SITL, simhost service (FastAPI + pymavlink), PX4 patch
```

Mission planning code map:

```
backend/app/services/coordination/   algorithm package (no DB/UI deps): CBBA, utility, comms model, task generation
backend/app/services/mission_planning_service.py   wizard persistence, CBBA runs, validation, readiness
backend/app/services/mission_state_service.py      lifecycle state machine
backend/app/services/spatial_validation.py         PostGIS NFZ / geofence checks
frontend/src/pages/MissionWizardPage.tsx           6-step wizard; components in components/mission/
frontend/src/components/map/MissionMap.tsx         the single shared Leaflet map
```

Sprint 4 code map:

```
backend/app/services/orca/        ORCA solver, engine, altitude bands, RTH queue, scenarios, live runtime
backend/app/messaging/            NATS JetStream bus (DroneBus) and subject naming
backend/app/services/comms/       link thresholds, Wi-Fi/LTE/LoRa transports, CommunicationManager
backend/app/services/telemetry/   simulator, TimescaleDB store + NATS ingestor
backend/app/services/runtime.py   FleetRuntime: wires simulator, ORCA, comms, NATS, consumers, WebSocket
frontend/src/components/map/OrcaLayer.tsx, components/ops/   ORCA map layer, panels, fleet wall
frontend/src/pages/AnalyticsPage.tsx                         Telemetry + Message bus tabs
```

Sprint 5 code map:

```
backend/app/services/simulation/   ENU frames, DEM, world generator, sim fleet, adapters, mission builder, orchestrator, reports
backend/app/api/routes/simulations.py   simulation REST API
sim/simhost/                       SITL process manager, Gazebo control, MAVLink bridge, session, HTTP server
frontend/src/pages/SimulationPage.tsx, components/simulation/   setup, map layers, controls, failures, report + diff
```

## Design system

The frontend follows `documents/Drone_Matrx_Design_Language_Specification.docx`
(navy/charcoal/slate palette, Aldrich/Rajdhani/Space Mono type, `--dm-*`
design tokens in `frontend/src/index.css`). New pages should reuse the
shared components in `frontend/src/components/layout` and
`frontend/src/components/ui` rather than introducing new visual patterns.

Sprint 3 added semantic tokens for the new meanings it introduced (geofence
types, lane palette, armed/warning status) in `index.css`, per spec 2.6
("new status meanings require a new token").

Note: `frontend/src/components/{LandingPage,MissionControl,DigitalTwinSimulator,AnalyticsDashboard}.tsx`
and `useSwarmStore` are an earlier client-only swarm-simulation prototype
(cyber/neon theme) kept for reference; they are not wired to the backend
described above and don't follow the current design spec.
