# Sprint 3 — CBBA Engine & Mission Wizard

Technical reference for the Sprint 3 mission-planning stack: architecture,
the CBBA algorithm and its extension points, the API, the mission state
machine, geofence/NFZ enforcement, testing, and known limitations.

## 1. Architecture

```
 React (Vite)                         FastAPI (edge orchestrator)                    PostgreSQL + PostGIS
 ───────────────                      ─────────────────────────────                  ────────────────────
 MissionsPage ─┐                      api/routes/mission_planning.py                 missions (+config, wizard_step,
 MissionWizard │  REST                  │                                             validation_report)
  Step 1-6     ├────────────────────────┤ MissionPlanningService ──── tasks/plans ──► mission_tasks
  MissionMap   │                        │   ├─ task_generation (AOI → lanes)          mission_plans
  CbbaLanes    │                        │   ├─ coordination.get_algorithm("cbba")     task_bundles (per drone)
 useMissionStore◄── WS mission events ──┤   │     └─ CBBAEngine ◄── UtilityModel       task_assignments
       ▲          (same /ws/telemetry)  │   │                  ◄── CommunicationModel  mission_state_history
       │                                │   ├─ SpatialValidator ── ST_Intersects/Covers ► geofences
 useDroneStore ◄── telemetry ───────────┤   └─ readiness checks                       audit_logs
                                        │ MissionStateService (guards, history, audit)
                                        └ mission_events.publish (org-scoped)
```

The algorithm package `app/services/coordination/` has **no database, HTTP or
UI dependencies**. It works in a local metric frame (metres east/north of the
AOI centroid) on plain dataclasses. `MissionPlanningService` is the only module
that translates ORM rows ⇄ algorithm types.

| Concern | Module |
|---|---|
| Task representation | `coordination/models.py` (`Agent`, `Task`, `AllocationProblem`, `AllocationResult`) |
| Task generation | `coordination/task_generation.py` (lanes from AOI + sensor footprint) |
| Bid / utility | `coordination/utility.py` (`UtilityModel`, `DefaultUtility`) |
| Consensus | `coordination/cbba.py` (`CBBAEngine`) |
| Communication model | `coordination/communication.py` (`FullyConnected`, `RangeLimited`) |
| Coordination strategy registry | `coordination/base.py` (`CoordinationAlgorithm`, `register_algorithm`) |
| Reallocation | `CBBAEngine.reallocate` (warm start) + `MissionPlanningService.reallocate` |
| Visualization | frontend `components/map/CbbaLanes.tsx`, `components/mission/*` |

### Extension point for future algorithms

```
CoordinationAlgorithm            (allocate / reallocate)
 ├── CBBAEngine                  "cbba"   — Sprint 3
 ├── ClusteredCBBA               future
 ├── LocalAuction                future
 └── AdaptiveHybrid (ACHBC)      future research algorithm — NOT implemented
```

A new strategy is one class implementing `CoordinationAlgorithm` plus
`register_algorithm("name", Factory)`. Missions select it through
`constraints.algorithm` (wizard Step 5) or `POST /missions/{id}/cbba/allocate
{"algorithm": "name"}`. Plans store the algorithm name, so reallocation uses the
same strategy.

## 2. CBBA

Implementation of the Consensus-Based Bundle Algorithm (Choi, Brunet & How,
IEEE T-RO 2009). Each agent keeps a **bundle** (tasks in the order added), a
**path** (execution order), **winning bids `y`**, **winning agents `z`**,
**timestamps `s`**, and its last computed **task utilities**.

Each iteration:

1. **Bundle construction** (`build_bundles` → `calculate_bids`): every agent
   repeatedly adds the task whose marginal bid is highest *and* beats the
   currently known winning bid (ties: lower agent index).
2. **Consensus** (`consensus` → `resolve_conflicts`): agents exchange
   `(y, z, s)` with their neighbours and apply the full CBBA
   update / reset / leave decision table (Table 1 of the paper).
3. **Bundle rebuilding** (`update_bundles`): an agent outbid on bundle entry *n*
   releases entries *n+1…* (their marginal scores depended on it) and rebuilds
   in the next iteration.
4. **Convergence** (`check_convergence`): stop when no agent's bundle, `z` or `y`
   changed during a full iteration **and** all agents agree on every winner.
   Stalls without agreement (e.g. partitioned networks) stop after
   *diameter + 1* frozen iterations and are reported as not converged. A
   wall-clock cap (`CBBA_TIME_LIMIT_SECONDS`, default 5 s) reports a timeout.

Even if a run stops early, the output is **never double-booked**: when two
agents still claim a task, the higher bid keeps it.

**Bid warping** (Johnson et al. 2012): an agent never bids more for a later
bundle entry than for an earlier one. Path-insertion scores lack the
diminishing-marginal-gain property CBBA's convergence proof needs; warping
restores it.

**Determinism**: no randomness, fixed processing order, deterministic tie
breaks. The same input always yields the same allocation (tested).

### Input / output

Input (`AllocationProblem`):
- **Agents:** home position, battery %, speed, capabilities (working payload
  types), battery drain rate, RTH reserve, max tasks, max range, and max
  flight time.
- **Tasks:** segment endpoints (or a point), priority, required capability,
  and hover time.

Output (`AllocationResult`):
- converged / timed_out, iterations, consensus rounds, messages exchanged,
  convergence time
- `assignments` (task → drone), winning bids
- per-drone `AgentPlan`: ordered path with per-task ETA and energy, distance,
  duration, battery start/end, waypoints
- `unassigned` with a reason, one of: `no_drone_with_<sensor>`,
  `exceeds_battery_range_or_flight_time`, `insufficient_capacity`, or
  `no_drones_available`
- total distance, makespan, and energy
- an iteration history, for research

### Utility function (`DefaultUtility`)

```
score(path) = Σ_j (reward_j + priority_bonus_j) · λ^{arrival_j}      # time-discounted reward
              − w_distance · metres flown (incl. return home)        # travel cost
              − w_energy · battery% used / battery fraction available # energy cost
bid(task)   = best marginal score over every insertion slot / segment direction
```

- reward = 1000 × task.reward; priority bonus = 250 × (priority − 1)
- λ = 0.5^(1/900 s) (a task reached 15 min later is worth half). This is the
  time penalty that spreads work across the swarm and shortens the mission.
- Feasibility is a hard constraint on each candidate path: the drone must
  carry the capability, have bundle capacity, stay within max range and max
  flight time, and finish with `battery − energy(path + return home) ≥ RTH reserve`.
- Insertion is O(len(path)) per task: inserting a task delays every later
  task by the same Δt, so their discounted value scales by λ^Δt. A test
  cross-checks this against brute-force re-scoring.
- To experiment, subclass `UtilityModel` (only `task_value` and `path_score`
  are required; the generic `calculate_bid` handles insertion).

### Coverage tasks

1. Swath width is `2·alt·tan(hfov/2)`, using the sensor's field of view:
   camera 84°, thermal 61°, multispectral 47°, lidar 70°; a sprayer uses a
   fixed 7 m swath.
2. Lane spacing is `swath·(1 − overlap)`.
3. Lanes run along the AOI's long axis, or along a locked heading. The
   crosshatch pattern adds a second, perpendicular pass.
4. Lanes are clipped to the polygon and cut into ~150 m segments; each
   segment is one task.
5. Each required sensor gets its own coverage pass.
6. Step 5 inspection points become hover tasks.

Coverage % = the imaged area of the assigned segments (each buffered by
swath/2) inside the AOI, divided by the AOI area, computed with shapely in
the local metric frame.

### Reallocation (drone add / remove)

`CBBAEngine.reallocate` warm-starts from the previous plan:
- Surviving drones keep their bundle prefix that is still feasible for their
  current state.
- A removed drone's tasks start unassigned.
- New drones start empty.

CBBA then only resolves what changed. The plan's `diagnostics.history` records
`tasks_moved`. A full re-run (`allocate`) remains available, and both are
tested.

## 3. Mission wizard (frontend `/missions/:id/plan`)

| Step | Content | Persisted as |
|---|---|---|
| 1 Template | survey / search / inspection / mapping / custom; name | `missions.template`, `config.template` (pre-fills steps 3–4) |
| 2 AOI | polygon or rectangle drawn on the shared map, existing AOI, or copy from another mission | `missions.aoi_geometry` (PostGIS geography) |
| 3 Flight | altitude (AGL/MSL), speed, overlap, pattern, heading lock, RTH on completion, RTH battery reserve, priority | `config.flight` |
| 4 Sensors | required sensors, drone roster, per-drone payload; compatibility enforced | `config.sensors` |
| 5 Constraints | min launch battery, max flight time, max range, max tasks per drone, enforced geofences (NFZs always enforced), link-loss policy, accept-unassigned, inspection points | `config.constraints` |
| 6 Validate | CBBA + all checks, lanes, ETAs, battery, coverage, CBBA details; Arm / Activate / Complete / Abort | `mission_plans`, `task_bundles`, `task_assignments`, `missions.validation_report` |

How the wizard behaves:
- **Auto-save:** every Next and every Save & Exit sends the step to the
  backend (`PUT /missions/{id}/wizard/steps/{n}`).
- **Resume:** a refresh, or Resume from the mission list, restores
  everything from the database.
- **Step order:** steps must be completed in order. Invalid data is
  rejected both client-side and server-side (422), and nothing invalid is
  persisted.
- **Live re-planning:** once a plan exists, Step 3 changes are auto-saved
  (debounced) and the server re-plans. ETAs, duration and coverage update
  on the map and in the summary without a reload.
- **Real-time sync:** mission events arrive over the existing telemetry
  WebSocket (org-scoped), so other clients refresh too.

## 4. Mission state machine

```
DRAFT ─► PLANNING ─► VALIDATING ─► ARMED ─► ACTIVE ─► COMPLETED
  │          │            │  ▲        │  │      │
  │          │            ▼  │        │  │      │
  │          │         PLANNING ◄─────┘  │      │   (VALIDATING→PLANNING: edit; ARMED→VALIDATING: disarm)
  └──────────┴────────────┴──────────────┴──────┴─► ABORTED
```

Any edge not in `TRANSITIONS` (`mission_state_service.py`) returns
**409 INVALID_TRANSITION**. That includes DRAFT→ACTIVE, ACTIVE→DRAFT,
COMPLETED→ACTIVE, and anything out of ABORTED. Every legal transition:
1. validates the edge
2. runs the guards
3. updates the state (start/end time)
4. writes a `mission_state_history` row
5. writes an `audit_logs` row (`mission_state_changed`)
6. broadcasts `mission.state_changed`

PATCH `/missions/{id}` with a `status` goes through the same service.

Guards (server-side, never trusting the client or a cached report):
- **→ VALIDATING:** wizard steps 1–5 complete.
- **→ ARMED:** re-runs the full validation now. It requires:
  - AOI valid and the plan current
  - CBBA converged
  - no unassigned tasks, unless explicitly accepted
  - every assigned drone still available and above the battery limits
  - landing SOC ≥ RTH reserve
  - no NFZ, exclusion, inclusion or altitude-ceiling conflict

  Failure returns **422 TRANSITION_BLOCKED** with the full report.
- **→ ACTIVE:** launch readiness. It requires:
  - plan not stale
  - every assigned drone active, with status online/active/warning and a
    GPS fix
  - telemetry newer than `MISSION_TELEMETRY_MAX_AGE_SECONDS` (default 120 s)
  - battery ≥ the launch minimum, and enough battery to finish the bundle
    above the reserve
  - no drone currently inside an NFZ

## 5. Geofences and NFZ enforcement

There are three types: **inclusion** (stay inside; blue dashed outline),
**exclusion** (stay outside; red), and **nfz** (regulatory; red hatched,
always enforced). The Sprint 2 `geofence` type was migrated to `inclusion`
and is still accepted on input.

- **CRUD:** `GET/POST /geofences`, `GET/PATCH/DELETE /geofences/{id}`.
- **Import:** `POST /geofences/import` accepts GeoJSON (FeatureCollection,
  Feature, Polygon or MultiPolygon) or KML 2.2. KML with DOCTYPE/ENTITY
  declarations is rejected, and the limit is 2 MB / 500 features.
- **Export:** `GET /geofences/export?format=geojson|kml`.
- **UI:** Settings → Geofence Library.

Spatial checks (`spatial_validation.SpatialValidator`, all in PostGIS):
- AOI ∩ NFZ / exclusion zone (`ST_Intersects`, intersection geometry
  returned for the map overlay)
- planned route, including every waypoint, ∩ NFZ / exclusion
  (`ST_Intersects`)
- launch point inside an NFZ / exclusion zone (`ST_Covers`)
- AOI and routes ⊆ the union of the selected inclusion zones (`ST_Covers`
  over `ST_Union`)
- inclusion-zone altitude ceiling below the planned altitude

A conflict shows **⚠ NFZ CONFLICT** in Step 6, pulses on the map, disables
Arm, and the backend refuses VALIDATING→ARMED.

## 6. API (all RBAC-protected; writes need ADMIN or OPERATOR)

| Method | Path | Purpose |
|---|---|---|
| GET | `/missions/templates` | Wizard templates |
| GET | `/missions/{id}/wizard` | Wizard state (resume) |
| PUT | `/missions/{id}/wizard/steps/{1-5}` | Save a step (`{"data": {...}}`) |
| POST | `/missions/{id}/cbba/allocate` (alias `/plan`) | Run CBBA, persist plan |
| GET | `/missions/{id}/cbba` | Current plan: bundles, waypoints, ETAs, tasks, diagnostics |
| POST | `/missions/{id}/cbba/reallocate` | `{"add_drone_ids": [], "remove_drone_ids": []}` |
| POST / DELETE | `/missions/{id}/drones`, `/missions/{id}/drones/{drone_id}` | Add / remove drone + re-allocate |
| POST | `/missions/{id}/validate` | Step 6: plan if needed + all checks; PLANNING→VALIDATING |
| GET | `/missions/{id}/readiness` | ARMED→ACTIVE readiness checks |
| POST | `/missions/{id}/transition` | `{"target_state": "...", "reason": "..."}` |
| GET | `/missions/{id}/state-history` | Transition history |
| GET / POST / PATCH / DELETE | `/geofences…` | CRUD (types inclusion / exclusion / nfz) |
| POST | `/geofences/import` | GeoJSON / KML import |
| GET | `/geofences/export` | GeoJSON / KML export |

Error codes:
- 401: unauthenticated
- 403: role not permitted
- 404: not found, or another organization's mission
- 409: invalid transition or locked mission
- 422: `PlanningError` (`code` field), schema error, or `TRANSITION_BLOCKED`

WebSocket events (`{"type": "mission", ...}` on `/ws/telemetry`, org-scoped):
`mission.state_changed`, `mission.plan_updated`, `mission.validated`,
`mission.drones_changed`, `mission.wizard_saved`.

## 7. Database (migration `45a893a48f92`)

Added:
- `mission_tasks`: the task's PostGIS geometry, priority, required
  capability, status, and unassigned reason
- `mission_plans`: one row per algorithm run, holding metrics, parameters
  and diagnostics, with `is_current`
- `task_assignments`: bid, order, ETA and energy per task
- `mission_state_history`

Extended:
- `missions`: `template`, `wizard_step`, `config` (JSONB), `validation_report`,
  `validated_at`
- `drones`: `max_speed`, `endurance_minutes`

Reshaped: `task_bundles`, which was never written in Sprint 2, is now the
per-drone CBBA bundle (path, waypoints, route geography, ETA, energy,
battery, coverage, colour index).

Geofence type check: `inclusion | exclusion | nfz`. The migration
downgrades cleanly (`alembic downgrade -1`).

## 8. Seed data (`python scripts/seed.py`, deterministic)

- **Drones:** 8 with varied model, battery, status, payloads, speed and
  endurance. Telemetry state is reset on every run.
- **Geofences:** 2 inclusion zones, 1 exclusion zone (Village Market Crowd
  Area) and 1 NFZ (Airport Approach).
- **Missions:** three, planned through the real service:
  - *Solar Farm Survey*: 24 tasks, converges, validation passes (ready to
    arm).
  - *Lidar Corridor Inspection*: a lidar inspection point no available
    drone can fly (unassigned, so validation fails).
  - *Airport Approach Survey*: AOI and routes cross the NFZ (blocked).

## 9. Testing

```bash
# backend (needs `docker compose up -d`)
cd backend && ./venv/Scripts/python -m pytest -q              # all tests, incl. Sprint 1-2 regression
./venv/Scripts/python scripts/benchmark_cbba.py              # CBBA benchmark table (exit code 1 on failure)

# frontend
cd frontend
npm run lint && npm run typecheck && npm test && npm run build
npm run test:e2e        # Playwright; starts backend + vite if not running; needs migrated + seeded DB
```

| Group | Files |
|---|---|
| A CBBA unit | `tests/test_cbba_engine.py` |
| B reallocation, C performance | `tests/test_cbba_reallocation.py`, `tests/test_mission_planning_api.py`, `scripts/benchmark_cbba.py` |
| D wizard | `tests/test_mission_wizard.py`, `e2e/mission-wizard.spec.ts` |
| E state machine | `tests/test_mission_state_machine.py` |
| F NFZ | `tests/test_nfz_enforcement.py`, `e2e/mission-wizard.spec.ts` (negative path) |
| G geofence | `tests/test_geofence_io.py`, `tests/test_geofences.py` |
| H RBAC | `tests/test_rbac_planning.py` |
| I API | `tests/test_mission_planning_api.py`, `tests/test_mission_events_ws.py` |
| Frontend logic | `src/__tests__/planning-logic.test.ts` (Vitest) |
| Offline | `e2e/offline.spec.ts` |

Performance tests time the real engine with `time.perf_counter()`, without
mocks, against the 2 s acceptance target.

## 10. Known limitations

- **Map tiles and fonts need internet.** OSM tiles and Google Fonts are the
  only external dependencies, verified by `e2e/offline.spec.ts`.
  Planning, CBBA, validation and the state machine run fully offline.
  An offline tile cache (PRD) is not implemented yet.
- **CBBA runs centrally.** It runs on the edge server and simulates the
  decentralised message exchange (fully connected by default). Companion
  computers do not run it yet; that needs the NATS/MAVSDK stack from
  Sprint 4+.
- **Energy is an estimate.** The model is linear (endurance-based drain);
  Wh uses a nominal pack capacity per airframe type. There is no wind or
  payload-mass model.
- **No real flight is commanded.** ARMED/ACTIVE are lifecycle states only.
  Arming and launching real hardware (MAVLink) is Sprint 5–6 scope.
- **NFZ checks ignore altitude.** NFZs block regardless of the zone's
  altitude band (conservative). Inclusion ceilings are checked against the
  planned altitude.
- **AOI shape support is limited.** AOIs and geofences use the outer ring
  only (no holes), and KML/GeoJSON import keeps the outer ring.
- **Telemetry is not org-scoped.** It is still broadcast to every connected
  client, as in Sprint 2. Mission events are org-scoped.
- **Auth is unchanged from Sprint 2.** It uses HS256 tokens in localStorage
  with no refresh endpoint; the PRD's RS256 + httpOnly cookies are
  unchanged.
