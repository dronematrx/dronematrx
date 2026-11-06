"""Auto Mission: the operator gives an AOI, a mission type and a number of
drones; everything else is derived.

    plan     -> create the mission, apply the template, save the AOI, choose
                the drones (N best, never mixing simulated and real aircraft),
                fit altitude to the aircraft limits with per-drone vertical
                separation, generate the inclusion geofence from the AOI
                (real aircraft), set constraints, run CBBA + validation.
    launch   -> simulation: session, world, preflight, start.
                real aircraft: ONE human approval (the same 5 safety
                acknowledgements) for the whole fleet -> per-drone flight
                permission, mission upload, staggered MISSION_START.
    status   -> per-drone progress from live telemetry; a drone that drops
                out before finishing (pilot takeover, failsafe RTL/LAND, lost
                link) is reported with its unfinished tasks.
    reassign -> one click sends the unfinished tasks to a ready replacement
                drone on the ground (in-flight missions are never rewritten).

It drives the same services as the manual wizard and Mission Control, so every
existing rule (wizard validation, NFZ checks, pre-arm checklist, flight
permission, hard limits, audit log) still applies.
"""
from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.drone import Drone
from app.models.geofence import Geofence
from app.models.mission import Mission
from app.models.user import User
from app.schemas.mission import MissionCreate
from app.services.mission_control import fc as fcsvc
from app.services.mission_control import permissions as permsvc
from app.services.mission_control.hwbridge import HwBridgeError, parse_identity
from app.services.mission_planning_service import MissionPlanningService, PlanningError
from app.services.mission_service import MissionService
from app.services.mission_state_service import MissionStateService
from app.services.mission_templates import TEMPLATES

logger = logging.getLogger("dronematrx.auto_mission")

HW_ALT_MARGIN_M = 5.0      # stay this far below the bridge's hard altitude limit
HW_ALT_STEP_M = 5.0        # vertical separation between real drones
HW_MAX_SPEED_MPS = 8.0
MIN_ALT_M = 10.0           # FlightParams minimum
GEOFENCE_BUFFER_M = 30.0   # auto inclusion fence = AOI + this margin
STAGGER_S = 10.0           # real drones take off this far apart
TELEMETRY_FRESH_S = 5.0


class AutoMissionError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, extra: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_s(live: Any) -> float | None:
    ts = getattr(live, "timestamp", None)
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (_now() - ts).total_seconds()


def hardware_limits() -> dict[str, float]:
    try:
        lim = fcsvc.hw_client().health().get("limits") or {}
    except HwBridgeError:
        lim = {}
    return {"max_alt_m": float(lim.get("max_alt_m") or 30.0), "max_distance_m": float(lim.get("max_distance_m") or 150.0)}


def altitude_plan(count: int, template_alt: float, max_alt: float) -> list[float]:
    """Per-drone altitudes HW_ALT_STEP_M apart. The lowest drone flies at the
    template's altitude when the stack fits under max_alt - margin; otherwise
    the stack is lowered to fit. Raises when it cannot fit above MIN_ALT_M."""
    top = min(template_alt + HW_ALT_STEP_M * (count - 1), max_alt - HW_ALT_MARGIN_M)
    base = top - HW_ALT_STEP_M * (count - 1)
    if base < MIN_ALT_M:
        fit = int((max_alt - HW_ALT_MARGIN_M - MIN_ALT_M) // HW_ALT_STEP_M) + 1
        raise AutoMissionError("TOO_MANY_DRONES", f"{count} real drones need {HW_ALT_STEP_M:g} m of vertical separation each, but the "
                               f"{max_alt:g} m altitude limit fits at most {fit}. Use fewer drones or raise the bridge's max_alt_m.", 422)
    return [round(base + HW_ALT_STEP_M * i, 1) for i in range(count)]


# ------------------------------------------------------------------ selection


def hardware_candidates(db: Session, org_id: uuid.UUID, required: list[str], live_by_id: dict[str, Any]) -> tuple[list[tuple[Drone, float]], list[dict]]:
    """Ready real aircraft (best battery first) and why the others are not."""
    s = get_settings()
    planning = MissionPlanningService(db)
    drones = db.execute(select(Drone).where(Drone.organization_id == org_id, Drone.active.is_(True), Drone.hardware.is_(True))
                        .order_by(Drone.name)).scalars().all()
    payloads = planning._functional_payloads([d.id for d in drones]) if drones else {}
    ready: list[tuple[Drone, float]] = []
    rejected: list[dict] = []
    fcs = aircraft_state(db, [str(d.id) for d in drones])
    for d in drones:
        b = fcsvc.active_binding(db, d.id)
        live = live_by_id.get(str(d.id))
        fc = fcs.get(str(d.id)) or {}
        age = _age_s(live)
        battery = float(getattr(live, "battery", 0) or 0) if live is not None else 0.0
        have = {p.type for p in payloads.get(d.id, [])}
        reason = None
        if b is None or b.state != "bound":
            reason = "not linked to a flight controller (Drone Setup step 12)"
        elif live is None or age is None or age > TELEMETRY_FRESH_S:
            reason = "no live telemetry (powered on? radio connected?)"
        elif getattr(live, "armed", False) or fc.get("armed"):
            reason = "already armed / flying"
        elif fc.get("override") is not None:
            reason = "pilot override lock: release it in Mission Control after the pilot hands back control"
        elif fc.get("mode") in ("RTL", "LAND", "AUTO", "BRAKE", "SMART_RTL"):
            reason = f"in {fc['mode']} mode: the pilot sets the mode switch to LOITER or STABILIZE"
        elif battery < s.HW_PREARM_MIN_BATTERY_PCT:
            reason = f"battery {battery:.0f} % (needs {s.HW_PREARM_MIN_BATTERY_PCT:.0f} %)"
        elif not set(required) <= have:
            reason = f"no working {', '.join(sorted(set(required) - have))} payload"
        if reason:
            rejected.append({"drone_id": str(d.id), "name": d.name, "reason": reason})
        else:
            ready.append((d, battery))
    ready.sort(key=lambda x: -x[1])
    return ready, rejected


def _auto_geofence(db: Session, mission: Mission, user: User) -> Geofence:
    """Inclusion fence = the AOI plus a margin (pre-arm needs one at the field)."""
    wkt = db.scalar(select(func.ST_AsText(func.ST_Buffer(mission.aoi_geometry, GEOFENCE_BUFFER_M))))
    g = Geofence(organization_id=mission.organization_id, name=f"Auto: {mission.name}"[:255], type="inclusion",
                 geometry=f"SRID=4326;{wkt}", altitude_min=0, altitude_max=120, active=True)
    db.add(g)
    db.add(AuditLog(user_id=user.id, action="geofence_auto_created", resource="geofence", resource_id=str(g.id),
                    event_metadata={"mission_id": str(mission.id), "buffer_m": GEOFENCE_BUFFER_M}))
    db.commit()
    db.refresh(g)
    return g


# ----------------------------------------------------------------------- plan


def plan(db: Session, user: User, *, name: str | None, template: str, aoi: dict, drone_count: int, mode: str,
         live_by_id: dict[str, Any], ip: str | None = None) -> dict:
    if template not in TEMPLATES:
        raise AutoMissionError("TEMPLATE", f"Unknown mission type {template}", 422)
    if mode not in ("simulation", "hardware"):
        raise AutoMissionError("MODE", "Mode must be simulation or hardware", 422)
    if not 1 <= drone_count <= 16:
        raise AutoMissionError("DRONE_COUNT", "Choose 1-16 drones", 422)
    tpl = TEMPLATES[template]
    required = list(tpl.required_sensors)

    # Real aircraft: check readiness BEFORE creating anything.
    chosen: list[Drone] = []
    altitudes: list[float] = []
    limits = None
    if mode == "hardware":
        ready, rejected = hardware_candidates(db, user.organization_id, required, live_by_id)
        if len(ready) < drone_count:
            raise AutoMissionError("NOT_ENOUGH_DRONES", f"{drone_count} real drone(s) requested but {len(ready)} ready", 409,
                                   {"ready": [d.name for d, _ in ready], "not_ready": rejected})
        chosen = [d for d, _ in ready[:drone_count]]
        # CBBA plans from each drone's stored position: use where it is right now.
        for d in chosen:
            lv = live_by_id.get(str(d.id))
            if lv is not None and getattr(lv, "latitude", None) is not None:
                d.location = f"SRID=4326;POINT({lv.longitude} {lv.latitude})"
        db.commit()
        limits = hardware_limits()
        altitudes = altitude_plan(drone_count, float(tpl.flight["altitude_m"]), limits["max_alt_m"])

    label = name or f"{tpl.label} {_now().strftime('%d %b %H:%M')}"
    mission = MissionService(db).create_mission(user.organization_id, user.id, MissionCreate(
        name=label[:255], mission_type=tpl.mission_type, status="draft", execution_mode="simulation" if mode == "simulation" else "live"))
    planning = MissionPlanningService(db)
    try:
        planning.save_step(mission, 1, {"template": template, "name": label[:255]}, user, ip)
        planning.save_step(mission, 2, {"source": "drawn", "geometry": aoi}, user, ip)
        flight = dict((mission.config or {}).get("flight") or tpl.flight)
        if mode == "hardware":
            flight["altitude_m"] = altitudes[0]
            flight["speed_mps"] = min(float(flight.get("speed_mps", 5)), HW_MAX_SPEED_MPS)
        planning.save_step(mission, 3, flight, user, ip)
        # The per-drone task cap is a manual-wizard knob: the operator here only gives
        # area, type and count, and a lower altitude (real aircraft) means more lanes.
        # CBBA still enforces the real limit, each drone's energy / flight time.
        constraints: dict[str, Any] = {"link_loss_policy": "RTH", "accept_unassigned": False, "max_tasks_per_drone": 60}
        geofence = None
        if mode == "simulation":
            from app.services.simulation.orchestrator import SimulationService, SimulationError

            try:
                staged = SimulationService(db).stage_fleet(mission, user, drone_count, ip)
            except SimulationError as exc:
                raise AutoMissionError(exc.code, exc.message, getattr(exc, "status", 409)) from exc
            chosen = list(staged["drones"])
            constraints["min_battery_pct"] = 40
        else:
            planning.save_step(mission, 4, {"required_sensors": required, "drone_ids": [str(d.id) for d in chosen]}, user, ip)
            geofence = _auto_geofence(db, mission, user)
            constraints.update(min_battery_pct=get_settings().HW_PREARM_MIN_BATTERY_PCT, geofence_ids=[str(geofence.id)])
        planning.save_step(mission, 5, constraints, user, ip)
        report = planning.validate(mission, user, transition=True, ip_address=ip)
    except PlanningError as exc:
        raise AutoMissionError(exc.code, exc.message, 422, {"mission_id": str(mission.id)}) from exc

    config = dict(mission.config or {})
    config["auto"] = {
        "mode": mode, "template": template, "drone_count": drone_count, "created_at": _now().isoformat(),
        "drones": {str(d.id): {"name": d.name, **({"altitude_m": altitudes[i]} if mode == "hardware" else {})} for i, d in enumerate(chosen)},
        "geofence_id": str(geofence.id) if geofence else None, "limits": limits, "launch": None,
    }
    mission.config = config
    db.commit()
    db.add(AuditLog(user_id=user.id, action="auto_mission_planned", resource="mission", resource_id=str(mission.id),
                    event_metadata={"mode": mode, "template": template, "drone_count": drone_count, "drones": [d.name for d in chosen],
                                    "passed": report["passed"]}))
    db.commit()
    return summary(db, mission, report)


def summary(db: Session, mission: Mission, report: dict | None = None) -> dict:
    planning = MissionPlanningService(db)
    plan_row = planning.current_plan(mission)
    plan_out = planning.plan_out(mission, plan_row).model_dump(mode="json") if plan_row else None
    auto = (mission.config or {}).get("auto") or {}
    return {"mission_id": str(mission.id), "name": mission.name, "status": mission.status, "mode": auto.get("mode"),
            "template": auto.get("template"), "drones": auto.get("drones", {}), "geofence_id": auto.get("geofence_id"),
            "flight": (mission.config or {}).get("flight"), "limits": auto.get("limits"),
            "validation": report or mission.validation_report, "plan": plan_out, "launch": auto.get("launch")}


def _auto_mission(db: Session, user: User, mission_id: uuid.UUID) -> Mission:
    mission = db.get(Mission, mission_id)
    if mission is None or mission.organization_id != user.organization_id:
        raise AutoMissionError("MISSION_NOT_FOUND", "Mission not found", 404)
    if not (mission.config or {}).get("auto"):
        raise AutoMissionError("NOT_AUTO", "This mission was not planned with Auto Mission", 409)
    return mission


def _save_auto(db: Session, mission: Mission, auto: dict) -> None:
    # Nested dicts are edited in place: deep-copy and flag the column, or
    # SQLAlchemy sees "no change" in the JSON and silently skips the UPDATE.
    config = copy.deepcopy(dict(mission.config or {}))
    config["auto"] = copy.deepcopy(auto)
    mission.config = config
    flag_modified(mission, "config")
    db.commit()


# --------------------------------------------------------------------- launch


def launch_simulation(db: Session, user: User, mission_id: uuid.UUID) -> dict:
    """Session + world + preflight; the route then starts the orchestrator."""
    from app.services.simulation.orchestrator import SimulationError, SimulationService

    mission = _auto_mission(db, user, mission_id)
    auto = copy.deepcopy(mission.config["auto"])
    if auto["mode"] != "simulation":
        raise AutoMissionError("MODE", "This is a real-aircraft mission", 409)
    svc = SimulationService(db)
    try:
        sess = svc.create_session(mission, user, {"speed": 1.0, "auto_reallocate": True})
        svc.generate_world(sess)
        pre = svc.preflight(sess)
    except SimulationError as exc:
        raise AutoMissionError(exc.code, exc.message, getattr(exc, "status", 409)) from exc
    if not pre.get("passed"):
        failed = [c for c in pre.get("checks", []) if not c["passed"]]
        raise AutoMissionError("PREFLIGHT_FAILED", "Simulation preflight failed: " + "; ".join(c["message"] for c in failed), 409,
                               {"session_id": str(sess.id), "checks": failed})
    auto["launch"] = {"session_id": str(sess.id), "launched_at": _now().isoformat(), "by": user.email}
    _save_auto(db, mission, auto)
    return {"session_id": str(sess.id), "mission_id": str(mission.id)}


def launch_hardware(db: Session, mc: Any, live_by_id: dict[str, Any], user: User, mission_id: uuid.UUID, acks: dict, minutes: int) -> dict:
    """One human approval for the whole fleet. All-or-nothing up to the first
    take-off: if any drone fails pre-arm, permission or upload, the granted
    permissions are revoked and nothing flies."""
    mission = _auto_mission(db, user, mission_id)
    auto = copy.deepcopy(mission.config["auto"])
    if auto["mode"] != "hardware":
        raise AutoMissionError("MODE", "This is a simulation mission; launch it as a simulation", 409)
    if auto.get("launch") and auto["launch"].get("status") in ("launching", "flying"):
        raise AutoMissionError("ALREADY_LAUNCHED", "This mission is already flying", 409)
    missing = [k for k in permsvc.REQUIRED_ACKS if acks.get(k) is not True]
    if missing:
        raise AutoMissionError("ACKNOWLEDGEMENTS_MISSING", "All safety acknowledgements are required: " + ", ".join(missing), 422)
    try:
        MissionStateService(db).transition(mission, "armed", user, "Auto Mission launch approved")
    except Exception as exc:  # TransitionBlockedError: validation failed now
        raise AutoMissionError("VALIDATION_FAILED", f"Mission validation failed at launch: {exc}", 409) from exc

    drones = {d.id: d for d in db.execute(select(Drone).where(Drone.id.in_([uuid.UUID(x) for x in auto["drones"]]))).scalars()}
    order = sorted(auto["drones"].items(), key=lambda kv: kv[1].get("altitude_m", 0))
    granted: list[uuid.UUID] = []
    uploads: dict[str, dict] = {}
    problems: list[dict] = []
    for did, meta in order:
        drone = drones.get(uuid.UUID(did))
        if drone is None:
            problems.append({"drone": meta["name"], "error": "drone no longer exists"})
            continue
        try:
            pre = mc.commands.prearm(db, drone, "hardware")
            permsvc.grant(db, user, drone.id, minutes, acks, pre, mission.id, note=f"Auto Mission {mission.name}")
            granted.append(drone.id)
            up = permsvc.upload_mission(db, user, drone.id, mission.id, live_by_id.get(did), meta["altitude_m"], None)
            uploads[did] = {"name": drone.name, "altitude_m": meta["altitude_m"], "tasks": up["tasks"], "seq_map": up["seq_map"],
                            "status": "uploaded"}
        except permsvc.FlightPermissionError as exc:
            problems.append({"drone": drone.name, "code": exc.code, "error": exc.message,
                             "blocking": (exc.extra or {}).get("prearm", {}).get("blocking")})
    if problems:
        for drone_id in granted:
            try:
                permsvc.revoke(db, user, drone_id, "Auto Mission launch cancelled: another drone was not ready")
            except Exception:  # noqa: BLE001 - best effort; the permission also expires
                logger.warning("revoke failed for %s", drone_id)
        try:
            MissionStateService(db).transition(mission, "validating", user, "Auto Mission launch cancelled")
        except Exception:  # noqa: BLE001
            pass
        raise AutoMissionError("LAUNCH_BLOCKED", "Nothing was launched: " + "; ".join(f"{p['drone']}: {p['error']}" for p in problems), 409,
                               {"problems": problems})

    auto["launch"] = {"status": "launching", "launched_at": _now().isoformat(), "by": user.email, "stagger_s": STAGGER_S,
                      "drones": uploads, "replacements": []}
    _save_auto(db, mission, auto)
    try:
        MissionStateService(db).transition(mission, "active", user, "Auto Mission: real aircraft launching")
    except Exception as exc:  # noqa: BLE001 - readiness warnings must not stop drones already approved
        logger.warning("mission %s not moved to active: %s", mission.id, exc)

    # Staggered MISSION_START through the normal command path (audited, confirmed by telemetry).
    starts = [did for did, _ in order]

    def start_one(did: str) -> None:
        try:
            mc.commands.execute(user, uuid.UUID(did), "MISSION_START", {}, "hardware", f"auto-{mission.id.hex[:8]}-{did[:8]}", time.perf_counter())
        except Exception as exc:  # noqa: BLE001 - shown in status via telemetry and the command log
            logger.warning("auto mission %s: MISSION_START %s failed: %s", mission.id, did, exc)

    start_one(starts[0])
    for i, did in enumerate(starts[1:], start=1):
        t = threading.Timer(STAGGER_S * i, start_one, args=(did,))
        t.daemon = True
        t.start()
    db.add(AuditLog(user_id=user.id, action="auto_mission_launched", resource="mission", resource_id=str(mission.id),
                    event_metadata={"drones": [u["name"] for u in uploads.values()], "minutes": minutes, "stagger_s": STAGGER_S}))
    db.commit()
    return {"mission_id": str(mission.id), "launch": auto["launch"], "start_order": [uploads[d]["name"] for d in starts]}


# --------------------------------------------------------------------- status


def aircraft_state(db: Session, drone_ids: list[str]) -> dict[str, dict]:
    """Straight from the flight controllers (via the bridge): armed, mode,
    pilot override, mission progress. Normalized telemetry drops these."""
    try:
        by_fc = {c["fc_id"]: c for c in fcsvc.hw_client().controllers()}
    except HwBridgeError:
        return {}
    out = {}
    for did in drone_ids:
        b = fcsvc.active_binding(db, uuid.UUID(did))
        hw = parse_identity(b.endpoint) if b is not None and b.state == "bound" else None
        if hw and hw["fc_id"] in by_fc:
            out[did] = by_fc[hw["fc_id"]]
    return out


def _progress(entry: dict, fc: dict | None) -> dict:
    seq_map = {int(k): v for k, v in (entry.get("seq_map") or {}).items()}
    # The bridge uploads a home placeholder at seq 0, so vehicle seq = item index + 1.
    reached = int((fc or {}).get("reached_max", -1))
    done = [v["task_key"] for k, v in seq_map.items() if v.get("role") == "exit" and k + 1 <= reached]
    remaining = [t for t in entry.get("tasks", []) if t not in done]
    return {"done": done, "remaining": remaining}


def status(db: Session, user: User, mission_id: uuid.UUID, live_by_id: dict[str, Any]) -> dict:
    mission = _auto_mission(db, user, mission_id)
    auto = copy.deepcopy(mission.config["auto"])
    launch = auto.get("launch") or {}
    if auto["mode"] != "hardware" or not launch.get("drones"):
        return {**summary(db, mission), "drones_status": []}
    out = []
    fcs = aircraft_state(db, list(launch["drones"]))
    for did, entry in launch["drones"].items():
        fc = fcs.get(did)
        prog = _progress(entry, fc)
        age = (fc or {}).get("heartbeat_age_s")
        mode = (fc or {}).get("mode")
        armed = bool((fc or {}).get("armed"))
        override = (fc or {}).get("override") is not None
        state = entry.get("status", "uploaded")
        if entry.get("reassigned_to"):
            state = "replaced"
        elif not prog["remaining"]:
            state = "completed"
        elif age is None or age > 10:
            state = "dropped" if state in ("flying", "dropped") else "waiting"
            if state == "dropped":
                entry["drop_reason"] = "lost telemetry"
        elif armed and mode == "AUTO" and not override:
            state = "flying"
        elif state in ("flying", "dropped") and (override or mode in ("RTL", "LAND", "LOITER", "ALT_HOLD", "STABILIZE", "BRAKE", "SMART_RTL")):
            state = "dropped"
            entry["drop_reason"] = "pilot took control" if override else f"left the mission ({mode})"
        entry["status"] = state
        out.append({"drone_id": did, "name": entry["name"], "state": state, "mode": mode, "armed": armed, "altitude_m": entry["altitude_m"],
                    "orca_avoiding": bool((fc or {}).get("orca_engaged")),
                    "tasks_total": len(entry.get("tasks", [])), "tasks_done": len(prog["done"]), "remaining": prog["remaining"],
                    "drop_reason": entry.get("drop_reason") if state == "dropped" else None, "reassigned_to": entry.get("reassigned_to")})
    states = {d["state"] for d in out}
    launch["status"] = "completed" if states <= {"completed", "replaced"} else ("flying" if "flying" in states else launch.get("status", "launching"))
    auto = {**auto, "launch": launch}
    _save_auto(db, mission, auto)
    if launch["status"] == "completed" and mission.status == "active":
        try:
            MissionStateService(db).transition(mission, "completed", user, "Auto Mission: every task flown")
        except Exception:  # noqa: BLE001
            pass
    return {**summary(db, mission), "drones_status": out}


# ------------------------------------------------------------------- reassign


def reassign(db: Session, mc: Any, live_by_id: dict[str, Any], user: User, mission_id: uuid.UUID, from_drone_id: uuid.UUID, acks: dict,
             minutes: int) -> dict:
    """Send a dropped drone's unfinished tasks to a ready drone on the ground.
    A person confirms (acknowledgements) because a new aircraft takes off."""
    mission = _auto_mission(db, user, mission_id)
    auto = copy.deepcopy(mission.config["auto"])
    launch = auto.get("launch") or {}
    entry = (launch.get("drones") or {}).get(str(from_drone_id))
    if entry is None:
        raise AutoMissionError("NOT_IN_MISSION", "That drone is not part of this launch", 404)
    remaining = _progress(entry, aircraft_state(db, [str(from_drone_id)]).get(str(from_drone_id)))["remaining"]
    if not remaining:
        raise AutoMissionError("NOTHING_LEFT", f"{entry['name']} has no unfinished tasks", 409)
    in_air = {d for d, e in launch["drones"].items() if e.get("status") in ("flying", "uploaded")}
    ready, rejected = hardware_candidates(db, user.organization_id, list(TEMPLATES[auto["template"]].required_sensors), live_by_id)
    ready = [(d, b) for d, b in ready if str(d.id) not in in_air and str(d.id) != str(from_drone_id)]
    if not ready:
        raise AutoMissionError("NO_REPLACEMENT", "No ready drone on the ground to take over. Land one, or set up a spare.", 409,
                               {"not_ready": rejected})
    drone = ready[0][0]
    # The replacement flies at the dropped drone's altitude: that layer is free now.
    alt = entry["altitude_m"]
    pre = mc.commands.prearm(db, drone, "hardware")
    try:
        permsvc.grant(db, user, drone.id, minutes, acks, pre, mission.id, note=f"Auto Mission replacement for {entry['name']}")
        up = permsvc.upload_mission(db, user, drone.id, mission.id, live_by_id.get(str(drone.id)), alt, None, task_keys=remaining)
    except permsvc.FlightPermissionError as exc:
        raise AutoMissionError(exc.code, f"{drone.name}: {exc.message}", 409) from exc
    launch["drones"][str(drone.id)] = {"name": drone.name, "altitude_m": alt, "tasks": up["tasks"], "seq_map": up["seq_map"],
                                       "status": "uploaded", "replacement_for": entry["name"]}
    entry["reassigned_to"] = drone.name
    launch.setdefault("replacements", []).append({"from": entry["name"], "to": drone.name, "tasks": remaining, "at": _now().isoformat()})
    auto["launch"] = launch
    _save_auto(db, mission, auto)
    mc.commands.execute(user, drone.id, "MISSION_START", {}, "hardware", f"auto-r-{uuid.uuid4().hex[:12]}", time.perf_counter())
    db.add(AuditLog(user_id=user.id, action="auto_mission_reassigned", resource="mission", resource_id=str(mission.id),
                    event_metadata={"from": entry["name"], "to": drone.name, "tasks": remaining}))
    db.commit()
    return {"from": entry["name"], "to": drone.name, "tasks": remaining, "altitude_m": alt}


def counts(db: Session, user: User, live_by_id: dict[str, Any]) -> dict:
    """How many drones Auto Mission could use right now, per mode and template."""
    from app.models.organization import Organization
    from app.services.simulation.fleet import ensure_sim_fleet

    out: dict[str, Any] = {"hardware": {}, "limits": hardware_limits()}
    for key, tpl in TEMPLATES.items():
        ready, rejected = hardware_candidates(db, user.organization_id, list(tpl.required_sensors), live_by_id)
        out["hardware"][key] = {"ready": [d.name for d, _ in ready], "not_ready": rejected}
    try:
        fleet = ensure_sim_fleet(db, db.get(Organization, user.organization_id))
        out["simulation"] = {"available": len(fleet)}
    except Exception as exc:  # noqa: BLE001
        out["simulation"] = {"available": 0, "error": str(exc)}
    return out
