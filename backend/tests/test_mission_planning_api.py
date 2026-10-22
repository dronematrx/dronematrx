"""Test groups B + I -- CBBA planning through the real API and database."""
import time
import uuid

from sqlalchemy import func, select

from app.models import MissionPlan, MissionTask, TaskAssignment, TaskBundle
from tests.planning_support import BASE_LAT, BASE_LON, fleet, headers_for, run_wizard  # noqa: F401

ACCEPTANCE_SECONDS = 2.0


def _allocate(client, headers, mission_id):
    t0 = time.perf_counter()
    resp = client.post(f"/missions/{mission_id}/cbba/allocate", headers=headers)
    return resp, time.perf_counter() - t0


def _assert_plan_consistent(plan: dict) -> None:
    owners: dict[str, str] = {}
    for bundle in plan["bundles"]:
        assert bundle["task_count"] == len(bundle["path"]) == len(bundle["tasks"]) > 0
        assert [t["task_key"] for t in bundle["tasks"]] == bundle["path"]
        etas = [t["eta_seconds"] for t in bundle["tasks"]]
        assert etas == sorted(etas)
        assert len(bundle["waypoints"]) >= 3
        assert bundle["waypoints"][0] == bundle["waypoints"][-1]  # launch and return home
        assert bundle["battery_end_pct"] == bundle["battery_start_pct"] - bundle["energy_pct"] or abs(
            bundle["battery_end_pct"] - (bundle["battery_start_pct"] - bundle["energy_pct"])
        ) < 0.05
        for key in bundle["path"]:
            assert key not in owners, f"task {key} assigned twice"
            owners[key] = bundle["drone_id"]
    for task in plan["tasks"]:
        if task["status"] == "assigned":
            assert owners[task["task_key"]] == task["assigned_drone_id"]
        else:
            assert task["task_key"] not in owners and task["unassigned_reason"]
    assert plan["assigned_count"] == len(owners)
    assert plan["assigned_count"] + plan["unassigned_count"] == plan["task_count"]


def test_allocate_eight_drones_end_to_end(client, db, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)

    resp, elapsed = _allocate(client, headers, mission_id)
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert elapsed < ACCEPTANCE_SECONDS, f"allocation API took {elapsed:.2f}s"

    assert plan["converged"] and not plan["timed_out"]
    assert plan["drone_count"] == 8
    assert plan["task_count"] >= 8
    assert plan["unassigned_count"] == 0
    assert 50 < plan["coverage_pct"] <= 100
    assert plan["coverage_pct"] == plan["max_coverage_pct"]
    assert plan["total_distance_m"] > 0 and plan["estimated_duration_s"] > 0 and plan["estimated_energy_wh"] > 0
    assert len({b["color_index"] for b in plan["bundles"]}) == len(plan["bundles"])  # distinct lane colours
    assert len(plan["bundles"]) >= 4  # work is shared, not swept by one drone
    _assert_plan_consistent(plan)

    # Persisted, and GET returns the same plan.
    mid = uuid.UUID(mission_id)
    assert db.scalar(select(func.count()).select_from(MissionTask).where(MissionTask.mission_id == mid)) == plan["task_count"]
    assert db.scalar(select(func.count()).select_from(TaskAssignment).where(TaskAssignment.plan_id == uuid.UUID(plan["id"]))) == plan["assigned_count"]
    assert db.scalar(select(func.count()).select_from(MissionPlan).where(MissionPlan.mission_id == mid, MissionPlan.is_current.is_(True))) == 1
    again = client.get(f"/missions/{mission_id}/cbba", headers=headers).json()
    assert again["id"] == plan["id"] and again["bundles"] == plan["bundles"]

    # Research diagnostics are exposed.
    diag = plan["diagnostics"]
    assert diag["history"] and diag["bundle_sizes"] and diag["coverage"]["passes"][0]["sensor"] == "camera"


def test_allocation_is_deterministic_for_same_input(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)
    first = _allocate(client, headers, mission_id)[0].json()
    second = _allocate(client, headers, mission_id)[0].json()
    assert [(b["drone_id"], b["path"]) for b in first["bundles"]] == [(b["drone_id"], b["path"]) for b in second["bundles"]]
    assert first["id"] != second["id"]  # new plan row, identical allocation


def test_remove_and_add_drone_reallocation(client, db, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)
    before = _allocate(client, headers, mission_id)[0].json()

    victim = max(before["bundles"], key=lambda b: b["task_count"])
    orphaned = set(victim["path"])
    t0 = time.perf_counter()
    resp = client.delete(f"/missions/{mission_id}/drones/{victim['drone_id']}", headers=headers)
    remove_s = time.perf_counter() - t0
    assert resp.status_code == 200, resp.text
    after = resp.json()

    assert remove_s < ACCEPTANCE_SECONDS
    assert after["converged"] and after["trigger"] == "reallocate"
    assert after["drone_count"] == 7
    assert victim["drone_id"] not in {b["drone_id"] for b in after["bundles"]}
    assigned_after = {key for b in after["bundles"] for key in b["path"]}
    assert orphaned <= assigned_after, "removed drone's tasks must be picked up by the others"
    assert after["unassigned_count"] == 0
    _assert_plan_consistent(after)
    assert db.scalar(select(func.count()).select_from(TaskBundle).where(TaskBundle.drone_id == uuid.UUID(victim["drone_id"]))) == 0

    newcomer = fleet[8]
    t0 = time.perf_counter()
    resp = client.post(f"/missions/{mission_id}/drones", headers=headers, json={"drone_id": str(newcomer.id)})
    add_s = time.perf_counter() - t0
    assert resp.status_code == 200, resp.text
    added = resp.json()
    assert add_s < ACCEPTANCE_SECONDS
    assert added["converged"] and added["drone_count"] == 8
    assert added["unassigned_count"] == 0
    _assert_plan_consistent(added)

    wizard = client.get(f"/missions/{mission_id}/wizard", headers=headers).json()
    roster = wizard["config"]["sensors"]["drone_ids"]
    assert victim["drone_id"] not in roster and str(newcomer.id) in roster


def test_reallocate_endpoint_and_errors(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet, drones=4)
    _allocate(client, headers, mission_id)

    resp = client.post(f"/missions/{mission_id}/cbba/reallocate", headers=headers,
                       json={"add_drone_ids": [str(fleet[5].id)], "remove_drone_ids": [str(fleet[0].id)]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["drone_count"] == 4

    not_member = client.delete(f"/missions/{mission_id}/drones/{fleet[7].id}", headers=headers)
    assert not_member.status_code == 422 and not_member.json()["code"] == "DRONE_NOT_IN_MISSION"
    duplicate = client.post(f"/missions/{mission_id}/drones", headers=headers, json={"drone_id": str(fleet[5].id)})
    assert duplicate.status_code == 422 and duplicate.json()["code"] == "DRONE_ALREADY_IN_MISSION"
    empty = client.post(f"/missions/{mission_id}/cbba/reallocate", headers=headers, json={})
    assert empty.status_code == 422


def test_infeasible_task_reported_and_blocks_unless_accepted(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    # Inspection point needs lidar, which no drone in the fleet carries.
    lidar_point = {"latitude": BASE_LAT, "longitude": BASE_LON + 0.006, "required_sensor": "lidar", "priority": 4}
    mission_id = run_wizard(client, headers, fleet, constraints={"inspection_points": [lidar_point]})

    plan = _allocate(client, headers, mission_id)[0].json()
    assert plan["converged"]
    assert plan["unassigned_count"] == 1
    lidar = next(t for t in plan["tasks"] if t["task_key"] == "P001")
    assert lidar["status"] == "unassigned" and lidar["unassigned_reason"] == "no_drone_with_lidar"

    report = client.post(f"/missions/{mission_id}/validate", headers=headers).json()
    assert not report["passed"]
    assert any(c["code"] == "TASKS_UNASSIGNED" and c["severity"] == "error" for c in report["checks"])

    # Explicitly accepting unassigned tasks downgrades it to a warning.
    client.put(f"/missions/{mission_id}/wizard/steps/5", headers=headers,
               json={"data": {"min_battery_pct": 30, "inspection_points": [lidar_point], "accept_unassigned": True}})
    report = client.post(f"/missions/{mission_id}/validate", headers=headers).json()
    assert report["passed"], report["checks"]
    assert any(c["code"] == "TASKS_UNASSIGNED" and c["severity"] == "warning" for c in report["checks"])


def test_low_battery_drone_is_excluded(client, db, operator_user, fleet):
    fleet[0].battery = 15
    db.commit()
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)
    plan = _allocate(client, headers, mission_id)[0].json()

    assert plan["drone_count"] == 7
    excluded = plan["diagnostics"]["excluded_drones"]
    assert excluded == [{"drone_id": str(fleet[0].id), "drone_name": "QA-01", "reason": "battery_below_30"}]
    assert str(fleet[0].id) not in {b["drone_id"] for b in plan["bundles"]}


def test_no_available_drones_is_a_validation_error(client, db, operator_user, fleet):
    for drone in fleet[:2]:
        drone.status = "maintenance"
    db.commit()
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet, drones=2)
    report = client.post(f"/missions/{mission_id}/validate", headers=headers).json()
    codes = {c["code"] for c in report["checks"]}
    assert not report["passed"]
    assert "NO_DRONES_AVAILABLE" in codes and "DRONE_UNAVAILABLE" in codes


def test_allocate_requires_completed_wizard(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission = client.post("/missions", headers=headers, json={"name": "Half", "mission_type": "SURVEY"}).json()
    client.put(f"/missions/{mission['id']}/wizard/steps/1", headers=headers, json={"data": {"template": "survey"}})
    resp = client.post(f"/missions/{mission['id']}/cbba/allocate", headers=headers)
    assert resp.status_code == 422
    assert resp.json()["code"] == "WIZARD_INCOMPLETE"


def test_allocate_in_draft_is_locked_and_unknown_mission_404(client, operator_user):
    headers = headers_for(client, operator_user)
    mission = client.post("/missions", headers=headers, json={"name": "Draft", "mission_type": "SURVEY"}).json()
    assert client.post(f"/missions/{mission['id']}/cbba/allocate", headers=headers).status_code == 409
    assert client.get(f"/missions/{uuid.uuid4()}/cbba", headers=headers).status_code == 404
    assert client.get(f"/missions/{mission['id']}/cbba", headers=headers).status_code == 404


def test_templates_endpoint(client, observer_user):
    resp = client.get("/missions/templates", headers=headers_for(client, observer_user))
    assert resp.status_code == 200
    assert {t["key"] for t in resp.json()} == {"survey", "search", "inspection", "mapping", "agriculture", "custom"}
