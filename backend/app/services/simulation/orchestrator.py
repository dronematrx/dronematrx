"""Sprint 5 simulation orchestrator.

    SimulationService (request scope, sync)      SimulationOrchestrator (app scope, async)
      stage fleet, create session,                 start: arm mission -> simhost (Gazebo + PX4)
      generate world, preflight, read models         -> upload CBBA routes -> ACTIVE -> fly
                                                   ingest NATS telemetry/alerts (same `ui` consumer
                                                     the rest of the app uses) -> progress, tracks
                                                   failure responses (matrix below), controls
                                                   finish -> mission COMPLETED/ABORTED -> report

Failure-response matrix (docs/sprint5/SIMULATION.md, section "Failure responses"):

    event                 detection                              response                                   outcome
    -------------------   ------------------------------------   ----------------------------------------   ---------
    SITL crash / kill     simhost process monitor (exit != 0)    drone FAILED -> unavailable; its unfinished replanned
                                                                 tasks re-allocated by CBBA (warm start)    (aborted if
                                                                 to survivors; new routes uploaded           no survivor)
    link degraded         operator injection                     telemetry/commands delayed / dropped;       continued
                                                                 status WARNING
    link lost             no telemetry for SIM_COMM_LOSS_S        drone COMM_LOST; PX4 runs NAV_DLL_ACT       continued
                                                                 (link-loss policy) on its own
    left mission          RTL/LAND with unfinished tasks          tasks re-allocated to the remaining fleet   replanned
    low battery           telemetry battery <= RTH reserve        RTL commanded + tasks re-allocated          replanned
    GPS drift             PX4 GPS 'wrong' failure injection       true (Gazebo) vs reported error tracked     continued
    obstacle              operator click -> Gazebo entity         existence + location verified; collision    continued
                                                                 detection only (no avoidance in PX4 mission)
    wind gust             operator                               force applied; cross-track deviation        continued
                                                                 measured
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point, Polygon, shape
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.geo import geography_to_geojson
from app.core.logging import log_event
from app.models import Drone, Mission, Organization, User
from app.models.mission_planning import MissionPlan, MissionTask
from app.models.simulation import TERMINAL_SESSION_STATES, SimulationDroneInstance, SimulationEvent, SimulationSession
from app.models.task_bundle import TaskBundle
from app.services.coordination import Agent, AgentPlan, AllocationParams, AllocationProblem, AllocationResult, PathStep, Task, get_algorithm
from app.services.coordination.geometry import LocalProjection
from app.services.coordination.task_generation import coverage_fraction
from app.services.mission_planning_service import AREA_TASK_TYPES, MissionPlanningService, PlanningError
from app.services.mission_state_service import MissionStateService
from app.services.simulation.adapters import AdapterError, SimulationAdapter
from app.services.simulation.dem import FlatSource, TerrariumSource
from app.services.simulation.fleet import LaunchAreaError, drone_lonlat, ensure_sim_fleet, launch_positions, min_separation_m, stage_drones
from app.services.simulation.geo import EnuFrame
from app.services.simulation.routes import BuiltMission, TaskGeometry, build_mission, task_geometries_from_rows
from app.services.simulation.simhost import SimhostClient, SimhostError
from app.services.simulation.world import SpawnRequest, WorldGenerationError, WorldGenerator, cleanup_workspace, validate_aoi
from app.services.spatial_validation import SpatialValidator

logger = logging.getLogger("dronematrx.sim")
settings = get_settings()

TASK_SENSORS = {"survey": ["camera"], "mapping": ["camera"], "search": ["thermal"], "inspection": ["camera"],
                "agriculture": ["multispectral"], "custom": ["camera"]}
SPEEDS = (1.0, 2.0, 5.0, 10.0)
TRACK_MIN_DT_S = 1.0  # simulated seconds between stored track points
GPS_ERROR_ALERT_M = 5.0
EVENT_TYPES = (
    "session_created", "world_generated", "preflight", "starting", "sitl_ready", "mission_uploaded", "running",
    "paused", "resumed", "speed_changed", "guardrail", "failure_injected", "process_crashed", "drone_unavailable",
    "comm_degraded", "comm_lost", "comm_restored", "battery_low", "rth_commanded", "gps_drift", "gps_error",
    "obstacle_spawned", "collision", "wind_gust", "wind_effect", "tasks_reallocated", "task_completed",
    "drone_left_mission", "drone_mission_complete", "mission_completed", "aborted", "stopped", "failed", "report_generated",
    "statustext", "mode_changed", "simulator_failed", "model_removed",
)


class SimulationError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def workspace_root() -> Path:
    p = Path(settings.SIM_WORKSPACE_DIR)
    return p if p.is_absolute() else Path(__file__).resolve().parents[3] / p


def dem_cache_dir() -> Path:
    p = Path(settings.SIM_DEM_CACHE_DIR)
    return p if p.is_absolute() else Path(__file__).resolve().parents[3] / p


# =====================================================================
# Request-scoped service
# =====================================================================


class SimulationService:
    def __init__(self, db: Session, client: SimhostClient | None = None):
        self.db = db
        self.client = client or SimhostClient()

    # ---------------------------------------------------------- capacity

    def capacity(self) -> dict:
        try:
            h = self.client.health()
            cap = h.get("capacity", {})
            return {"available": True, "max_instances": cap.get("max_instances"), "running_instances": cap.get("running_instances", 0),
                    "versions": h.get("versions"), "nats_connected": h.get("nats", {}).get("connected"), "host": cap}
        except SimhostError as exc:
            return {"available": False, "max_instances": None, "error": str(exc)}

    def fleet(self, org: Organization) -> dict:
        drones = ensure_sim_fleet(self.db, org)
        return {"drones": drones, "capacity": self.capacity()}

    # ------------------------------------------------------------ staging

    def stage_fleet(self, mission: Mission, user: User, drone_count: int, ip: str | None = None) -> dict:
        """Pick `drone_count` simulation drones, place them on AOI launch pads
        and save wizard step 4. Never silently stages fewer than requested."""
        self._require_sim_mission(mission)
        if mission.aoi_geometry is None:
            raise SimulationError("AOI_MISSING", "Save the AOI before choosing drones")
        org = self.db.get(Organization, mission.organization_id)
        fleet = ensure_sim_fleet(self.db, org)
        cap = self.capacity()
        limit = min(len(fleet), cap["max_instances"]) if cap.get("max_instances") else len(fleet)
        if drone_count < 1:
            raise SimulationError("DRONE_COUNT", "Select at least one drone")
        if drone_count > limit:
            raise SimulationError(
                "FLEET_TOO_LARGE",
                f"{drone_count} drones requested but this simulation host supports {limit}"
                + ("" if cap.get("available") else " (simulation host unreachable)"),
                409,
            )
        aoi = geography_to_geojson(mission.aoi_geometry)
        validator = SpatialValidator(self.db, mission.organization_id)
        blocked = self._blocked_polygons(validator, aoi)
        try:
            positions = launch_positions(aoi, drone_count, blocked)
        except LaunchAreaError as exc:
            raise SimulationError("LAUNCH_AREA", str(exc)) from exc
        chosen = fleet[:drone_count]
        # Unstage the rest so the planner never sees stale pads.
        for d in fleet[drone_count:]:
            if d.flight_mode == "SIM_STAGED":
                d.flight_mode = "STANDBY"
        stage_drones(self.db, chosen, positions)
        sensors = TASK_SENSORS.get(mission.template or "survey", ["camera"])
        planning = MissionPlanningService(self.db)
        if 3 not in planning.completed_steps(mission):
            # The Simulation page picks drones before flight parameters; the
            # wizard requires step 3 first, so save the template's defaults
            # (the operator's values replace them when the plan is generated).
            planning.save_step(mission, 3, (mission.config or {}).get("flight") or {}, user, ip)
        state = planning.save_step(mission, 4, {"required_sensors": sensors, "drone_ids": [str(d.id) for d in chosen]}, user, ip)
        return {"wizard": state, "drones": chosen, "positions": positions, "min_separation_m": round(min_separation_m(positions), 2)}

    def _blocked_polygons(self, validator: SpatialValidator, aoi: dict) -> list[Polygon]:
        from app.models.geofence import Geofence

        frame = LocalProjection.for_polygon(aoi)
        zones = self.db.execute(select(Geofence).where(Geofence.organization_id == validator.organization_id, Geofence.type.in_(("nfz", "exclusion")))).scalars()
        out = []
        for z in zones:
            gj = geography_to_geojson(z.geometry)
            if gj and gj.get("type") == "Polygon":
                out.append(Polygon([frame.to_local(lon, lat) for lon, lat in gj["coordinates"][0]]))
        # fleet.launch_positions works in the AOI-centroid ENU frame; the
        # equirectangular planning frame agrees to < 0.1 % at these extents.
        return out

    @staticmethod
    def _require_sim_mission(mission: Mission) -> None:
        if mission.execution_mode != "simulation":
            raise SimulationError("NOT_SIMULATION", "Only simulation missions can be simulated; create a simulation mission", 409)

    # ----------------------------------------------------------- session

    def create_session(self, mission: Mission, user: User, config: dict) -> SimulationSession:
        self._require_sim_mission(mission)
        active = self.db.execute(
            select(SimulationSession).where(SimulationSession.mission_id == mission.id, SimulationSession.state.notin_(TERMINAL_SESSION_STATES))
        ).scalar_one_or_none()
        if active is not None:
            raise SimulationError("SESSION_EXISTS", "This mission already has an active simulation session", 409)
        planning = MissionPlanningService(self.db)
        plan = planning.current_plan(mission)
        if plan is None:
            raise SimulationError("CBBA_NOT_RUN", "Run the CBBA allocation before creating a simulation")
        drone_ids = (mission.config or {}).get("sensors", {}).get("drone_ids", [])
        drones = self.db.execute(select(Drone).where(Drone.id.in_([uuid.UUID(d) for d in drone_ids]))).scalars().all()
        if not drones or not all(d.simulated for d in drones):
            raise SimulationError("NOT_SIM_DRONES", "Simulation missions must use simulation drones only")
        speed = float(config.get("speed", 1.0))
        if speed not in SPEEDS:
            raise SimulationError("SPEED", f"Speed must be one of {SPEEDS}")
        sess = SimulationSession(
            organization_id=mission.organization_id,
            mission_id=mission.id,
            plan_id=plan.id,
            state="created",
            task_type=mission.template or "custom",
            requested_drones=len(drones),
            speed=speed,
            config={
                "speed": speed,
                "dem_source": config.get("dem_source", settings.SIM_DEM_SOURCE),
                "allow_fallback": bool(config.get("allow_fallback", settings.SIM_ALLOW_FLAT_FALLBACK)),
                "link_loss_policy": (mission.config or {}).get("constraints", {}).get("link_loss_policy", "RTH"),
                "auto_reallocate": bool(config.get("auto_reallocate", True)),
                "seed": int(config.get("seed", 42)),
                "flight": (mission.config or {}).get("flight", {}),
                "constraints": (mission.config or {}).get("constraints", {}),
                "aoi": geography_to_geojson(mission.aoi_geometry),
            },
            created_by=user.id,
        )
        self.db.add(sess)
        self.db.flush()
        bundles = {b.drone_id: b for b in self.db.execute(select(TaskBundle).where(TaskBundle.plan_id == plan.id)).scalars()}
        for idx, d in enumerate(sorted(drones, key=lambda d: d.name)):
            lonlat = drone_lonlat(d)
            b = bundles.get(d.id)
            self.db.add(SimulationDroneInstance(
                session_id=sess.id, drone_id=d.id, instance_index=idx, uin=d.uin, state="pending",
                spawn={"longitude": lonlat[0], "latitude": lonlat[1]} if lonlat else {},
                bundle=list(b.path) if b else [],
            ))
        self.db.commit()
        self.db.refresh(sess)
        record_event(self.db, sess, "session_created", "operator", response=f"{len(drones)} drones, {sess.task_type}",
                     details={"requested_drones": len(drones), "speed": speed})
        return sess

    def generate_world(self, sess: SimulationSession) -> dict:
        if sess.state not in ("created", "world_ready", "failed"):
            raise SimulationError("STATE", f"World cannot be (re)generated in state {sess.state}", 409)
        cfg = sess.config
        sess.state = "world_generating"
        self.db.commit()
        cleanup_workspace(workspace_root(), str(sess.id))
        source = FlatSource(0.0, reason="requested") if cfg.get("dem_source") == "flat" else TerrariumSource(dem_cache_dir())
        spawns = []
        for inst in sess.instances:
            drone = self.db.get(Drone, inst.drone_id)
            spawns.append(SpawnRequest(inst.uin, drone.name, inst.spawn["latitude"], inst.spawn["longitude"], 0.0))
        gen = WorldGenerator(workspace_root(), uri_root=settings.SIM_WORKSPACE_URI)
        try:
            result = gen.generate(str(sess.id), str(sess.mission_id), cfg["aoi"], spawns, source, allow_fallback=cfg.get("allow_fallback", True), config={"speed": sess.speed})
        except (WorldGenerationError, Exception) as exc:
            sess.state = "failed"
            sess.error = {"stage": "world", "message": str(exc)}
            self.db.commit()
            record_event(self.db, sess, "failed", "orchestrator", response=f"World generation failed: {exc}")
            raise SimulationError("WORLD_GENERATION", str(exc)) from exc
        meta = result.metadata
        by_uin = {s.uin: s for s in result.spawns}
        for inst in sess.instances:
            s = by_uin[inst.uin]
            inst.spawn = {**inst.spawn, "ground_elevation_m": s.ground_elevation_m, "x": s.x, "y": s.y, "z": s.z, "yaw": s.yaw_rad, "pose": s.pose_string()}
        sess.world = {k: meta[k] for k in ("world_name", "frame", "aoi_area_km2", "extent_m", "terrain", "timings_s", "gazebo")}
        sess.world["sdf_uri"] = f"{settings.SIM_WORKSPACE_URI}/{sess.id}/world.sdf"
        sess.state = "world_ready"
        self.db.commit()
        record_event(self.db, sess, "world_generated", "orchestrator",
                     response=f"{meta['terrain']['source']} terrain {meta['terrain']['heightmap_px'][0]} px, {meta['timings_s']['total_s']:.2f} s",
                     details={"terrain": meta["terrain"], "timings_s": meta["timings_s"], "aoi_area_km2": meta["aoi_area_km2"]})
        return sess.world

    def preflight(self, sess: SimulationSession) -> dict:
        mission = self.db.get(Mission, sess.mission_id)
        planning = MissionPlanningService(self.db)
        plan = planning.current_plan(mission)
        checks: list[dict] = []

        def check(code: str, ok: bool, message: str, **details) -> None:
            checks.append({"code": code, "passed": bool(ok), "message": message, "details": details})

        aoi = geography_to_geojson(mission.aoi_geometry)
        try:
            validate_aoi(aoi)
            check("AOI_VALID", True, "AOI polygon is valid")
        except WorldGenerationError as exc:
            check("AOI_VALID", False, str(exc))
        check("TASKS_GENERATED", plan is not None and plan.task_count > 0, f"{plan.task_count if plan else 0} tasks generated")
        check("CBBA_ALLOCATION", bool(plan and plan.converged and plan.assigned_count > 0 and not planning.is_stale(mission, plan)),
              f"CBBA {'converged' if plan and plan.converged else 'not converged'}: {plan.assigned_count if plan else 0}/{plan.task_count if plan else 0} assigned"
              + (" (stale: re-run CBBA)" if plan and planning.is_stale(mission, plan) else ""))
        cap = SimulationService(self.db, self.client).capacity()
        check("SIMHOST", cap.get("available", False), "Simulation host reachable" if cap.get("available") else f"Simulation host unavailable: {cap.get('error')}")
        versions = cap.get("versions") or {}
        check("GAZEBO", bool(versions.get("gazebo") and versions["gazebo"] != "unknown"), f"Gazebo: {versions.get('gazebo', 'not available')}")
        check("SITL", bool(versions.get("px4") and versions["px4"] != "unknown"), f"PX4 SITL: {versions.get('px4', 'not available')}")
        free = (cap.get("max_instances") or 0) - (cap.get("running_instances") or 0)
        check("DRONE_COUNT", cap.get("available", False) and sess.requested_drones <= free,
              f"{sess.requested_drones} drones requested, {free} SITL slots free (limit {cap.get('max_instances')})")
        check("PORTS", cap.get("available", False) and free >= sess.requested_drones, "MAVLink ports 14540+i available" if free >= sess.requested_drones else "Not enough free MAVLink ports")
        check("NATS", bool(cap.get("nats_connected")), "Simulator connected to the NATS telemetry bus" if cap.get("nats_connected") else "Simulator not connected to NATS")
        runtime_ok = SimulationOrchestrator.instance is not None and SimulationOrchestrator.instance.telemetry_ready()
        check("TELEMETRY_PIPELINE", runtime_ok, "Backend consumes simulator telemetry over NATS" if runtime_ok else "Backend telemetry runtime/NATS not running")
        world_ok = bool(sess.world) and (workspace_root() / str(sess.id) / "world.sdf").exists()
        check("WORLD_GENERATED", world_ok, f"World {sess.world.get('world_name')} ready" if world_ok else "Generate the simulation world first")
        spawn_pts = [(i.spawn.get("longitude"), i.spawn.get("latitude")) for i in sess.instances if i.spawn.get("longitude") is not None]
        poly = shape(aoi)
        inside = all(poly.contains(Point(p)) for p in spawn_pts)
        validator = SpatialValidator(self.db, mission.organization_id)
        nfz = [p for p in spawn_pts if validator.covering({"type": "Point", "coordinates": list(p)}, ["nfz"])]
        sep = min_separation_m(spawn_pts) if len(spawn_pts) > 1 else math.inf
        check("SPAWN_POSITIONS", len(spawn_pts) == sess.requested_drones and inside and not nfz and sep >= 2.0,
              f"{len(spawn_pts)} spawn poses inside AOI, min separation {sep if sep != math.inf else 0:.1f} m, {len(nfz)} in NFZ")
        passed = all(c["passed"] for c in checks)
        sess.progress = {**(sess.progress or {}), "preflight": {"passed": passed, "checks": checks, "at": _now().isoformat()}}
        self.db.commit()
        record_event(self.db, sess, "preflight", "orchestrator", response="passed" if passed else "failed",
                     details={"failed": [c["code"] for c in checks if not c["passed"]]})
        return {"passed": passed, "checks": checks}


# =====================================================================
# Events
# =====================================================================


def record_event(db: Session, sess: SimulationSession, event_type: str, source: str, *, drone_id=None, previous_state=None,
                 new_state=None, response=None, outcome=None, details=None, sim_time_s=None) -> SimulationEvent:
    assert event_type in EVENT_TYPES, event_type
    ev = SimulationEvent(session_id=sess.id, event_type=event_type, source=source, drone_id=drone_id, previous_state=previous_state,
                         new_state=new_state, response=response, mission_outcome=outcome, details=details or {},
                         sim_time_s=sim_time_s if sim_time_s is not None else sess.sim_time_s, created_at=_now())
    db.add(ev)
    db.commit()
    log_event(logger, f"sim.{event_type}", session_id=sess.id, drone_id=drone_id, response=response)
    orch = SimulationOrchestrator.instance
    if orch is not None:
        orch.broadcast_soon({"type": "simulation_event", "data": event_out(ev)}, str(sess.organization_id))
    return ev


def event_out(ev: SimulationEvent) -> dict:
    return {
        "id": str(ev.id), "session_id": str(ev.session_id), "created_at": ev.created_at.isoformat() if ev.created_at else None,
        "sim_time_s": ev.sim_time_s, "event_type": ev.event_type, "source": ev.source, "drone_id": str(ev.drone_id) if ev.drone_id else None,
        "previous_state": ev.previous_state, "new_state": ev.new_state, "response": ev.response, "mission_outcome": ev.mission_outcome,
        "details": ev.details or {},
    }


# =====================================================================
# Runtime state
# =====================================================================


@dataclass
class DroneRun:
    instance_id: uuid.UUID
    drone_id: str
    uin: str
    name: str
    index: int
    start: tuple[float, float]  # lon, lat
    state: str = "starting"
    path: list[tuple[str, int]] = field(default_factory=list)  # remaining CBBA path (task key, orientation)
    seq_map: dict[int, dict] = field(default_factory=dict)
    mission_count: int = 0
    # Generation of the mission currently on the vehicle (simhost bumps it on
    # every accepted upload). None while a replacement upload is in flight:
    # progress frames are only applied when their generation matches.
    mission_gen: int | None = None
    planned_route: list[list[float]] = field(default_factory=list)
    reached: set[int] = field(default_factory=set)
    battery_start: float | None = None
    battery: float | None = None
    # PX4's load-compensated estimate rebounds once the drone lands and is
    # unloaded; the end-of-flight value is the last sample taken while armed.
    battery_last_armed: float | None = None
    lat: float | None = None
    lon: float | None = None
    alt: float = 0.0
    mode: str = ""
    armed: bool = False
    armed_once: bool = False
    landed_state: int = 0
    distance_m: float = 0.0
    last_xy: tuple[float, float] | None = None
    track: list[list[float]] = field(default_factory=list)  # [sim_t, lat, lon, alt, truth_lat, truth_lon]
    last_track_t: float = -1e9
    last_rx: float = 0.0
    comm_lost: bool = False
    failed: bool = False
    returning: bool = False  # left the mission (RTL/LAND, battery, link-loss failsafe)
    done: bool = False
    rtl_by_policy: bool = False
    gps_err_m: float | None = None
    gps_err_max_m: float = 0.0
    gps_alerted: bool = False
    collided_with: set[str] = field(default_factory=set)
    waypoints_reached: int = 0
    tail: list[list[float]] = field(default_factory=list)  # track points since the last UI broadcast


@dataclass
class ActiveRun:
    session_id: uuid.UUID
    org_id: str
    mission_id: uuid.UUID
    drones: dict[str, DroneRun]
    tasks: dict[str, dict]  # key -> {status, drone (uin), type}
    geoms: dict[str, TaskGeometry]
    task_rows: dict[str, Any]
    flight: dict
    constraints: dict
    projection: LocalProjection
    aoi_local: Polygon
    swath_m: float
    frame: EnuFrame | None
    state: str = "starting"
    sim_time_s: float = 0.0
    speed: float = 1.0
    paused: bool = False
    guardrail: dict = field(default_factory=dict)
    obstacles: list[dict] = field(default_factory=list)
    gusts: list[dict] = field(default_factory=list)
    started_wall: float = field(default_factory=time.time)
    finishing: bool = False
    last_persist: float = 0.0
    auto_reallocate: bool = True
    seed: int = 42


class SimulationOrchestrator:
    instance: "SimulationOrchestrator | None" = None

    def __init__(self, session_factory: sessionmaker, connections, runtime=None, client: SimhostClient | None = None):
        self.session_factory = session_factory
        self.connections = connections
        self.runtime = runtime
        self.client = client or SimhostClient()
        self.runs: dict[uuid.UUID, ActiveRun] = {}
        self.by_uin: dict[str, uuid.UUID] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task] = []
        self._bg: set[asyncio.Task] = set()
        SimulationOrchestrator.instance = self

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self.runtime is not None:
            self.runtime.message_listeners.append(self.on_bus_message)
        self._tasks.append(asyncio.create_task(self._monitor_loop()))
        if self.runtime is not None:  # only a live backend can have left sessions running
            await asyncio.to_thread(self._recover_orphaned_sessions)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for sid in list(self.runs):
            try:
                await asyncio.to_thread(self.client.stop, str(sid))
            except SimhostError:
                pass

    def telemetry_ready(self) -> bool:
        return self.runtime is not None and bool(getattr(self.runtime, "nats_ok", False))

    def _recover_orphaned_sessions(self) -> None:
        """Sessions left 'running' by a backend restart can't be resumed: mark them failed."""
        with self.session_factory() as db:
            for sess in db.execute(select(SimulationSession).where(SimulationSession.state.in_(("starting", "running", "paused")))).scalars():
                sess.state = "failed"
                sess.error = {"stage": "backend_restart", "message": "Backend restarted while the simulation was running"}
                sess.ended_at = _now()
                db.commit()
                try:
                    self.client.stop(str(sess.id))
                except SimhostError:
                    pass

    def broadcast_soon(self, payload: dict, org_id: str | None) -> None:
        loop = self._loop
        if loop is None or loop.is_closed() or not loop.is_running():
            return
        coro = self.connections.broadcast_json(payload, organization_id=org_id)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            t = loop.create_task(coro)
            self._bg.add(t)
            t.add_done_callback(self._bg.discard)
        else:
            asyncio.run_coroutine_threadsafe(coro, loop)

    def _spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    # -------------------------------------------------------------- start

    async def launch(self, session_id: uuid.UUID, user_id: uuid.UUID) -> None:
        with self.session_factory() as db:
            sess = db.get(SimulationSession, session_id)
            if sess is None or sess.state != "world_ready":
                raise SimulationError("STATE", f"Session must be world_ready to start (is {sess.state if sess else 'missing'})", 409)
            pre = (sess.progress or {}).get("preflight") or {}
            if not pre.get("passed"):
                raise SimulationError("PREFLIGHT", "Run the preflight checklist; every check must pass", 409)
            sess.state = "starting"
            db.commit()
            record_event(db, sess, "starting", "operator", response="Launching Gazebo world and PX4 SITL fleet")
        self._spawn(self._launch(session_id, user_id))

    async def _launch(self, session_id: uuid.UUID, user_id: uuid.UUID) -> None:
        try:
            await asyncio.to_thread(self._launch_sync, session_id, user_id)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a failed session
            logger.exception("simulation launch failed")
            await asyncio.to_thread(self._fail, session_id, f"{type(exc).__name__}: {exc}")

    def _launch_sync(self, session_id: uuid.UUID, user_id: uuid.UUID) -> None:
        with self.session_factory() as db:
            sess = db.get(SimulationSession, session_id)
            mission = db.get(Mission, sess.mission_id)
            user = db.get(User, user_id)
            # 1. VALIDATING -> ARMED (re-runs every pre-arm check incl. PostGIS NFZ).
            if mission.status == "planning":
                MissionPlanningService(db).validate(mission, user)
            MissionStateService(db).transition(mission, "armed", user, f"Simulation session {sess.id} launching")
            # 2. Gazebo world + PX4 SITL fleet.
            body = {
                "session_id": str(sess.id), "world_sdf": sess.world["sdf_uri"], "world_name": sess.world["world_name"],
                "origin": sess.world["frame"]["origin"], "speed": 1.0, "link_loss_policy": sess.config.get("link_loss_policy", "RTH"),
                "seed": sess.config.get("seed", 42), "cruise_speed_mps": float(sess.config["flight"].get("speed_mps", 8)),
                "drones": [{"uin": i.uin, "drone_id": str(i.drone_id), "name": db.get(Drone, i.drone_id).name, "pose": i.spawn["pose"]} for i in sess.instances],
            }
            self._register_run(db, sess, mission)
            started = time.perf_counter()
            status = self.client.create_session(body)
            startup_s = time.perf_counter() - started
            sess.environment = {"versions": self.client.health().get("versions"), "startup_s": round(startup_s, 2)}
            by_uin = {i["uin"]: i for i in status.get("instances", [])}
            spawned = 0
            for inst in sess.instances:
                info = by_uin.get(inst.uin)
                if info:
                    inst.sitl_pid, inst.mavlink_port, inst.gz_model_name = info["pid"], info["api_port"], info["gz_model_name"]
                    inst.state = "ready" if info["state"] == "running" else inst.state
                    inst.started_at = _now()
                    spawned += 1
            sess.spawned_drones = spawned
            db.commit()
            if spawned != sess.requested_drones:
                raise SimulationError("FLEET_MISMATCH", f"{spawned} of {sess.requested_drones} drones spawned")
            record_event(db, sess, "sitl_ready", "simhost", response=f"{spawned} PX4 SITL instances ready in {startup_s:.1f} s",
                         details={"instances": status.get("instances"), "versions": sess.environment["versions"]})
            # 3. Upload CBBA routes through the flight-controller adapter.
            run = self.runs[sess.id]
            for inst in sess.instances:
                dr = run.drones[inst.uin]
                built = self._build(run, dr, dr.path, airborne=False)
                if built is None:
                    continue
                drone = db.get(Drone, inst.drone_id)
                res = SimulationAdapter(self.client, str(sess.id), mission, drone).upload_mission(built.items)
                dr.mission_gen = (res.get(inst.uin) or {}).get("gen")
                inst.route = built.as_dict()
            db.commit()
            record_event(db, sess, "mission_uploaded", "orchestrator", response=f"Routes uploaded to {sum(1 for d in run.drones.values() if d.path)} drones",
                         details={uin: len(d.path) for uin, d in run.drones.items()})
            # 4. ARMED -> ACTIVE needs fresh telemetry from every assigned drone (it arrives over NATS).
            deadline = time.time() + 30
            while time.time() < deadline:
                db.expire_all()
                checks = MissionPlanningService(db).readiness_checks(mission)
                if not [c for c in checks if c["severity"] == "error"]:
                    break
                time.sleep(1.0)
            MissionStateService(db).transition(mission, "active", user, f"Simulation session {sess.id} running")
            # 5. Fly.
            for inst in sess.instances:
                dr = run.drones[inst.uin]
                if not dr.path:
                    dr.done, dr.state = True, "landed"
                    continue
                SimulationAdapter(self.client, str(sess.id), mission, db.get(Drone, inst.drone_id)).start_mission()
                dr.state = "flying"
            if sess.speed != 1.0:
                self.client.speed(str(sess.id), sess.speed)
            sess.state = "running"
            sess.started_at = _now()
            db.commit()
            run.state = "running"
            run.speed = sess.speed
            record_event(db, sess, "running", "orchestrator", response="Mission started on all assigned drones", new_state="running")

    def _register_run(self, db: Session, sess: SimulationSession, mission: Mission) -> None:
        plan = db.get(MissionPlan, sess.plan_id)
        rows = db.execute(select(MissionTask).where(MissionTask.mission_id == mission.id)).scalars().all()
        geoms = task_geometries_from_rows(rows)
        aoi = geography_to_geojson(mission.aoi_geometry)
        projection = LocalProjection.for_polygon(aoi)
        agent_state = (plan.diagnostics or {}).get("agent_state", {})
        drones: dict[str, DroneRun] = {}
        tasks = {t.task_key: {"status": "unassigned" if t.status == "unassigned" else "assigned", "drone": None, "type": t.task_type,
                              "capability": t.required_capability} for t in rows}
        for inst in sess.instances:
            drone = db.get(Drone, inst.drone_id)
            entry = agent_state.get(str(inst.drone_id), {})
            path = [(k, int(o)) for k, o, _bid in entry.get("path", [])]
            for k, _ in path:
                tasks[k]["drone"] = inst.uin
            drones[inst.uin] = DroneRun(inst.id, str(inst.drone_id), inst.uin, drone.name, inst.instance_index,
                                        (inst.spawn["longitude"], inst.spawn["latitude"]), path=path)
        passes = ((plan.diagnostics or {}).get("coverage") or {}).get("passes") or []
        frame_o = (sess.world or {}).get("frame", {}).get("origin")
        run = ActiveRun(
            session_id=sess.id, org_id=str(sess.organization_id), mission_id=mission.id, drones=drones, tasks=tasks, geoms=geoms,
            task_rows={t.task_key: t.id for t in rows}, flight=sess.config.get("flight", {}), constraints=sess.config.get("constraints", {}),
            projection=projection, aoi_local=projection.polygon_to_local(aoi), swath_m=float(passes[0]["swath_m"]) if passes else 30.0,
            frame=EnuFrame(frame_o["latitude"], frame_o["longitude"], frame_o["height_m"]) if frame_o else None,
            speed=sess.speed, auto_reallocate=sess.config.get("auto_reallocate", True), seed=sess.config.get("seed", 42),
        )
        self.runs[sess.id] = run
        for uin in drones:
            self.by_uin[uin] = sess.id

    def _build(self, run: ActiveRun, dr: DroneRun, path: list[tuple[str, int]], airborne: bool) -> BuiltMission | None:
        if not path:
            return None
        start = (dr.lon, dr.lat) if (airborne and dr.lon is not None) else dr.start
        built = build_mission(start, path, run.geoms, float(run.flight.get("altitude_m", 40)), float(run.flight.get("speed_mps", 8)),
                              rth_on_complete=bool(run.flight.get("rth_on_complete", True)),
                              stop_and_turn=run.flight.get("turn_behaviour") == "stop_and_turn", airborne=airborne)
        dr.seq_map, dr.mission_count, dr.reached = built.seq_map, len(built.items), set()
        dr.planned_route = built.planned_route
        dr.mission_gen = None  # until the upload is acknowledged
        return built

    def _fail(self, session_id: uuid.UUID, message: str) -> None:
        with self.session_factory() as db:
            sess = db.get(SimulationSession, session_id)
            if sess is None:
                return
            sess.state = "failed"
            sess.error = {"stage": "launch", "message": message}
            sess.ended_at = _now()
            db.commit()
            record_event(db, sess, "failed", "orchestrator", response=message)
            mission = db.get(Mission, sess.mission_id)
            try:
                if mission.status == "armed":
                    MissionStateService(db).transition(mission, "validating", None, "Simulation launch failed; disarmed")
                elif mission.status == "active":
                    MissionStateService(db).transition(mission, "aborted", None, "Simulation failed")
            except Exception:  # pragma: no cover
                logger.exception("mission state rollback failed")
            try:
                self.client.stop(str(session_id))
            except SimhostError:
                pass
            self._generate_report(db, sess)
        self._forget(session_id)

    def _forget(self, session_id: uuid.UUID) -> None:
        run = self.runs.pop(session_id, None)
        if run:
            for uin in run.drones:
                if self.by_uin.get(uin) == session_id:
                    del self.by_uin[uin]

    # ---------------------------------------------------------- ingestion

    async def on_bus_message(self, msg) -> None:
        """FleetRuntime `ui` consumer hook: every drone telemetry/alert message."""
        sid = self.by_uin.get(msg.uin)
        if sid is None or msg.kind not in ("telemetry", "alert"):
            return
        run = self.runs.get(sid)
        if run is None or str(msg.payload.get("session_id")) != str(sid):
            return
        dr = run.drones.get(msg.uin)
        if dr is None:
            return
        if msg.kind == "telemetry":
            await self._on_telemetry(run, dr, msg.payload)
        else:
            await self._on_alert(run, dr, msg.payload)

    async def _on_telemetry(self, run: ActiveRun, dr: DroneRun, p: dict) -> None:
        dr.last_rx = time.time()
        if dr.comm_lost:
            dr.comm_lost = False
            await self._event(run, "comm_restored", "simhost", dr, "comm_lost", dr.state, "Telemetry received again", "continued")
        lat, lon = p.get("latitude"), p.get("longitude")
        if lat is None or lon is None:
            return
        st = float(p.get("sim_time_s") or run.sim_time_s)
        run.sim_time_s = max(run.sim_time_s, st)
        dr.lat, dr.lon, dr.alt = lat, lon, float(p.get("altitude") or 0)
        dr.battery = float(p.get("battery", 100))
        if dr.battery_start is None:
            dr.battery_start = dr.battery
        dr.mode, dr.armed, dr.landed_state = p.get("flight_mode", ""), bool(p.get("armed")), int(p.get("landed_state") or 0)
        dr.armed_once = dr.armed_once or dr.armed
        if dr.armed:
            dr.battery_last_armed = dr.battery
        xy = run.projection.to_local(lon, lat)
        if dr.last_xy is not None and dr.armed:
            dr.distance_m += math.dist(xy, dr.last_xy)
        dr.last_xy = xy
        tlat, tlon = p.get("truth_lat"), p.get("truth_lon")
        if tlat is not None and tlon is not None:
            dr.gps_err_m = round(math.dist(xy, run.projection.to_local(tlon, tlat)), 2)
            dr.gps_err_max_m = max(dr.gps_err_max_m, dr.gps_err_m)
        if st - dr.last_track_t >= TRACK_MIN_DT_S:
            point = [round(st, 2), lat, lon, round(dr.alt, 1), tlat, tlon]
            dr.track.append(point)
            dr.tail.append(point)
            dr.last_track_t = st
        # PX4 executes items in order: reaching item k implies every earlier
        # item was reached. This also recovers progress after a link outage.
        reached_max = int(p.get("reached_max", -1))
        if reached_max >= 0 and self._current_gen(dr, p):
            for seq in range(0, reached_max + 1):
                if seq not in dr.reached and seq in dr.seq_map:
                    await self._reached(run, dr, seq)
        await self._check_conditions(run, dr, p)

    @staticmethod
    def _current_gen(dr: DroneRun, p: dict) -> bool:
        """Progress belongs to the mission currently on the vehicle?"""
        if dr.mission_gen is None:
            return False  # replacement upload in flight
        gen = p.get("mission_gen")
        return gen is None or int(gen) == dr.mission_gen

    async def _reached(self, run: ActiveRun, dr: DroneRun, seq: int) -> None:
        dr.reached.add(seq)
        info = dr.seq_map.get(seq, {})
        if info.get("task_key"):
            dr.waypoints_reached += 1
        if info.get("role") == "exit":
            key = info["task_key"]
            t = run.tasks.get(key)
            if t and t["status"] != "completed":
                t["status"], t["drone"], t["completed_at"] = "completed", dr.uin, run.sim_time_s
                dr.path = [(k, o) for k, o in dr.path if k != key]
                await self._event(run, "task_completed", "simhost", dr, None, None, f"{key} completed by {dr.name} (waypoint {seq} reached)",
                                  None, {"task_key": key, "seq": seq})

    async def _check_conditions(self, run: ActiveRun, dr: DroneRun, p: dict) -> None:
        reserve = float(run.flight.get("rth_battery_pct", 25))
        mission_done_seq = max((s for s, i in dr.seq_map.items() if i.get("role") in ("rtl", "land")), default=None)
        remaining = [k for k, _ in dr.path if run.tasks.get(k, {}).get("status") != "completed"]
        # Low battery -> RTL + reallocate.
        if dr.armed and dr.battery is not None and dr.battery <= reserve and not dr.returning and not dr.failed:
            dr.returning = True
            await self._event(run, "battery_low", "simhost", dr, dr.state, "returning",
                              f"Battery {dr.battery:.0f}% <= {reserve:.0f}% reserve", None, {"battery": dr.battery})
            await asyncio.to_thread(self._command_rtl, run, dr)
            dr.state = "returning"
            await self._reallocate(run, dr, "battery_low")
        # Left the mission (PX4 failsafe RTL/LAND, e.g. link loss) with unfinished tasks.
        elif dr.mode in ("RTL", "LAND") and remaining and not dr.returning and not dr.failed and dr.armed_once \
                and (mission_done_seq is None or (p.get("mission_current", -1) or -1) < mission_done_seq):
            dr.returning = True
            dr.state = "returning"
            await self._event(run, "drone_left_mission", "simhost", dr, "flying", "returning",
                              f"PX4 switched to {dr.mode} with {len(remaining)} tasks unfinished", None, {"mode": dr.mode, "remaining": remaining})
            await self._reallocate(run, dr, f"left_mission_{dr.mode.lower()}")
        # GPS error between Gazebo truth and PX4-reported position.
        if dr.gps_err_m is not None and dr.gps_err_m > GPS_ERROR_ALERT_M and not dr.gps_alerted:
            dr.gps_alerted = True
            await self._event(run, "gps_error", "simhost", dr, None, None,
                              f"Reported position {dr.gps_err_m:.1f} m from Gazebo ground truth", "continued", {"error_m": dr.gps_err_m})
        # Obstacles: collision detection from ground truth (no avoidance in the PX4 mission path).
        tlat, tlon = p.get("truth_lat"), p.get("truth_lon")
        if tlat is not None and run.obstacles:
            txy = run.projection.to_local(tlon, tlat)
            for ob in run.obstacles:
                if ob["name"] in dr.collided_with:
                    continue
                half = ob["size_m"][0] / 2
                if abs(txy[0] - ob["xy"][0]) <= half and abs(txy[1] - ob["xy"][1]) <= half and float(p.get("truth_z", dr.alt)) <= ob["top_z"]:
                    dr.collided_with.add(ob["name"])
                    await self._event(run, "collision", "simhost", dr, dr.state, dr.state,
                                      f"{dr.name} entered obstacle {ob['name']} (no obstacle avoidance in the PX4 mission path)", "continued",
                                      {"obstacle": ob["name"]})
        # Drone finished: no tasks left and on the ground, disarmed.
        if not dr.done and not dr.failed and not remaining and dr.armed_once and not dr.armed and dr.landed_state in (0, 1):
            dr.done, dr.state = True, "landed"
            await self._event(run, "drone_mission_complete", "simhost", dr, "flying", "landed",
                              f"{dr.name} landed; {dr.waypoints_reached} waypoints reached", None,
                              {"battery_end": dr.battery, "distance_m": round(dr.distance_m, 1)})
        elif not dr.done and (dr.failed or (dr.returning and dr.armed_once and not dr.armed and dr.landed_state in (0, 1))):
            dr.done = True
            dr.state = "failed" if dr.failed else "landed"
        await self._maybe_finish(run)

    async def _on_alert(self, run: ActiveRun, dr: DroneRun, p: dict) -> None:
        kind = p.get("kind")
        if kind == "waypoint_reached":
            if not self._current_gen(dr, p):
                return
            seq = int(p.get("seq", -1))
            for s in range(0, seq + 1):
                if s not in dr.reached and s in dr.seq_map:
                    await self._reached(run, dr, s)
        elif kind == "process_exited" and not p.get("expected"):
            await self._on_crash(run, dr, p)
        elif kind == "model_removed":
            await self._event(run, "model_removed", "simhost", dr, dr.state, dr.state,
                              f"{dr.name} removed from Gazebo: {p.get('reason')}", None, p)
        elif kind == "guardrail":
            run.guardrail = {**run.guardrail, "last": p}
            await self._event(run, "guardrail", "simhost", None, None, None,
                              f"FPS {p.get('fps')} < 10 at {p.get('requested_speed')}x: {p.get('action')}", "continued", p)
        elif kind == "statustext":
            text = str(p.get("text", ""))
            if any(w in text.lower() for w in ("failsafe", "battery", "gps", "link", "rtl", "land", "collision")):
                await self._event(run, "statustext", "simhost", dr, None, None, f"PX4: {text}", None, {"severity": p.get("severity")})

    async def _on_crash(self, run: ActiveRun, dr: DroneRun, p: dict) -> None:
        if dr.failed:
            return
        prev = dr.state
        dr.failed, dr.done, dr.state = True, True, "failed"
        await self._event(run, "process_crashed", "simhost", dr, prev, "crashed",
                          f"PX4 SITL process exited (code {p.get('returncode')})", None, {"returncode": p.get("returncode")})
        await asyncio.to_thread(self._mark_drone_unavailable, run, dr)
        await self._event(run, "drone_unavailable", "orchestrator", dr, "crashed", "unavailable", f"{dr.name} removed from the mission", None)
        await self._reallocate(run, dr, "sitl_crash")

    def _mark_drone_unavailable(self, run: ActiveRun, dr: DroneRun) -> None:
        with self.session_factory() as db:
            drone = db.get(Drone, uuid.UUID(dr.drone_id))
            drone.status, drone.flight_mode, drone.armed = "critical", "FAILED", False
            inst = db.get(SimulationDroneInstance, dr.instance_id)
            inst.state, inst.failed_at, inst.failure_reason = "failed", _now(), "PX4 SITL process exited unexpectedly"
            db.commit()

    def _command_rtl(self, run: ActiveRun, dr: DroneRun) -> None:
        try:
            with self.session_factory() as db:
                mission = db.get(Mission, run.mission_id)
                SimulationAdapter(self.client, str(run.session_id), mission, db.get(Drone, uuid.UUID(dr.drone_id))).return_to_launch()
        except (SimhostError, AdapterError) as exc:
            logger.warning("RTL command failed for %s: %s", dr.uin, exc)

    # ------------------------------------------------------- reallocation

    async def _reallocate(self, run: ActiveRun, lost: DroneRun, reason: str) -> None:
        remaining = [(k, o) for k, o in lost.path if run.tasks.get(k, {}).get("status") != "completed"]
        lost.path = []
        if not remaining:
            await self._event(run, "tasks_reallocated", "orchestrator", lost, None, None, f"{lost.name} had no unfinished tasks", "continued",
                              {"reason": reason, "moved": {}})
            return
        survivors = [d for d in run.drones.values() if d is not lost and not d.failed and not d.returning and not d.comm_lost
                     and d.lat is not None and d.armed_once]
        if not survivors or not run.auto_reallocate:
            for k, _ in remaining:
                run.tasks[k]["status"] = "failed"
            alive = [d for d in run.drones.values() if not d.failed]
            outcome = "aborted" if not alive else "continued"
            await self._event(run, "tasks_reallocated", "orchestrator", lost, None, None,
                              f"{len(remaining)} tasks could not be re-assigned ({'no available drone' if not survivors else 'auto re-allocation off'})",
                              outcome, {"reason": reason, "failed_tasks": [k for k, _ in remaining]})
            if not alive:
                self._spawn(self.finish(run.session_id, "aborted", "All simulated drones failed"))
            return
        result = await asyncio.to_thread(self._cbba_reallocate, run, lost, survivors, remaining)
        moved: dict[str, str] = {}
        uploads: dict[str, BuiltMission] = {}
        by_id = {d.drone_id: d for d in survivors}
        for agent_id, plan in result.agent_plans.items():
            dr = by_id[agent_id]
            new_path = [(s.task_id, s.orientation) for s in plan.path]
            if new_path != dr.path:
                for k, _ in new_path:
                    if run.tasks[k]["drone"] != dr.uin:
                        moved[k] = dr.uin
                        run.tasks[k]["drone"] = dr.uin
                dr.path = new_path
                built = self._build(run, dr, new_path, airborne=True)
                if built:
                    uploads[dr.uin] = built
        for k in result.unassigned:
            run.tasks[k]["status"] = "failed"
        # The decision is recorded as soon as CBBA returns; uploading the new
        # routes (HOLD -> upload -> MISSION per drone, in parallel) follows.
        await self._event(run, "tasks_reallocated", "orchestrator", lost, None, None,
                          f"CBBA re-allocated {lost.name}'s {len(remaining)} unfinished tasks across {len(survivors)} drones "
                          f"({len(moved)} assignments changed, {len(result.unassigned)} infeasible)",
                          "replanned", {"reason": reason, "moved": moved, "unassigned": list(result.unassigned),
                                        "iterations": result.iterations, "converged": result.converged})
        outcomes = await asyncio.gather(*(asyncio.to_thread(self._upload_replan, run, uin, built) for uin, built in uploads.items()))
        ok = [u for u, good in zip(uploads, outcomes) if good]
        await self._event(run, "mission_uploaded", "orchestrator", None, None, None,
                          f"Re-planned routes active on {len(ok)}/{len(uploads)} drones",
                          "replanned" if len(ok) == len(uploads) else "continued",
                          {run.drones[u].name: len(run.drones[u].path) for u in uploads})

    def _cbba_reallocate(self, run: ActiveRun, lost: DroneRun, survivors: list[DroneRun], remaining: list[tuple[str, int]]) -> AllocationResult:
        """Warm-started CBBA (the Sprint 3 engine): survivors keep their
        unfinished bundles, the lost drone's tasks start unassigned."""
        keys = sorted({k for k, _ in remaining} | {k for d in survivors for k, _ in d.path if run.tasks[k]["status"] != "completed"})
        tasks = []
        for k in keys:
            g = run.geoms[k]
            pts = [run.projection.to_local(lon, lat) for lon, lat in g.coords]
            length = LineString(pts).length if len(pts) > 1 else None
            tasks.append(Task(id=k, endpoints=(pts[0], pts[-1]), priority=2, required_capability=run.tasks[k].get("capability"),
                              hover_time_s=g.hover_s, task_type=g.task_type, path_length_m=length if len(pts) > 2 else None))
        reserve = float(run.flight.get("rth_battery_pct", 25))
        agents = [Agent(id=d.drone_id, name=d.name, position=run.projection.to_local(d.lon, d.lat), battery_pct=float(d.battery or 100),
                        speed_mps=float(run.flight.get("speed_mps", 8)), capabilities=frozenset(("camera", "thermal", "multispectral", "sprayer")),
                        drain_pct_per_s=100.0 / (25 * 60), reserve_pct=reserve, max_tasks=60)
                  for d in survivors]
        previous_plans = {}
        for d in survivors:
            path = [PathStep(task_id=k, orientation=o) for k, o in d.path if k in keys]
            previous_plans[d.drone_id] = AgentPlan(agent_id=d.drone_id, path=path, bundle=[s.task_id for s in path])
        previous = AllocationResult(algorithm="cbba", converged=True, timed_out=False, iterations=0, consensus_rounds=0, messages_exchanged=0,
                                    convergence_time_s=0.0, assignments={s.task_id: a for a, p in previous_plans.items() for s in p.path},
                                    winning_bids={}, agent_plans=previous_plans, unassigned={})
        problem = AllocationProblem(agents=tuple(agents), tasks=tuple(tasks), params=AllocationParams(time_limit_s=settings.CBBA_TIME_LIMIT_SECONDS))
        return get_algorithm("cbba").reallocate(problem, previous, removed_agent_ids=[lost.drone_id])

    def _upload_replan(self, run: ActiveRun, uin: str, built: BuiltMission) -> bool:
        """HOLD -> upload the re-planned mission -> resume MISSION for one drone."""
        dr = run.drones[uin]
        with self.session_factory() as db:
            mission = db.get(Mission, run.mission_id)
            adapter = SimulationAdapter(self.client, str(run.session_id), mission, db.get(Drone, uuid.UUID(dr.drone_id)))
            try:
                adapter.hold()
                res = adapter.upload_mission(built.items)
                dr.mission_gen = (res.get(uin) or {}).get("gen")
                adapter.start_mission()
                inst = db.get(SimulationDroneInstance, dr.instance_id)
                inst.route = built.as_dict()
                inst.bundle = [k for k, _ in dr.path]
                db.commit()
                return True
            except (SimhostError, AdapterError) as exc:
                logger.warning("replan upload failed for %s: %s", uin, exc)
                return False

    # ------------------------------------------------------------ controls

    def _run(self, session_id: uuid.UUID) -> ActiveRun:
        run = self.runs.get(session_id)
        if run is None:
            raise SimulationError("NOT_RUNNING", "Simulation session is not running", 409)
        return run

    async def pause(self, session_id: uuid.UUID, paused: bool) -> dict:
        run = self._run(session_id)
        res = await asyncio.to_thread(self.client.pause if paused else self.client.resume, str(session_id))
        run.paused = paused
        if not paused:
            # No telemetry flows while the world is paused: restart the
            # comm-loss clocks so resuming is not mistaken for a link loss.
            now = time.time()
            for dr in run.drones.values():
                if dr.last_rx:
                    dr.last_rx = now
        run.state = "paused" if paused else "running"
        await asyncio.to_thread(self._set_state, session_id, run.state)
        await self._event(run, "paused" if paused else "resumed", "operator", None, "running" if paused else "paused", run.state,
                          f"Gazebo world {'paused' if paused else 'resumed'} at t={res.get('sim_time_s') or 0:.1f} s", "continued", res)
        return res

    async def set_speed(self, session_id: uuid.UUID, speed: float) -> dict:
        if speed not in SPEEDS:
            raise SimulationError("SPEED", f"Speed must be one of {SPEEDS}")
        run = self._run(session_id)
        res = await asyncio.to_thread(self.client.speed, str(session_id), speed)
        previous, run.speed = run.speed, speed
        await asyncio.to_thread(self._set_speed, session_id, speed)
        await self._event(run, "speed_changed", "operator", None, f"{previous:g}x", f"{speed:g}x", "Gazebo real-time factor updated", "continued", res)
        return res

    def _set_state(self, session_id, state):
        with self.session_factory() as db:
            sess = db.get(SimulationSession, session_id)
            sess.state = state
            db.commit()

    def _set_speed(self, session_id, speed):
        with self.session_factory() as db:
            sess = db.get(SimulationSession, session_id)
            sess.speed = speed
            db.commit()

    async def inject_failure(self, session_id: uuid.UUID, ftype: str, drone_id: str | None, params: dict) -> dict:
        run = self._run(session_id)
        dr = next((d for d in run.drones.values() if d.drone_id == str(drone_id)), None) if drone_id else None
        if ftype in ("kill", "link", "battery", "gps_drift") and dr is None:
            raise SimulationError("DRONE", "Select a simulated drone of this session")
        if dr is not None and dr.failed and ftype != "obstacle":
            raise SimulationError("DRONE_FAILED", f"{dr.name} has already failed", 409)
        if ftype == "obstacle":
            lat, lon = float(params["latitude"]), float(params["longitude"])
            size = params.get("size_m") or [6.0, 6.0, 30.0]
            ground = self._ground_z(run, lat, lon)
            params = {**params, "size_m": size, "ground_z": ground, "name": params.get("name") or f"obstacle_{len(run.obstacles) + 1}"}
        res = await asyncio.to_thread(self.client.failure, str(session_id), ftype, dr.uin if dr else None, params)
        await self._event(run, "failure_injected", "operator", dr, None, None, f"{ftype} injected", None, {"type": ftype, "params": params, "result": res})
        if ftype == "link":
            cfg = res.get("link", {})
            if cfg.get("down"):
                await self._event(run, "comm_degraded", "simhost", dr, "ok", "down",
                                  f"Link down: no telemetry or commands; PX4 data-link-loss action = {run.constraints.get('link_loss_policy', 'RTH')}",
                                  "continued", cfg)
            else:
                await self._event(run, "comm_degraded", "simhost", dr, None, "degraded" if (cfg.get("loss_pct") or cfg.get("latency_ms")) else "ok",
                                  f"Latency {cfg.get('latency_ms', 0):.0f} ms, loss {cfg.get('loss_pct', 0):.0f} %", "continued", cfg)
        elif ftype == "battery":
            await self._event(run, "battery_low", "operator", dr, None, None,
                              f"PX4 battery simulator forced down to {params.get('target_pct', 15)} %", None, res)
        elif ftype == "gps_drift":
            await self._event(run, "gps_drift", "simhost", dr, None, "drift" if params.get("enabled", True) else "nominal",
                              "GPS 'wrong' failure injected into PX4's simulated GPS", "continued", res)
        elif ftype == "obstacle":
            ob = res.get("obstacle", {})
            xy = run.projection.to_local(lon, lat)
            run.obstacles.append({"name": params["name"], "xy": xy, "size_m": size, "top_z": params["ground_z"] + size[2],
                                  "latitude": lat, "longitude": lon})
            err = None
            if ob.get("gazebo_latlon"):
                g = ob["gazebo_latlon"]
                err = round(math.dist(xy, run.projection.to_local(g[1], g[0])), 3)
            await self._event(run, "obstacle_spawned", "simhost", None, None, None,
                              f"{params['name']} {'present' if ob.get('exists_in_gazebo') else 'NOT found'} in Gazebo; map->Gazebo->map error {err} m",
                              "continued", {**ob, "position_error_m": err})
        elif ftype == "wind":
            run.gusts.append({"start_sim_t": run.sim_time_s, "duration_s": float(params.get("duration_s", 10)), "force_n": res.get("force_n"),
                              "targets": [dr.uin] if dr else list(run.drones)})
            await self._event(run, "wind_gust", "simhost", dr, None, None,
                              f"Gust {params.get('speed_mps', 8)} m/s from {params.get('from_deg', 270)}° -> {res.get('force_n')} N for {params.get('duration_s', 10)} s",
                              "continued", res)
            self._spawn(self._measure_gust(run, run.gusts[-1]))
        return res

    def _ground_z(self, run: ActiveRun, lat: float, lon: float) -> float:
        """Terrain height (Gazebo z) at a point, from the session heightmap."""
        try:
            import json

            import numpy as np
            from PIL import Image

            ws = workspace_root() / str(run.session_id)
            meta = json.loads((ws / "metadata.json").read_text())
            hm = np.asarray(Image.open(ws / "heightmap.png")).astype(float)
            zmin, zmax = meta["terrain"]["relative_z_range_m"]
            half = meta["extent_m"]["half"]
            n = hm.shape[0]
            x, y, _ = run.frame.to_enu(lat, lon, run.frame.h0)
            j = int(round((x + half) / (2 * half) * (n - 1)))
            i = int(round((half - y) / (2 * half) * (n - 1)))
            i, j = min(max(i, 0), n - 1), min(max(j, 0), n - 1)
            return float(zmin + hm[i, j] / 65535.0 * (zmax - zmin))
        except Exception:
            return 0.0

    async def _measure_gust(self, run: ActiveRun, gust: dict) -> None:
        """Max cross-track deviation (Gazebo truth vs planned route) during the gust."""
        end_t = gust["start_sim_t"] + gust["duration_s"] + 2.0
        while run.sim_time_s < end_t and run.session_id in self.runs:
            await asyncio.sleep(0.5)
        result = {}
        for uin in gust["targets"]:
            dr = run.drones.get(uin)
            if dr is None or len(dr.planned_route) < 2:
                continue
            route = LineString([run.projection.to_local(lon, lat) for lon, lat in dr.planned_route])
            before = [p for p in dr.track if gust["start_sim_t"] - 10 <= p[0] < gust["start_sim_t"] and p[4] is not None]
            during = [p for p in dr.track if gust["start_sim_t"] <= p[0] <= end_t and p[4] is not None]
            dev = lambda pts: max((route.distance(Point(run.projection.to_local(p[5], p[4]))) for p in pts), default=None)  # noqa: E731
            result[uin] = {"max_deviation_before_m": round(dev(before), 2) if before else None,
                           "max_deviation_during_m": round(dev(during), 2) if during else None}
        gust["effect"] = result
        await self._event(run, "wind_effect", "orchestrator", None, None, None,
                          "Cross-track deviation from the planned route (Gazebo ground truth): "
                          + ", ".join(f"{run.drones[u].name} {v['max_deviation_before_m']} m -> {v['max_deviation_during_m']} m" for u, v in result.items()),
                          "continued", result)

    # -------------------------------------------------------------- monitor

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            for sid, run in list(self.runs.items()):
                try:
                    await self._tick(run)
                except Exception:  # pragma: no cover - keep monitoring other sessions
                    logger.exception("simulation monitor tick failed")

    async def _tick(self, run: ActiveRun) -> None:
        if run.state not in ("running", "paused"):
            return
        try:
            status = await asyncio.to_thread(self.client.status, str(run.session_id))
            run.sim_time_s = float(status.get("stats", {}).get("sim_time_s") or run.sim_time_s)
            run.guardrail = {**status.get("guardrail", {}), "achieved_rtf": status.get("achieved_rtf"), "sim_fps": status.get("sim_fps")}
            run.speed = float(status.get("requested_speed") or run.speed)
        except SimhostError:
            status = {}
        if status.get("state") == "failed" and not run.finishing:
            # The simulator itself died (e.g. the Gazebo server): nothing is flying any more.
            error = status.get("error") or "Simulation host reported the session failed"
            await self._event(run, "simulator_failed", "simhost", None, run.state, "failed", error, "aborted")
            self._spawn(self.finish(run.session_id, "failed", error))
            return
        now = time.time()
        if not run.paused:
            for dr in run.drones.values():
                if dr.failed or dr.done or not dr.last_rx:
                    continue
                if not dr.comm_lost and now - dr.last_rx > settings.SIM_COMM_LOSS_S:
                    dr.comm_lost = True
                    await self._event(run, "comm_lost", "orchestrator", dr, dr.state, "comm_lost",
                                      f"No telemetry for {now - dr.last_rx:.1f} s; PX4 link-loss policy {run.constraints.get('link_loss_policy', 'RTH')} applies onboard",
                                      "continued")
        await self.connections.broadcast_json({"type": "simulation", "data": self.snapshot(run, consume_tail=True)}, organization_id=run.org_id)
        if now - run.last_persist > 2.0:
            run.last_persist = now
            await asyncio.to_thread(self._persist, run)

    def _persist(self, run: ActiveRun) -> None:
        with self.session_factory() as db:
            sess = db.get(SimulationSession, run.session_id)
            if sess is None:
                return
            sess.sim_time_s = run.sim_time_s
            sess.speed = run.speed
            sess.progress = {**(sess.progress or {}), **self.progress(run), "guardrail": run.guardrail}
            for dr in run.drones.values():
                inst = db.get(SimulationDroneInstance, dr.instance_id)
                state = "failed" if dr.failed else ("comm_lost" if dr.comm_lost else dr.state)
                inst.state = state if state in ("pending", "starting", "ready", "flying", "returning", "landed", "failed", "unavailable", "comm_lost") else inst.state
                inst.track = dr.track
                inst.stats = self.drone_stats(dr)
            db.commit()

    # ------------------------------------------------------------- finish

    async def _maybe_finish(self, run: ActiveRun) -> None:
        if run.finishing or run.state != "running":
            return
        if all(d.done or d.failed for d in run.drones.values()):
            outcome = "completed" if any(not d.failed for d in run.drones.values()) else "aborted"
            self._spawn(self.finish(run.session_id, outcome, "All drones finished"))

    async def finish(self, session_id: uuid.UUID, outcome: str, reason: str, user_id: uuid.UUID | None = None) -> dict:
        run = self.runs.get(session_id)
        if run is not None:
            if run.finishing:
                return {"state": "finishing"}
            run.finishing = True
            run.state = outcome
            await self.connections.broadcast_json({"type": "simulation", "data": self.snapshot(run, consume_tail=True)}, organization_id=run.org_id)
        result = await asyncio.to_thread(self._finish_sync, session_id, outcome, reason, user_id)
        self._forget(session_id)
        return result

    def _finish_sync(self, session_id: uuid.UUID, outcome: str, reason: str, user_id) -> dict:
        run = self.runs.get(session_id)
        if run is not None:
            self._persist(run)
        with self.session_factory() as db:
            sess = db.get(SimulationSession, session_id)
            mission = db.get(Mission, sess.mission_id)
            user = db.get(User, user_id) if user_id else None
            try:
                stop = self.client.stop(str(session_id))
            except SimhostError as exc:
                stop = {"error": str(exc)}
            sess.state = outcome
            sess.ended_at = _now()
            sess.progress = {**(sess.progress or {}), "cleanup": stop}
            db.commit()
            try:
                if outcome == "completed" and mission.status == "active":
                    MissionStateService(db).transition(mission, "completed", user, f"Simulation {session_id} completed")
                elif mission.status in ("active", "armed"):
                    MissionStateService(db).transition(mission, "aborted", user, f"Simulation {session_id} {outcome}: {reason}")
            except Exception:  # pragma: no cover
                logger.exception("mission transition on finish failed")
            if run is not None:
                for k, t in run.tasks.items():
                    if t["status"] == "completed" and k in run.task_rows:
                        row = db.get(MissionTask, run.task_rows[k])
                        if row:
                            row.status = "completed"
                db.commit()
            for inst in sess.instances:
                dr = run.drones.get(inst.uin) if run is not None else None
                if inst.state != "failed":
                    # Final state: landed if it came back down, otherwise its
                    # SITL process was stopped with the session.
                    inst.state = "landed" if (dr is not None and dr.done and not dr.failed) or inst.state in ("pending", "ready", "landed") else "unavailable"
                drone = db.get(Drone, inst.drone_id)
                if drone.status != "critical":
                    drone.status, drone.armed = "offline", False
                drone.flight_mode = "SIM_ENDED" if drone.flight_mode != "FAILED" else "FAILED"
            db.commit()
            ev_type = {"completed": "mission_completed", "aborted": "aborted", "stopped": "stopped"}.get(outcome, "failed")
            record_event(db, sess, ev_type, "orchestrator", new_state=outcome, response=reason,
                         outcome="aborted" if outcome != "completed" else None,
                         details={"leftover_processes": stop.get("leftover_processes") if isinstance(stop, dict) else None})
            report = self._generate_report(db, sess)
            return {"state": outcome, "report_id": str(report.id) if report else None, "cleanup": stop}

    def _generate_report(self, db: Session, sess: SimulationSession):
        from app.services.simulation.reports import generate_report

        report = generate_report(db, sess)
        record_event(db, sess, "report_generated", "orchestrator", response=f"Report generated in {report.generation_ms:.0f} ms",
                     details={"report_id": str(report.id), "generation_ms": report.generation_ms})
        return report

    # --------------------------------------------------------------- views

    def progress(self, run: ActiveRun) -> dict:
        tasks = run.tasks
        completed = [k for k, t in tasks.items() if t["status"] == "completed"]
        area = [k for k in completed if run.geoms[k].task_type in AREA_TASK_TYPES]
        segs = [LineString([run.projection.to_local(lon, lat) for lon, lat in run.geoms[k].coords]) for k in area if len(run.geoms[k].coords) > 1]
        coverage = coverage_fraction(run.aoi_local, segs, run.swath_m) * 100 if segs else 0.0
        return {
            "tasks_total": len(tasks),
            "tasks_assigned": sum(1 for t in tasks.values() if t["status"] in ("assigned", "completed") and t["drone"]),
            "tasks_completed": len(completed),
            "tasks_failed": sum(1 for t in tasks.values() if t["status"] == "failed"),
            "tasks_unassigned": sum(1 for t in tasks.values() if t["status"] == "unassigned"),
            "coverage_pct": round(coverage, 2),
            "task_status": {k: {"status": t["status"], "drone": t["drone"]} for k, t in tasks.items()},
        }

    @staticmethod
    def drone_stats(dr: DroneRun) -> dict:
        return {
            "battery_start": dr.battery_start,
            "battery_end": dr.battery_last_armed if dr.battery_last_armed is not None else dr.battery,
            "distance_m": round(dr.distance_m, 1),
            "waypoints_reached": dr.waypoints_reached, "gps_error_max_m": round(dr.gps_err_max_m, 2),
            "collisions": sorted(dr.collided_with), "failed": dr.failed, "comm_lost": dr.comm_lost, "returning": dr.returning,
        }

    def snapshot(self, run: ActiveRun, consume_tail: bool = False) -> dict:
        drones = []
        for dr in run.drones.values():
            current = next((k for k, _ in dr.path if run.tasks.get(k, {}).get("status") != "completed"), None)
            drones.append({
                "uin": dr.uin, "drone_id": dr.drone_id, "name": dr.name, "index": dr.index,
                "state": "failed" if dr.failed else ("comm_lost" if dr.comm_lost else dr.state),
                "lat": dr.lat, "lon": dr.lon, "alt": dr.alt, "battery": dr.battery, "mode": dr.mode, "armed": dr.armed,
                "current_task": current, "remaining_tasks": [k for k, _ in dr.path], "gps_error_m": dr.gps_err_m,
                "planned_route": dr.planned_route, "track_tail": list(dr.tail), "stats": self.drone_stats(dr),
            })
            if consume_tail:
                dr.tail.clear()
        return {
            "session_id": str(run.session_id), "mission_id": str(run.mission_id), "state": run.state, "sim_time_s": round(run.sim_time_s, 2),
            "speed": run.speed, "paused": run.paused, "guardrail": run.guardrail, "progress": self.progress(run), "drones": drones,
            "obstacles": [{k: v for k, v in o.items() if k != "xy"} for o in run.obstacles],
        }

    # ---------------------------------------------------------- helpers

    async def _event(self, run: ActiveRun, event_type: str, source: str, dr: DroneRun | None, prev, new, response, outcome, details=None) -> None:
        def write():
            with self.session_factory() as db:
                sess = db.get(SimulationSession, run.session_id)
                sess.sim_time_s = run.sim_time_s
                record_event(db, sess, event_type, source, drone_id=uuid.UUID(dr.drone_id) if dr else None, previous_state=prev, new_state=new,
                             response=response, outcome=outcome, details=details, sim_time_s=run.sim_time_s)

        await asyncio.to_thread(write)
