"""Simulation report (generated automatically when a session ends) and the
report comparison (diff) view.

Every number comes from persisted simulation data: session progress (task
status driven by PX4 MISSION_ITEM_REACHED), drone instance stats and tracks
(PX4 telemetry over NATS), and simulation events. Estimates are labelled.
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Any

from shapely.geometry import shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Drone, Mission
from app.models.mission_planning import MissionPlan, MissionTask
from app.models.simulation import SimulationEvent, SimulationReport, SimulationReportComparison, SimulationSession

PACK_WH_ESTIMATE = 250.0  # x500-class 4S pack; energy figures derived from it are estimates
FAILURE_GROUPS = {
    "drone_failures": ("process_crashed",),
    "link_events": ("comm_degraded", "comm_lost", "comm_restored"),
    "battery_events": ("battery_low",),
    "gps_events": ("gps_drift", "gps_error"),
    "obstacle_events": ("obstacle_spawned", "collision"),
    "wind_events": ("wind_gust", "wind_effect"),
    "reallocation_events": ("tasks_reallocated",),
    "left_mission": ("drone_left_mission",),
    "guardrail_events": ("guardrail",),
}


def generate_report(db: Session, sess: SimulationSession) -> SimulationReport:
    started = time.perf_counter()
    mission = db.get(Mission, sess.mission_id)
    plan = db.get(MissionPlan, sess.plan_id) if sess.plan_id else None
    events = db.execute(select(SimulationEvent).where(SimulationEvent.session_id == sess.id).order_by(SimulationEvent.created_at)).scalars().all()
    progress = sess.progress or {}
    task_status: dict[str, dict] = progress.get("task_status") or {}
    if not task_status:
        rows = db.execute(select(MissionTask).where(MissionTask.mission_id == sess.mission_id)).scalars().all()
        task_status = {t.task_key: {"status": "unassigned" if t.status == "unassigned" else "assigned", "drone": None} for t in rows}
    names = {}
    per_drone_tasks: Counter = Counter()
    drones = []
    missing_telemetry = []
    for inst in sess.instances:
        drone = db.get(Drone, inst.drone_id)
        names[inst.uin] = drone.name
        stats = inst.stats or {}
        track = inst.track or []
        mine = [k for k, t in task_status.items() if t.get("drone") == inst.uin]
        done = [k for k in mine if task_status[k]["status"] == "completed"]
        per_drone_tasks[drone.name] = len(done)
        b0, b1 = stats.get("battery_start"), stats.get("battery_end")
        used = round(b0 - b1, 2) if b0 is not None and b1 is not None else None
        if not track:
            missing_telemetry.append(drone.name)
        drones.append({
            "drone_id": str(inst.drone_id), "name": drone.name, "uin": inst.uin, "final_state": inst.state,
            "sitl_instance": inst.instance_index, "gz_model": inst.gz_model_name,
            "distance_m": stats.get("distance_m"),
            "flight_duration_s": round(track[-1][0] - track[0][0], 1) if len(track) > 1 else None,
            "battery_start_pct": b0, "battery_end_pct": b1, "battery_used_pct": used,
            "energy_wh_estimate": round(used / 100 * PACK_WH_ESTIMATE, 1) if used is not None else None,
            "waypoints_reached": stats.get("waypoints_reached"),
            "tasks_completed": len(done), "tasks_interrupted": sorted(k for k in mine if task_status[k]["status"] != "completed"),
            "gps_error_max_m": stats.get("gps_error_max_m"), "collisions": stats.get("collisions", []),
            "failure_reason": inst.failure_reason, "track_points": len(track),
        })
    counts = Counter(e.event_type for e in events)
    total = len(task_status)
    completed = sum(1 for t in task_status.values() if t["status"] == "completed")
    failed = sum(1 for t in task_status.values() if t["status"] == "failed")
    unassigned = sum(1 for t in task_status.values() if t["status"] == "unassigned")
    used_all = [d["battery_used_pct"] for d in drones if d["battery_used_pct"] is not None]
    aoi = sess.config.get("aoi")
    world = sess.world or {}
    data: dict[str, Any] = {
        "mission_summary": {
            "mission_id": str(sess.mission_id), "mission_name": mission.name, "mission_state": mission.status,
            "session_id": str(sess.id), "aoi": aoi, "aoi_area_km2": world.get("aoi_area_km2"),
            "task_type": sess.task_type, "drones_requested": sess.requested_drones, "drones_spawned": sess.spawned_drones,
            "start_time": sess.started_at.isoformat() if sess.started_at else None, "end_time": sess.ended_at.isoformat() if sess.ended_at else None,
            "wall_duration_s": round((sess.ended_at - sess.started_at).total_seconds(), 1) if sess.started_at and sess.ended_at else None,
            "simulated_duration_s": round(sess.sim_time_s or 0, 1), "status": sess.state, "error": sess.error,
        },
        "task_summary": {
            "total": total, "assigned": total - unassigned, "completed": completed, "failed": failed, "unassigned": unassigned,
            "incomplete": total - unassigned - completed - failed,
            "coverage_pct": progress.get("coverage_pct"), "planned_coverage_pct": plan.coverage_pct if plan else None,
            "per_drone_completed": dict(per_drone_tasks),
        },
        "flight_summary": drones,
        "battery": {"total_used_pct": round(sum(used_all), 2) if used_all else None, "mean_used_pct": round(sum(used_all) / len(used_all), 2) if used_all else None,
                    "energy_wh_estimate": round(sum(used_all) / 100 * PACK_WH_ESTIMATE, 1) if used_all else None, "estimate_basis": f"{PACK_WH_ESTIMATE:.0f} Wh pack (estimate)"},
        "failure_summary": {group: sum(counts[t] for t in types) for group, types in FAILURE_GROUPS.items()},
        "events": [{"t": e.sim_time_s, "type": e.event_type, "drone": next((d["name"] for d in drones if d["drone_id"] == str(e.drone_id)), None),
                    "previous_state": e.previous_state, "new_state": e.new_state, "response": e.response, "outcome": e.mission_outcome}
                   for e in events],
        "environment": {
            "gazebo": ((sess.environment or {}).get("versions") or {}).get("gazebo"), "sitl": ((sess.environment or {}).get("versions") or {}).get("px4"),
            "dem_source": (world.get("terrain") or {}).get("source_label"), "terrain_fallback": (world.get("terrain") or {}).get("is_fallback"),
            "terrain_resolution_m": (world.get("terrain") or {}).get("heightmap_spacing_m"), "dem_native_resolution_m": (world.get("terrain") or {}).get("native_resolution_m"),
            "simulation_speed": sess.speed, "active_drones": sum(1 for d in drones if d["final_state"] != "failed"),
            "world_generation_s": (world.get("timings_s") or {}).get("total_s"), "sitl_startup_s": (sess.environment or {}).get("startup_s"),
            "configuration": {k: v for k, v in (sess.config or {}).items() if k != "aoi"},
        },
        "data_completeness": {"missing_telemetry": missing_telemetry, "complete": not missing_telemetry},
    }
    elapsed_ms = (time.perf_counter() - started) * 1000
    data["generation_ms"] = round(elapsed_ms, 1)
    report = db.execute(select(SimulationReport).where(SimulationReport.session_id == sess.id)).scalar_one_or_none()
    if report is None:
        report = SimulationReport(session_id=sess.id, mission_id=sess.mission_id, organization_id=sess.organization_id)
        db.add(report)
    report.data = data
    report.generation_ms = round(elapsed_ms, 2)
    db.commit()
    db.refresh(report)
    return report


def _iou(a: dict | None, b: dict | None) -> float:
    if not a or not b:
        return 0.0
    pa, pb = shape(a), shape(b)
    union = pa.union(pb).area
    return pa.intersection(pb).area / union if union else 0.0


def compare_reports(db: Session, report: SimulationReport, baseline: SimulationReport) -> SimulationReportComparison:
    if report.organization_id != baseline.organization_id:
        raise ValueError("Reports belong to different organizations")
    a, b = report.data, baseline.data
    warnings: list[str] = []
    iou = _iou(a["mission_summary"].get("aoi"), b["mission_summary"].get("aoi"))
    if iou < 0.95:
        warnings.append(f"Different AOI (overlap {iou * 100:.0f} %)")
    if a["mission_summary"]["task_type"] != b["mission_summary"]["task_type"]:
        warnings.append(f"Different task type ({a['mission_summary']['task_type']} vs {b['mission_summary']['task_type']})")
    if a["mission_summary"]["drones_spawned"] != b["mission_summary"]["drones_spawned"]:
        warnings.append(f"Different fleet size ({a['mission_summary']['drones_spawned']} vs {b['mission_summary']['drones_spawned']})")
    ea, eb = a["environment"], b["environment"]
    if ea.get("simulation_speed") != eb.get("simulation_speed"):
        warnings.append(f"Different simulation speed ({ea.get('simulation_speed')}x vs {eb.get('simulation_speed')}x)")
    if ea.get("dem_source") != eb.get("dem_source"):
        warnings.append("Different terrain source")
    fa = {k: v for k, v in a["failure_summary"].items() if v}
    fb = {k: v for k, v in b["failure_summary"].items() if v}
    if set(fa) != set(fb):
        warnings.append(f"Different injected conditions ({', '.join(sorted(fa)) or 'none'} vs {', '.join(sorted(fb)) or 'none'})")
    sa, sb = a["mission_summary"].get("status"), b["mission_summary"].get("status")
    if sa != sb:
        # e.g. a run stopped early against a completed one: totals cover different parts of the mission.
        warnings.append(f"Different outcome ({sa} vs {sb}); totals are not over the same part of the mission")
    compatible = iou >= 0.95 and a["mission_summary"]["task_type"] == b["mission_summary"]["task_type"] \
        and a["mission_summary"]["drones_spawned"] == b["mission_summary"]["drones_spawned"]

    def delta(x, y):
        return None if x is None or y is None else round(x - y, 2)

    diff = {
        "coverage_pct": {"current": a["task_summary"]["coverage_pct"], "baseline": b["task_summary"]["coverage_pct"],
                         "delta": delta(a["task_summary"]["coverage_pct"], b["task_summary"]["coverage_pct"])},
        "battery_used_total_pct": {"current": a["battery"]["total_used_pct"], "baseline": b["battery"]["total_used_pct"],
                                   "delta": delta(a["battery"]["total_used_pct"], b["battery"]["total_used_pct"])},
        "battery_used_mean_pct": {"current": a["battery"]["mean_used_pct"], "baseline": b["battery"]["mean_used_pct"],
                                  "delta": delta(a["battery"]["mean_used_pct"], b["battery"]["mean_used_pct"])},
        "simulated_duration_s": {"current": a["mission_summary"]["simulated_duration_s"], "baseline": b["mission_summary"]["simulated_duration_s"],
                                 "delta": delta(a["mission_summary"]["simulated_duration_s"], b["mission_summary"]["simulated_duration_s"])},
        "tasks_completed": {"current": a["task_summary"]["completed"], "baseline": b["task_summary"]["completed"],
                            "delta": delta(a["task_summary"]["completed"], b["task_summary"]["completed"])},
        "tasks_failed": {"current": a["task_summary"]["failed"], "baseline": b["task_summary"]["failed"],
                         "delta": delta(a["task_summary"]["failed"], b["task_summary"]["failed"])},
        "drone_failures": {"current": a["failure_summary"]["drone_failures"], "baseline": b["failure_summary"]["drone_failures"],
                           "delta": delta(a["failure_summary"]["drone_failures"], b["failure_summary"]["drone_failures"])},
    }
    comp = SimulationReportComparison(report_id=report.id, baseline_report_id=baseline.id, compatible=compatible,
                                      data={"diff": diff, "warnings": warnings, "aoi_overlap": round(iou, 4),
                                            "current_session": a["mission_summary"]["session_id"], "baseline_session": b["mission_summary"]["session_id"]})
    db.add(comp)
    db.commit()
    db.refresh(comp)
    return comp
