"""Mission planning: wizard persistence, task generation, coordination
(CBBA), plan persistence, validation, and launch readiness.

This is the only module that translates between the database world
(missions, drones, geofences, PostGIS geography) and the algorithm world
(`app.services.coordination`, local metres, plain dataclasses).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from shapely.geometry import LineString, Point as ShapelyPoint, mapping
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.geo import geography_to_geojson, geojson_to_geography
from app.core.logging import log_event
from app.models.drone import Drone
from app.models.geofence import Geofence
from app.models.mission import Mission
from app.models.mission_planning import MissionPlan, MissionTask, TaskAssignment
from app.models.payload import Payload
from app.models.task_bundle import TaskBundle
from app.models.user import User
from app.repositories.mission_repository import MissionRepository
from app.schemas.mission_planning import (
    REQUIRED_STEPS,
    STEP_SCHEMAS,
    AOIStep,
    BundleOut,
    BundleTaskOut,
    ConstraintsStep,
    FlightParams,
    PlanOut,
    PlanTaskOut,
    SensorsStep,
    TemplateStep,
    WizardState,
)
from app.services.coordination import (
    Agent,
    AgentPlan,
    AllocationParams,
    AllocationProblem,
    AllocationResult,
    PathStep,
    Task,
    get_algorithm,
)
from app.services.coordination.geometry import LocalProjection
from app.services.coordination.task_generation import (
    CoverageError,
    CoverageSpec,
    coverage_fraction,
    generate_coverage_tasks,
    generate_search_cells,
    swath_width,
)
from app.services.mission_state_service import EDITABLE_STATES, MissionStateService
from app.services.mission_templates import TEMPLATES
from app.services.spatial_validation import SpatialValidator

logger = logging.getLogger("dronematrx.mission.planning")
settings = get_settings()

AOI_MIN_AREA_M2 = 100.0
AOI_MAX_AREA_M2 = 25_000_000.0  # 25 km^2
INSPECTION_POINT_MAX_DISTANCE_M = 10_000.0
ELIGIBLE_DRONE_STATUSES = ("online", "active", "warning")
BATTERY_MARGIN_PCT = 5.0
# Task types that image area (count towards coverage %).
AREA_TASK_TYPES = ("coverage", "search_cell")
# Rough usable pack energy by airframe, for the Wh estimate only.
BATTERY_CAPACITY_WH = {"quadrotor": 250.0, "multirotor": 250.0, "hexarotor": 450.0, "octorotor": 700.0, "fixed_wing": 200.0}


class PlanningError(Exception):
    """Mission cannot be planned/saved as requested (-> HTTP 422)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class MissionLockedError(Exception):
    """Mission state does not allow this change (-> HTTP 409)."""


@dataclass
class _ProblemContext:
    projection: LocalProjection
    aoi_geojson: dict[str, Any]
    aoi_local: Any
    tasks: list[Task]
    task_geometry: dict[str, Any]  # task key -> local shapely geometry
    task_swath: dict[str, float]
    agents: list[Agent]
    drones: dict[str, Drone]
    excluded: list[dict[str, Any]]
    warnings: list[str]
    flight: FlightParams
    sensors: SensorsStep
    constraints: ConstraintsStep
    coverage_meta: dict[str, Any] = field(default_factory=dict)


def _check(code: str, severity: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "details": details}


class MissionPlanningService:
    def __init__(self, db: Session):
        self.db = db
        self.missions = MissionRepository(db)

    # =================================================================
    # Wizard
    # =================================================================

    @staticmethod
    def completed_steps(mission: Mission) -> list[int]:
        return sorted(set((mission.config or {}).get("completed_steps", [])))

    def missing_steps(self, mission: Mission) -> list[int]:
        done = set(self.completed_steps(mission))
        return [step for step in REQUIRED_STEPS if step not in done]

    def config_hash(self, mission: Mission) -> str:
        config = mission.config or {}
        aoi = geography_to_geojson(mission.aoi_geometry)
        payload = {key: config.get(key) for key in ("flight", "sensors", "constraints")}
        payload["aoi"] = aoi
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def current_plan(self, mission: Mission) -> MissionPlan | None:
        return self.db.execute(
            select(MissionPlan).where(MissionPlan.mission_id == mission.id, MissionPlan.is_current.is_(True))
        ).scalar_one_or_none()

    def is_stale(self, mission: Mission, plan: MissionPlan | None) -> bool:
        return plan is not None and plan.params.get("config_hash") != self.config_hash(mission)

    def wizard_state(self, mission: Mission, replanned: bool = False) -> WizardState:
        plan = self.current_plan(mission)
        return WizardState(
            mission_id=mission.id,
            name=mission.name,
            status=mission.status,
            template=mission.template,
            wizard_step=mission.wizard_step,
            completed_steps=self.completed_steps(mission),
            config={k: v for k, v in (mission.config or {}).items() if k != "completed_steps"},
            aoi_geometry=geography_to_geojson(mission.aoi_geometry),
            aoi_area_sq_meters=self.missions.area_sq_meters(mission),
            editable=mission.status in EDITABLE_STATES,
            plan_id=plan.id if plan else None,
            plan_stale=self.is_stale(mission, plan),
            validation=mission.validation_report,
            replanned=replanned,
        )

    def save_step(self, mission: Mission, step: int, data: dict[str, Any], user: User, ip_address: str | None = None) -> WizardState:
        """Validate and persist one wizard step (auto-save on Next / Save & Exit)."""
        if mission.status not in EDITABLE_STATES:
            raise MissionLockedError(f"Mission is {mission.status.upper()}; its plan can no longer be edited")
        if step not in STEP_SCHEMAS:
            raise PlanningError("INVALID_STEP", "Steps 1-5 are saved here; step 6 is POST /missions/{id}/validate")
        done = set(self.completed_steps(mission))
        for previous in range(1, step):
            if previous not in done:
                raise PlanningError("STEP_ORDER", f"Complete step {previous} before step {step}")

        parsed = STEP_SCHEMAS[step].model_validate(data)  # pydantic.ValidationError -> 422
        config = dict(mission.config or {})

        if step == 1:
            self._apply_template(mission, config, parsed)  # type: ignore[arg-type]
        elif step == 2:
            config["aoi"] = self._apply_aoi(mission, parsed)  # type: ignore[arg-type]
        elif step == 3:
            config["flight"] = parsed.model_dump(mode="json")
            constraints = config.get("constraints")
            if constraints and parsed.rth_battery_pct >= constraints.get("min_battery_pct", 100):  # type: ignore[attr-defined]
                raise PlanningError("BATTERY_THRESHOLDS", "RTH battery reserve must be below the minimum launch battery (step 5)")
        elif step == 4:
            config["sensors"] = self._check_sensors(mission, parsed)  # type: ignore[arg-type]
        elif step == 5:
            config["constraints"] = self._check_constraints(mission, config, parsed)  # type: ignore[arg-type]

        config["completed_steps"] = sorted(done | {step})
        mission.config = config
        mission.wizard_step = max(mission.wizard_step or 1, min(step + 1, 6))
        self.db.commit()
        self.db.refresh(mission)
        log_event(logger, "mission.wizard_saved", mission_id=mission.id, step=step)

        state = MissionStateService(self.db)
        if mission.status == "draft":
            state.transition(mission, "planning", user, "Mission planning wizard started", ip_address)
        elif mission.status == "validating":
            state.transition(mission, "planning", user, f"Wizard step {step} changed after validation", ip_address)

        replanned = False
        plan = self.current_plan(mission)
        if plan is not None and step >= 2 and not self.missing_steps(mission) and self.is_stale(mission, plan):
            try:
                self._plan(mission, user, trigger="parameters_changed", algorithm=plan.algorithm)
                replanned = True
            except PlanningError as exc:
                # Keep the old plan (now reported stale); the UI shows why.
                log_event(logger, "cbba.replan_failed", logging.WARNING, mission_id=mission.id, code=exc.code)
        return self.wizard_state(mission, replanned=replanned)

    def _apply_template(self, mission: Mission, config: dict, step: TemplateStep) -> None:
        template = TEMPLATES[step.template]
        mission.template = step.template
        mission.mission_type = template.mission_type
        if step.name:
            mission.name = step.name
        config["template"] = step.model_dump(mode="json")
        done = set(config.get("completed_steps", []))
        if 3 not in done:
            config["flight"] = FlightParams(**template.flight).model_dump(mode="json")
        if 4 not in done:
            config["sensors"] = {**(config.get("sensors") or {}), "required_sensors": list(template.required_sensors)}

    def _apply_aoi(self, mission: Mission, step: AOIStep) -> dict[str, Any]:
        if step.source == "existing":
            if mission.aoi_geometry is None:
                raise PlanningError("AOI_MISSING", "This mission has no saved AOI yet; draw one")
            geometry = geography_to_geojson(mission.aoi_geometry)
        elif step.source == "copied":
            other = self.missions.get(step.copy_from_mission_id, mission.organization_id)  # type: ignore[arg-type]
            if other is None or other.aoi_geometry is None:
                raise PlanningError("AOI_MISSING", "The selected mission has no AOI to copy")
            geometry = geography_to_geojson(other.aoi_geometry)
        else:
            geometry = step.geometry
        try:
            candidate = geojson_to_geography(geometry)  # type: ignore[arg-type]
        except ValueError as exc:
            raise PlanningError("AOI_INVALID", str(exc)) from exc
        # Check before assigning so a rejected AOI never touches the mission.
        area = self.db.scalar(select(func.ST_Area(func.ST_GeogFromText(candidate.data)))) or 0.0
        if not AOI_MIN_AREA_M2 <= area <= AOI_MAX_AREA_M2:
            raise PlanningError(
                "AOI_INVALID",
                f"AOI area {area / 1e6:.4f} km² is outside the supported range "
                f"({AOI_MIN_AREA_M2:.0f} m² – {AOI_MAX_AREA_M2 / 1e6:.0f} km²)",
            )
        mission.aoi_geometry = candidate
        return {"source": step.source, "vertex_count": len(geometry["coordinates"][0]), "area_sq_meters": area}  # type: ignore[index]

    def _org_drones(self, mission: Mission, drone_ids: list[uuid.UUID]) -> dict[uuid.UUID, Drone]:
        if not drone_ids:
            return {}
        rows = self.db.execute(
            select(Drone).where(Drone.organization_id == mission.organization_id, Drone.id.in_(drone_ids))
        ).scalars().all()
        return {drone.id: drone for drone in rows}

    def _functional_payloads(self, drone_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Payload]]:
        result: dict[uuid.UUID, list[Payload]] = {d: [] for d in drone_ids}
        if drone_ids:
            for payload in self.db.execute(select(Payload).where(Payload.drone_id.in_(drone_ids))).scalars():
                if payload.status != "fault":
                    result[payload.drone_id].append(payload)
        return result

    def _check_sensors(self, mission: Mission, step: SensorsStep) -> dict[str, Any]:
        drones = self._org_drones(mission, step.drone_ids)
        missing = [str(d) for d in step.drone_ids if d not in drones or not drones[d].active]
        if missing:
            raise PlanningError("DRONE_NOT_FOUND", f"Unknown or inactive drone(s): {', '.join(missing)}")
        payloads = self._functional_payloads(step.drone_ids)
        for drone_key, payload_type in step.payload_assignments.items():
            try:
                drone_id = uuid.UUID(drone_key)
            except ValueError:
                raise PlanningError("PAYLOAD_INCOMPATIBLE", f"Invalid drone id {drone_key}") from None
            if drone_id not in drones:
                raise PlanningError("PAYLOAD_INCOMPATIBLE", "Payload assigned to a drone that is not selected")
            if payload_type and payload_type not in {p.type for p in payloads[drone_id]}:
                raise PlanningError("PAYLOAD_INCOMPATIBLE", f"{drones[drone_id].name} has no working {payload_type} payload")
        capabilities = self._capabilities(step, drones, payloads)
        for sensor in step.required_sensors:
            if not any(sensor in caps for caps in capabilities.values()):
                raise PlanningError("PAYLOAD_MISSING", f"No selected drone carries a working {sensor} payload")
        return step.model_dump(mode="json")

    @staticmethod
    def _capabilities(step: SensorsStep, drones: dict[uuid.UUID, Drone], payloads: dict[uuid.UUID, list[Payload]]) -> dict[uuid.UUID, frozenset[str]]:
        caps: dict[uuid.UUID, frozenset[str]] = {}
        for drone_id in drones:
            assigned = step.payload_assignments.get(str(drone_id))
            available = frozenset(p.type for p in payloads.get(drone_id, []))
            caps[drone_id] = frozenset({assigned}) & available if assigned else available
        return caps

    def _check_constraints(self, mission: Mission, config: dict, step: ConstraintsStep) -> dict[str, Any]:
        flight = config.get("flight") or {}
        if flight and step.min_battery_pct <= flight.get("rth_battery_pct", 0):
            raise PlanningError("BATTERY_THRESHOLDS", "Minimum launch battery must be above the RTH battery reserve (step 3)")
        if step.geofence_ids:
            zones = self.db.execute(
                select(Geofence).where(Geofence.organization_id == mission.organization_id, Geofence.id.in_(step.geofence_ids))
            ).scalars().all()
            found = {z.id: z for z in zones}
            unknown = [str(g) for g in step.geofence_ids if g not in found]
            if unknown:
                raise PlanningError("GEOFENCE_NOT_FOUND", f"Unknown geofence(s): {', '.join(unknown)}")
            if any(z.type == "nfz" for z in zones):
                raise PlanningError("GEOFENCE_INVALID", "NFZs are always enforced; select only inclusion/exclusion zones")
        try:
            get_algorithm(step.algorithm)
        except ValueError as exc:
            raise PlanningError("ALGORITHM_UNKNOWN", str(exc)) from exc
        if step.inspection_points and mission.aoi_geometry is not None:
            projection = LocalProjection.for_polygon(geography_to_geojson(mission.aoi_geometry))
            for point in step.inspection_points:
                x, y = projection.to_local(point.longitude, point.latitude)
                if (x * x + y * y) ** 0.5 > INSPECTION_POINT_MAX_DISTANCE_M:
                    raise PlanningError("TASK_INVALID", "Inspection point is more than 10 km from the AOI")
        return step.model_dump(mode="json")

    # =================================================================
    # Planning (coordination algorithm)
    # =================================================================

    def plan(self, mission: Mission, user: User | None, algorithm: str | None = None, ip_address: str | None = None) -> MissionPlan:
        """Explicit allocation request (wizard Step 6 'Run CBBA')."""
        if mission.status not in ("planning", "validating"):
            raise MissionLockedError(f"Cannot allocate a mission in {mission.status.upper()} state")
        plan = self._plan(mission, user, trigger="allocate", algorithm=algorithm)
        if mission.status == "validating":
            MissionStateService(self.db).transition(mission, "planning", user, "Plan re-allocated; validation required", ip_address)
        return plan

    def reallocate(
        self,
        mission: Mission,
        user: User | None,
        add_drone_ids: list[uuid.UUID],
        remove_drone_ids: list[uuid.UUID],
        ip_address: str | None = None,
    ) -> MissionPlan:
        """Add/remove drones and re-run CBBA warm-started from the current plan."""
        if mission.status not in ("planning", "validating"):
            raise MissionLockedError(f"Cannot change drones of a mission in {mission.status.upper()} state")
        if self.missing_steps(mission):
            raise PlanningError("WIZARD_INCOMPLETE", f"Complete wizard steps {self.missing_steps(mission)} first")
        sensors = dict(mission.config["sensors"])
        roster = [uuid.UUID(d) for d in sensors.get("drone_ids", [])]
        for drone_id in remove_drone_ids:
            if drone_id not in roster:
                raise PlanningError("DRONE_NOT_IN_MISSION", f"Drone {drone_id} is not part of this mission")
        for drone_id in add_drone_ids:
            if drone_id in roster:
                raise PlanningError("DRONE_ALREADY_IN_MISSION", f"Drone {drone_id} is already part of this mission")
        new_roster = [d for d in roster if d not in remove_drone_ids] + list(add_drone_ids)
        if not new_roster:
            raise PlanningError("NO_DRONES_SELECTED", "A mission needs at least one drone")
        assignments = {k: v for k, v in sensors.get("payload_assignments", {}).items() if uuid.UUID(k) in new_roster}
        new_sensors = SensorsStep(required_sensors=sensors["required_sensors"], drone_ids=new_roster, payload_assignments=assignments)
        mission.config = {**mission.config, "sensors": self._check_sensors(mission, new_sensors)}
        self.db.commit()
        for drone_id in remove_drone_ids:
            log_event(logger, "mission.drone_removed", mission_id=mission.id, drone_id=drone_id)
        for drone_id in add_drone_ids:
            log_event(logger, "mission.drone_added", mission_id=mission.id, drone_id=drone_id)

        previous_plan = self.current_plan(mission)
        previous = self._result_from_plan(previous_plan) if previous_plan else None
        plan = self._plan(
            mission,
            user,
            trigger="reallocate",
            algorithm=previous_plan.algorithm if previous_plan else None,
            previous=previous,
            removed=[str(d) for d in remove_drone_ids],
            added=[str(d) for d in add_drone_ids],
        )
        if mission.status == "validating":
            MissionStateService(self.db).transition(mission, "planning", user, "Drones changed; validation required", ip_address)
        return plan

    def _build_problem(self, mission: Mission) -> _ProblemContext:
        missing = self.missing_steps(mission)
        if missing:
            raise PlanningError("WIZARD_INCOMPLETE", f"Complete wizard steps {missing} before planning")
        if mission.aoi_geometry is None:
            raise PlanningError("AOI_MISSING", "Mission has no AOI")
        config = mission.config
        flight = FlightParams.model_validate(config["flight"])
        sensors = SensorsStep.model_validate(config["sensors"])
        constraints = ConstraintsStep.model_validate(config["constraints"])

        aoi_geojson = geography_to_geojson(mission.aoi_geometry)
        projection = LocalProjection.for_polygon(aoi_geojson)
        aoi_local = projection.polygon_to_local(aoi_geojson)

        tasks: list[Task] = []
        geometry: dict[str, Any] = {}
        swaths: dict[str, float] = {}
        warnings: list[str] = []
        meta: dict[str, Any] = {"passes": [], "template": mission.template}
        n_sensors = len(sensors.required_sensors)
        prefixes = "TUVWX"
        # Sprint 5 task types: inspection = route through the picked targets;
        # custom = ordered custom waypoints. Without targets/waypoints both
        # fall back to a coverage pass, as before.
        point_mission = (mission.template == "inspection" and bool(constraints.inspection_points)) or (
            mission.template == "custom" and bool(constraints.custom_waypoints)
        )
        meta["mode"] = "points" if point_mission else ("search_cells" if flight.pattern == "sar_cells" else "coverage")
        for idx, sensor in enumerate([] if point_mission else sensors.required_sensors):
            spec = CoverageSpec(
                altitude_m=flight.altitude_m,
                overlap_pct=flight.overlap_pct,
                pattern=flight.pattern,
                heading_deg=flight.heading_deg,
                sensor=sensor,
                priority=flight.priority,
                required_capability=sensor,
                max_tasks=max(10, 120 // n_sensors),
                spacing_m=flight.lane_spacing_m,
            )
            try:
                if flight.pattern == "sar_cells":
                    generated = generate_search_cells(aoi_local, spec, flight.sar_cell_size_m, id_prefix="S" if idx == 0 else prefixes[idx])
                else:
                    generated = generate_coverage_tasks(aoi_local, spec, id_prefix=prefixes[idx])
            except CoverageError as exc:
                raise PlanningError("AOI_COVERAGE", str(exc)) from exc
            for task in generated.tasks:
                tasks.append(task)
                geometry[task.id] = generated.segments[task.id]
                swaths[task.id] = generated.swath_m
            warnings.extend(w for w in generated.warnings if w not in warnings)
            meta["passes"].append(
                {
                    "sensor": sensor,
                    "swath_m": round(generated.swath_m, 2),
                    "lane_spacing_m": round(generated.spacing_m, 2),
                    "lane_bearing_deg": round(generated.lane_bearing_deg, 1),
                    "tasks": len(generated.tasks),
                }
            )
        for n, point in enumerate(constraints.inspection_points, start=1):
            x, y = projection.to_local(point.longitude, point.latitude)
            key = f"P{n:03d}"
            tasks.append(
                Task(
                    id=key,
                    endpoints=((x, y), (x, y)),
                    priority=point.priority,
                    required_capability=point.required_sensor,
                    hover_time_s=point.hover_time_s,
                    task_type="inspection",
                )
            )
            geometry[key] = ShapelyPoint(x, y)
            swaths[key] = swath_width(point.required_sensor, flight.altitude_m)
        if mission.template == "custom":
            for n, wp in enumerate(constraints.custom_waypoints, start=1):
                x, y = projection.to_local(wp.longitude, wp.latitude)
                key = f"W{n:03d}"
                tasks.append(Task(id=key, endpoints=((x, y), (x, y)), priority=flight.priority, task_type="waypoint"))
                geometry[key] = ShapelyPoint(x, y)
                swaths[key] = swath_width(None, flight.altitude_m)

        drones = self._org_drones(mission, sensors.drone_ids)
        payloads = self._functional_payloads(sensors.drone_ids)
        capabilities = self._capabilities(sensors, drones, payloads)
        agents: list[Agent] = []
        excluded: list[dict[str, Any]] = []
        for drone in sorted(drones.values(), key=lambda d: (d.name, str(d.id))):
            reason = self._ineligibility(drone, constraints)
            if reason:
                excluded.append({"drone_id": str(drone.id), "drone_name": drone.name, "reason": reason})
                continue
            location = geography_to_geojson(drone.location)
            assert location is not None
            agents.append(
                Agent(
                    id=str(drone.id),
                    name=drone.name,
                    position=projection.to_local(*location["coordinates"][:2]),
                    battery_pct=float(drone.battery),
                    speed_mps=min(flight.speed_mps, drone.max_speed or flight.speed_mps),
                    capabilities=capabilities.get(drone.id, frozenset()),
                    drain_pct_per_s=100.0 / (max(drone.endurance_minutes or 30.0, 1.0) * 60.0),
                    reserve_pct=flight.rth_battery_pct,
                    max_tasks=constraints.max_tasks_per_drone,
                    max_range_m=constraints.max_range_km * 1000 if constraints.max_range_km else None,
                    max_flight_time_s=constraints.max_flight_time_min * 60 if constraints.max_flight_time_min else None,
                )
            )
        missing_ids = [str(d) for d in sensors.drone_ids if d not in drones]
        for drone_id in missing_ids:
            excluded.append({"drone_id": drone_id, "drone_name": drone_id[:8], "reason": "not_found"})

        return _ProblemContext(
            projection=projection,
            aoi_geojson=aoi_geojson,  # type: ignore[arg-type]
            aoi_local=aoi_local,
            tasks=tasks,
            task_geometry=geometry,
            task_swath=swaths,
            agents=agents,
            drones={str(d.id): d for d in drones.values()},
            excluded=excluded,
            warnings=warnings,
            flight=flight,
            sensors=sensors,
            constraints=constraints,
            coverage_meta=meta,
        )

    @staticmethod
    def _ineligibility(drone: Drone, constraints: ConstraintsStep) -> str | None:
        if not drone.active:
            return "inactive"
        # Sprint 5: a simulation drone staged on its launch pad (SITL not yet
        # spawned) can be planned; ARMED -> ACTIVE readiness still requires
        # live telemetry from the running SITL instance.
        staged = drone.simulated and drone.flight_mode == "SIM_STAGED" and drone.status == "offline"
        if drone.status not in ELIGIBLE_DRONE_STATUSES and not staged:
            return f"status_{drone.status}"
        if drone.location is None:
            return "no_position"
        if drone.gps_status in ("NO_FIX", "GPS_LOST") and not staged:
            return "no_gps_fix"
        if drone.battery < constraints.min_battery_pct:
            return f"battery_below_{constraints.min_battery_pct:g}"
        return None

    def _plan(
        self,
        mission: Mission,
        user: User | None,
        trigger: str,
        algorithm: str | None = None,
        previous: AllocationResult | None = None,
        removed: list[str] | None = None,
        added: list[str] | None = None,
    ) -> MissionPlan:
        ctx = self._build_problem(mission)
        name = algorithm or ctx.constraints.algorithm
        try:
            engine = get_algorithm(name)
        except ValueError as exc:
            raise PlanningError("ALGORITHM_UNKNOWN", str(exc)) from exc
        problem = AllocationProblem(
            agents=tuple(ctx.agents),
            tasks=tuple(ctx.tasks),
            params=AllocationParams(time_limit_s=settings.CBBA_TIME_LIMIT_SECONDS),
        )
        log_event(logger, "cbba.started", mission_id=mission.id, algorithm=name, trigger=trigger, drones=len(ctx.agents), tasks=len(ctx.tasks))
        started = time.perf_counter()
        if previous is not None and (removed or added):
            added_set = set(added or [])
            result = engine.reallocate(
                problem,
                previous,
                removed_agent_ids=removed or [],
                added_agents=[a for a in ctx.agents if a.id in added_set],
            )
        else:
            result = engine.allocate(problem)
        elapsed_ms = (time.perf_counter() - started) * 1000
        plan = self._persist(mission, ctx, result, trigger, user, elapsed_ms)
        log_event(
            logger,
            "cbba.converged" if result.converged else "cbba.failed",
            logging.INFO if result.converged else logging.WARNING,
            mission_id=mission.id,
            plan_id=plan.id,
            iterations=result.iterations,
            assigned=result.assigned_count,
            unassigned=len(result.unassigned),
            elapsed_ms=round(elapsed_ms, 1),
        )
        if previous is not None:
            moved = {t: a for t, a in result.assignments.items() if previous.assignments.get(t) not in (None, a)}
            for task_id, agent_id in sorted(moved.items()):
                log_event(logger, "cbba.task_reassigned", logging.DEBUG, mission_id=mission.id, task=task_id, drone_id=agent_id)
            log_event(logger, "cbba.reallocation", mission_id=mission.id, tasks_moved=len(moved))
        return plan

    def _persist(
        self,
        mission: Mission,
        ctx: _ProblemContext,
        result: AllocationResult,
        trigger: str,
        user: User | None,
        elapsed_ms: float,
    ) -> MissionPlan:
        # Replace the previous working set; older plan rows stay as summaries.
        old_plan_ids = select(MissionPlan.id).where(MissionPlan.mission_id == mission.id)
        self.db.execute(delete(TaskAssignment).where(TaskAssignment.plan_id.in_(old_plan_ids)))
        self.db.execute(delete(TaskBundle).where(TaskBundle.mission_id == mission.id))
        self.db.execute(delete(MissionTask).where(MissionTask.mission_id == mission.id))
        self.db.execute(update(MissionPlan).where(MissionPlan.mission_id == mission.id).values(is_current=False))

        to_lonlat = ctx.projection.to_lonlat
        task_rows: dict[str, MissionTask] = {}
        for task in ctx.tasks:
            geom_local = ctx.task_geometry[task.id]
            geom_lonlat = (
                LineString([to_lonlat(x, y) for x, y in geom_local.coords])
                if isinstance(geom_local, LineString)
                else ShapelyPoint(*to_lonlat(geom_local.x, geom_local.y))
            )
            row = MissionTask(
                mission_id=mission.id,
                task_key=task.id,
                task_type=task.task_type,
                source="manual" if task.task_type == "inspection" else "generated",
                geometry=geojson_to_geography(mapping(geom_lonlat)),
                priority=task.priority,
                required_capability=task.required_capability,
                length_m=task.service_distance_m,
                hover_time_s=task.hover_time_s,
                status="assigned" if task.id in result.assignments else "unassigned",
                unassigned_reason=result.unassigned.get(task.id),
            )
            self.db.add(row)
            task_rows[task.id] = row

        coverage_tasks = [t for t in ctx.tasks if t.task_type in AREA_TASK_TYPES]

        def coverage(task_ids: list[str]) -> float:
            # Coverage per sensor pass, averaged: every required sensor must image the AOI.
            sensors = ctx.sensors.required_sensors
            values = []
            for sensor in sensors:
                segs = [ctx.task_geometry[t.id] for t in coverage_tasks if t.required_capability == sensor and t.id in task_ids]
                swath = next((ctx.task_swath[t.id] for t in coverage_tasks if t.required_capability == sensor), 0.0)
                values.append(coverage_fraction(ctx.aoi_local, segs, swath))
            return 100.0 * sum(values) / len(values) if values else 0.0

        total_wh = 0.0
        plan = MissionPlan(
            mission_id=mission.id,
            algorithm=result.algorithm,
            trigger=trigger,
            is_current=True,
            converged=result.converged,
            timed_out=result.timed_out,
            iterations=result.iterations,
            consensus_rounds=result.consensus_rounds,
            messages_exchanged=result.messages_exchanged,
            convergence_time_ms=round(result.convergence_time_s * 1000, 3),
            drone_count=len(ctx.agents),
            task_count=len(ctx.tasks),
            assigned_count=result.assigned_count,
            unassigned_count=len(result.unassigned),
            total_distance_m=round(result.total_distance_m, 2),
            estimated_duration_s=round(result.makespan_s, 2),
            coverage_pct=round(coverage(list(result.assignments)), 2),
            max_coverage_pct=round(coverage([t.id for t in coverage_tasks]), 2),
            average_bid=round(result.average_bid(), 4),
            params={
                "config_hash": self.config_hash(mission),
                "flight": ctx.flight.model_dump(mode="json"),
                "constraints": ctx.constraints.model_dump(mode="json"),
                "required_sensors": ctx.sensors.required_sensors,
            },
            diagnostics={},
            created_by=user.id if user else None,
        )
        self.db.add(plan)
        self.db.flush()

        agent_state: dict[str, Any] = {}
        ordered_agents = sorted(ctx.agents, key=lambda a: (a.name, a.id))
        for color_index, agent in enumerate(ordered_agents):
            agent_plan = result.agent_plans.get(agent.id)
            if agent_plan is None:
                continue
            agent_state[agent.id] = {
                "bundle": agent_plan.bundle,
                "path": [[s.task_id, s.orientation, s.bid] for s in agent_plan.path],
            }
            if not agent_plan.path:
                continue
            drone = ctx.drones[agent.id]
            waypoints = [list(to_lonlat(x, y)) for x, y in agent_plan.waypoints]
            wh = agent_plan.energy_pct / 100.0 * BATTERY_CAPACITY_WH.get(drone.vehicle_type, 300.0)
            total_wh += wh
            bundle = TaskBundle(
                mission_id=mission.id,
                plan_id=plan.id,
                drone_id=drone.id,
                status="assigned",
                color_index=color_index,
                task_count=len(agent_plan.path),
                path=[s.task_id for s in agent_plan.path],
                waypoints=waypoints,
                route=geojson_to_geography({"type": "LineString", "coordinates": waypoints}) if len(waypoints) >= 2 else None,
                distance_m=round(agent_plan.distance_m, 2),
                eta_seconds=round(agent_plan.duration_s, 2),
                energy_pct=round(agent_plan.energy_pct, 3),
                battery_start_pct=round(agent_plan.battery_start_pct, 2),
                battery_end_pct=round(agent_plan.battery_end_pct, 2),
                coverage_pct=round(coverage([s.task_id for s in agent_plan.path]), 2),
            )
            self.db.add(bundle)
            self.db.flush()
            for order, step in enumerate(agent_plan.path):
                self.db.add(
                    TaskAssignment(
                        plan_id=plan.id,
                        bundle_id=bundle.id,
                        task_id=task_rows[step.task_id].id,
                        drone_id=drone.id,
                        bid=round(step.bid, 6),
                        assignment_order=order,
                        eta_seconds=round(step.eta_s, 2),
                        energy_pct=round(step.energy_pct, 3),
                    )
                )

        plan.estimated_energy_wh = round(total_wh, 2)
        plan.diagnostics = {
            "history": result.history,
            "bundle_sizes": {ctx.drones[a].name: n for a, n in result.bundle_sizes().items()},
            "unassigned": result.unassigned,
            "excluded_drones": ctx.excluded,
            "warnings": ctx.warnings,
            "coverage": ctx.coverage_meta,
            "agent_state": agent_state,
            "wall_time_ms": round(elapsed_ms, 2),
            "communication": "fully_connected",
        }
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def _result_from_plan(self, plan: MissionPlan) -> AllocationResult:
        """Rebuild the algorithm-level result of a stored plan (for warm starts)."""
        state = (plan.diagnostics or {}).get("agent_state", {})
        agent_plans: dict[str, AgentPlan] = {}
        assignments: dict[str, str] = {}
        bids: dict[str, float] = {}
        for agent_id, entry in state.items():
            path = [PathStep(task_id=key, orientation=int(orient), bid=float(bid)) for key, orient, bid in entry["path"]]
            agent_plans[agent_id] = AgentPlan(agent_id=agent_id, path=path, bundle=list(entry["bundle"]))
            for step in path:
                assignments[step.task_id] = agent_id
                bids[step.task_id] = step.bid
        return AllocationResult(
            algorithm=plan.algorithm,
            converged=plan.converged,
            timed_out=plan.timed_out,
            iterations=plan.iterations,
            consensus_rounds=plan.consensus_rounds,
            messages_exchanged=plan.messages_exchanged,
            convergence_time_s=plan.convergence_time_ms / 1000,
            assignments=assignments,
            winning_bids=bids,
            agent_plans=agent_plans,
            unassigned={},
        )

    # =================================================================
    # Output
    # =================================================================

    def plan_out(self, mission: Mission, plan: MissionPlan) -> PlanOut:
        tasks = self.db.execute(select(MissionTask).where(MissionTask.mission_id == mission.id).order_by(MissionTask.task_key)).scalars().all()
        bundles = self.db.execute(
            select(TaskBundle).where(TaskBundle.plan_id == plan.id).order_by(TaskBundle.color_index)
        ).scalars().all()
        assignments = self.db.execute(
            select(TaskAssignment).where(TaskAssignment.plan_id == plan.id).order_by(TaskAssignment.assignment_order)
        ).scalars().all()
        drone_names = {d.id: d.name for d in self.db.execute(select(Drone).where(Drone.organization_id == mission.organization_id)).scalars()}
        key_of = {t.id: t.task_key for t in tasks}
        drone_of_task = {a.task_id: a.drone_id for a in assignments}
        by_bundle: dict[uuid.UUID, list[TaskAssignment]] = {}
        for a in assignments:
            by_bundle.setdefault(a.bundle_id, []).append(a)
        reserve = float(plan.params.get("flight", {}).get("rth_battery_pct", 20))

        return PlanOut(
            id=plan.id,
            mission_id=mission.id,
            algorithm=plan.algorithm,
            trigger=plan.trigger,
            converged=plan.converged,
            timed_out=plan.timed_out,
            iterations=plan.iterations,
            consensus_rounds=plan.consensus_rounds,
            messages_exchanged=plan.messages_exchanged,
            convergence_time_ms=plan.convergence_time_ms,
            drone_count=plan.drone_count,
            task_count=plan.task_count,
            assigned_count=plan.assigned_count,
            unassigned_count=plan.unassigned_count,
            total_distance_m=plan.total_distance_m,
            estimated_duration_s=plan.estimated_duration_s,
            estimated_energy_wh=plan.estimated_energy_wh,
            coverage_pct=plan.coverage_pct,
            max_coverage_pct=plan.max_coverage_pct,
            average_bid=plan.average_bid,
            stale=self.is_stale(mission, plan),
            params={k: v for k, v in plan.params.items()},
            diagnostics={k: v for k, v in plan.diagnostics.items() if k != "agent_state"},
            bundles=[
                BundleOut(
                    drone_id=b.drone_id,
                    drone_name=drone_names.get(b.drone_id, "unknown"),
                    color_index=b.color_index,
                    task_count=b.task_count,
                    path=list(b.path),
                    waypoints=list(b.waypoints),
                    distance_m=b.distance_m,
                    eta_seconds=b.eta_seconds,
                    energy_pct=b.energy_pct,
                    battery_start_pct=b.battery_start_pct,
                    battery_end_pct=b.battery_end_pct,
                    reserve_pct=reserve,
                    battery_warning=b.battery_end_pct < reserve + BATTERY_MARGIN_PCT,
                    coverage_pct=b.coverage_pct,
                    tasks=[
                        BundleTaskOut(
                            task_key=key_of.get(a.task_id, "?"),
                            order=a.assignment_order,
                            eta_seconds=a.eta_seconds,
                            bid=a.bid,
                            energy_pct=a.energy_pct,
                        )
                        for a in by_bundle.get(b.id, [])
                    ],
                )
                for b in bundles
            ],
            tasks=[
                PlanTaskOut(
                    task_key=t.task_key,
                    task_type=t.task_type,
                    source=t.source,
                    geometry=geography_to_geojson(t.geometry) or {},
                    priority=t.priority,
                    required_capability=t.required_capability,
                    length_m=t.length_m,
                    status=t.status,
                    assigned_drone_id=drone_of_task.get(t.id),
                    unassigned_reason=t.unassigned_reason,
                )
                for t in tasks
            ],
            created_at=plan.created_at,
        )

    # =================================================================
    # Validation & readiness
    # =================================================================

    def validate(self, mission: Mission, user: User | None, transition: bool = True, ip_address: str | None = None) -> dict[str, Any]:
        """Wizard Step 6: plan if needed, then run every safety/feasibility check.

        Persists the report on the mission and (when `transition`) moves
        PLANNING -> VALIDATING. Always recomputed -- never trusts a cached report.
        """
        checks: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        plan: MissionPlan | None = None
        missing = self.missing_steps(mission)

        if mission.aoi_geometry is None:
            checks.append(_check("AOI_MISSING", "error", "Draw an area of interest (step 2)"))
        if missing:
            checks.append(_check("WIZARD_INCOMPLETE", "error", f"Complete wizard steps {missing}", steps=missing))

        if not missing and mission.aoi_geometry is not None:
            plan = self.current_plan(mission)
            if plan is None or self.is_stale(mission, plan):
                try:
                    plan = self._plan(mission, user, trigger="validate", algorithm=plan.algorithm if plan else None)
                except PlanningError as exc:
                    checks.append(_check(exc.code, "error", exc.message))
                    plan = None

        if plan is not None:
            checks.extend(self._plan_checks(mission, plan))
            conflicts = self._spatial_conflicts(mission, plan)
            for conflict in conflicts:
                checks.append(self._conflict_check(conflict))
                if conflict["kind"] == "nfz":
                    log_event(logger, "mission.nfz_conflict", logging.WARNING, mission_id=mission.id, scope=conflict["scope"], zone=conflict["geofence_name"])

        passed = not any(c["severity"] == "error" for c in checks)
        if passed:
            checks.append(_check("READY_TO_ARM", "info", "All pre-arm checks passed"))
        report = {
            "passed": passed,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "plan_id": str(plan.id) if plan else None,
            "checks": checks,
            "conflicts": conflicts,
            "nfz_conflict": any(c["kind"] == "nfz" for c in conflicts),
        }
        mission.validation_report = report
        mission.validated_at = datetime.now(timezone.utc)
        self.db.commit()
        log_event(
            logger,
            "mission.validated",
            logging.INFO if passed else logging.WARNING,
            mission_id=mission.id,
            passed=passed,
            errors=sum(1 for c in checks if c["severity"] == "error"),
            nfz_conflict=report["nfz_conflict"],
        )
        if transition and mission.status == "planning":
            MissionStateService(self.db).transition(mission, "validating", user, "Wizard step 6 validation run", ip_address)
        return report

    def _plan_checks(self, mission: Mission, plan: MissionPlan) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        diag = plan.diagnostics or {}
        constraints = mission.config.get("constraints", {})
        for drone in diag.get("excluded_drones", []):
            checks.append(_check("DRONE_UNAVAILABLE", "warning", f"{drone['drone_name']} excluded: {drone['reason']}", **drone))
        if plan.drone_count == 0:
            checks.append(_check("NO_DRONES_AVAILABLE", "error", "No selected drone is available and above the battery minimum"))
        if "AOI_NARROWER_THAN_LANE" in diag.get("warnings", []):
            checks.append(_check("AOI_NARROWER_THAN_LANE", "warning", "AOI is narrower than one lane width; single-drone mode suggested"))
        if plan.timed_out:
            checks.append(_check("CBBA_TIMEOUT", "error", f"CBBA hit its time limit after {plan.iterations} iterations"))
        elif not plan.converged:
            checks.append(_check("CBBA_NOT_CONVERGED", "error", f"CBBA failed to converge after {plan.iterations} iterations"))
        if plan.task_count > 0 and plan.assigned_count == 0 and plan.drone_count > 0:
            checks.append(_check("NO_FEASIBLE_ASSIGNMENT", "error", "No task could be assigned to any drone under the current constraints"))
        if plan.unassigned_count:
            reasons = diag.get("unassigned", {})
            severity = "warning" if constraints.get("accept_unassigned") else "error"
            checks.append(
                _check(
                    "TASKS_UNASSIGNED",
                    severity,
                    f"{plan.unassigned_count} of {plan.task_count} tasks have no feasible drone"
                    + (" (accepted)" if severity == "warning" else "; adjust constraints or accept unassigned tasks"),
                    reasons=sorted(set(reasons.values())),
                    tasks=sorted(reasons),
                )
            )
        reserve = float(plan.params.get("flight", {}).get("rth_battery_pct", 20))
        bundles = self.db.execute(select(TaskBundle).where(TaskBundle.plan_id == plan.id)).scalars().all()
        drones = {d.id: d for d in self.db.execute(select(Drone).where(Drone.id.in_([b.drone_id for b in bundles]))).scalars()} if bundles else {}
        live_constraints = ConstraintsStep.model_validate(constraints) if constraints else None
        for bundle in bundles:
            name = drones[bundle.drone_id].name if bundle.drone_id in drones else "drone"
            # The plan snapshot may be minutes old: re-check each assigned drone now.
            drone = drones.get(bundle.drone_id)
            reason = "not_found" if drone is None else (self._ineligibility(drone, live_constraints) if live_constraints else None)
            if reason:
                checks.append(_check("ASSIGNED_DRONE_UNAVAILABLE", "error", f"{name} has assigned tasks but is now unavailable ({reason}); re-allocate", drone_id=str(bundle.drone_id), reason=reason))
            if bundle.battery_end_pct < reserve:
                checks.append(_check("BATTERY_CONSTRAINT", "error", f"{name} would land at {bundle.battery_end_pct:.0f}% (< {reserve:.0f}% reserve)"))
            elif bundle.battery_end_pct < reserve + BATTERY_MARGIN_PCT:
                checks.append(_check("BATTERY_MARGINAL", "warning", f"{name} lands with only {bundle.battery_end_pct:.0f}% battery"))
        # Payload calibration of drones that actually fly.
        if bundles:
            for payload in self.db.execute(select(Payload).where(Payload.drone_id.in_([b.drone_id for b in bundles]))).scalars():
                if payload.status in ("due", "uncalibrated") and payload.type in plan.params.get("required_sensors", []):
                    owner = drones.get(payload.drone_id)
                    checks.append(_check("PAYLOAD_CALIBRATION", "warning", f"{owner.name if owner else 'drone'}: {payload.name} is {payload.status}"))
        return checks

    def _spatial_conflicts(self, mission: Mission, plan: MissionPlan) -> list[dict[str, Any]]:
        bundles = self.db.execute(select(TaskBundle).where(TaskBundle.plan_id == plan.id)).scalars().all()
        routes = {b.drone_id: {"type": "LineString", "coordinates": b.waypoints} for b in bundles if len(b.waypoints) >= 2}
        launch = {b.drone_id: {"type": "Point", "coordinates": b.waypoints[0]} for b in bundles if b.waypoints}
        constraints = mission.config.get("constraints", {})
        zone_ids = [uuid.UUID(z) for z in constraints.get("geofence_ids", [])]
        altitude = mission.config.get("flight", {}).get("altitude_m")
        validator = SpatialValidator(self.db, mission.organization_id)
        found = validator.mission_conflicts(geography_to_geojson(mission.aoi_geometry), routes, launch, zone_ids, altitude)
        return [c.as_dict() for c in found]

    @staticmethod
    def _conflict_check(conflict: dict[str, Any]) -> dict[str, Any]:
        kind, scope, name = conflict["kind"], conflict["scope"], conflict["geofence_name"]
        if kind == "nfz":
            text = {"aoi": "AOI intersects", "route": "Planned route crosses", "launch": "Launch point is inside"}[scope]
            return _check("NFZ_CONFLICT", "error", f"⚠ NFZ CONFLICT: {text} no-fly zone '{name}'", **conflict)
        if kind == "exclusion":
            text = {"aoi": "AOI intersects", "route": "Planned route crosses", "launch": "Launch point is inside"}[scope]
            return _check("EXCLUSION_CONFLICT", "error", f"{text} exclusion zone '{name}'", **conflict)
        if kind == "inclusion":
            return _check("INCLUSION_VIOLATION", "error", f"{'AOI' if scope == 'aoi' else 'Planned route'} leaves the selected inclusion zone(s)", **conflict)
        return _check("ALTITUDE_CEILING", "error", f"Planned altitude exceeds the ceiling of '{name}'", **conflict)

    def readiness_checks(self, mission: Mission) -> list[dict[str, Any]]:
        """ARMED -> ACTIVE: are the drones that will fly actually ready right now?"""
        checks: list[dict[str, Any]] = []
        plan = self.current_plan(mission)
        if plan is None:
            return [_check("CBBA_NOT_RUN", "error", "Mission has no allocation plan")]
        if self.is_stale(mission, plan):
            checks.append(_check("PLAN_STALE", "error", "Mission parameters changed after planning; re-validate"))
        constraints = ConstraintsStep.model_validate(mission.config.get("constraints", {}))
        bundles = self.db.execute(select(TaskBundle).where(TaskBundle.plan_id == plan.id)).scalars().all()
        if not bundles:
            checks.append(_check("NO_DRONES_ASSIGNED", "error", "No drone has assigned tasks"))
        max_age = settings.MISSION_TELEMETRY_MAX_AGE_SECONDS
        now = datetime.now(timezone.utc)
        validator = SpatialValidator(self.db, mission.organization_id)
        reserve = float(plan.params.get("flight", {}).get("rth_battery_pct", 20))
        for bundle in bundles:
            drone = self.db.get(Drone, bundle.drone_id) if bundle.drone_id else None
            if drone is None or not drone.active:
                checks.append(_check("DRONE_UNAVAILABLE", "error", "An assigned drone no longer exists or was deactivated"))
                continue
            if drone.status not in ELIGIBLE_DRONE_STATUSES:
                checks.append(_check("DRONE_UNAVAILABLE", "error", f"{drone.name} is {drone.status}", drone_id=str(drone.id)))
            if drone.gps_status in ("NO_FIX", "GPS_LOST"):
                checks.append(_check("GPS_NOT_READY", "error", f"{drone.name} has no GPS fix", drone_id=str(drone.id)))
            if drone.last_telemetry_at is None or (now - drone.last_telemetry_at).total_seconds() > max_age:
                checks.append(_check("TELEMETRY_STALE", "error", f"{drone.name} has no telemetry in the last {max_age:.0f} s", drone_id=str(drone.id)))
            if drone.battery < constraints.min_battery_pct:
                checks.append(_check("BATTERY_LOW", "error", f"{drone.name} battery {drone.battery:.0f}% < {constraints.min_battery_pct:g}% minimum", drone_id=str(drone.id)))
            elif drone.battery - bundle.energy_pct < reserve:
                checks.append(_check("BATTERY_CONSTRAINT", "error", f"{drone.name} cannot finish its bundle above the {reserve:.0f}% reserve", drone_id=str(drone.id)))
            location = geography_to_geojson(drone.location)
            if location is not None and validator.covering(location, ["nfz"]):
                checks.append(_check("NFZ_CONFLICT", "error", f"⚠ NFZ CONFLICT: {drone.name} is inside a no-fly zone", drone_id=str(drone.id)))
        return checks
