"""Test group D -- 6-step wizard persistence, ordering, validation, resume."""
from tests.planning_support import (  # noqa: F401
    AOI,
    create_mission,
    default_steps,
    fleet,
    headers_for,
    run_wizard,
    save_step,
)


def test_full_wizard_flow_persists_every_step(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission = create_mission(client, headers)
    steps = default_steps(fleet, flight={"speed_mps": 9, "overlap_pct": 65, "heading_deg": 45},
                          constraints={"max_flight_time_min": 25, "link_loss_policy": "HOLD"})

    for step, data in steps.items():
        state = save_step(client, headers, mission["id"], step, data)
        assert step in state["completed_steps"]
        assert state["wizard_step"] == min(step + 1, 6)
        assert state["status"] == "planning"  # DRAFT -> PLANNING on the first save

    # A fresh GET (i.e. a browser refresh) restores everything from the database.
    restored = client.get(f"/missions/{mission['id']}/wizard", headers=headers).json()
    assert restored["completed_steps"] == [1, 2, 3, 4, 5]
    assert restored["wizard_step"] == 6
    assert restored["template"] == "survey" and restored["name"] == "QA Survey"
    assert restored["config"]["flight"]["speed_mps"] == 9
    assert restored["config"]["flight"]["heading_deg"] == 45
    assert restored["config"]["sensors"]["drone_ids"] == steps[4]["drone_ids"]
    assert restored["config"]["constraints"]["max_flight_time_min"] == 25
    assert restored["config"]["constraints"]["link_loss_policy"] == "HOLD"
    assert restored["aoi_geometry"]["coordinates"][0][0] == AOI["coordinates"][0][0]
    assert 250_000 < restored["aoi_area_sq_meters"] < 320_000

    # Step 6: validate runs CBBA and reports.
    report = client.post(f"/missions/{mission['id']}/validate", headers=headers).json()
    assert report["passed"], report["checks"]
    assert report["status"] == "validating"
    plan = client.get(f"/missions/{mission['id']}/cbba", headers=headers).json()
    assert plan["converged"] and plan["assigned_count"] == plan["task_count"]
    assert report["plan_id"] == plan["id"]


def test_save_and_exit_then_resume(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission = create_mission(client, headers)
    steps = default_steps(fleet)
    for step in (1, 2, 3, 4):
        save_step(client, headers, mission["id"], step, steps[step])

    # "Save & Exit": nothing else is sent. Reopen from the mission list.
    listing = client.get("/missions", headers=headers).json()
    entry = next(m for m in listing if m["id"] == mission["id"])
    assert entry["wizard_step"] == 5 and entry["completed_steps"] == [1, 2, 3, 4]
    assert entry["template"] == "survey" and entry["status"] == "planning"

    state = client.get(f"/missions/{mission['id']}/wizard", headers=headers).json()
    assert state["wizard_step"] == 5
    assert state["config"]["flight"]["altitude_m"] == 60
    assert len(state["config"]["sensors"]["drone_ids"]) == 8
    assert "constraints" not in state["config"]

    # Resume and finish.
    final = save_step(client, headers, mission["id"], 5, steps[5])
    assert final["completed_steps"] == [1, 2, 3, 4, 5]


def test_steps_must_be_completed_in_order(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission = create_mission(client, headers)
    resp = client.put(f"/missions/{mission['id']}/wizard/steps/3", headers=headers, json={"data": {"altitude_m": 50}})
    assert resp.status_code == 422
    assert resp.json()["code"] == "STEP_ORDER"
    assert client.put(f"/missions/{mission['id']}/wizard/steps/6", headers=headers, json={"data": {}}).status_code == 422


def test_invalid_step_data_blocks_progression(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission = create_mission(client, headers)
    steps = default_steps(fleet)
    save_step(client, headers, mission["id"], 1, steps[1])

    # Self-intersecting "bow tie" AOI.
    bow_tie = {"type": "Polygon", "coordinates": [[[72.64, 23.21], [72.65, 23.22], [72.65, 23.21], [72.64, 23.22], [72.64, 23.21]]]}
    bad_aoi = client.put(f"/missions/{mission['id']}/wizard/steps/2", headers=headers, json={"data": {"source": "drawn", "geometry": bow_tie}})
    assert bad_aoi.status_code == 422 and bad_aoi.json()["code"] == "AOI_INVALID"
    tiny = {"type": "Polygon", "coordinates": [[[72.64, 23.21], [72.64001, 23.21], [72.64001, 23.21001], [72.64, 23.21]]]}
    assert client.put(f"/missions/{mission['id']}/wizard/steps/2", headers=headers, json={"data": {"source": "drawn", "geometry": tiny}}).status_code == 422
    assert client.put(f"/missions/{mission['id']}/wizard/steps/2", headers=headers, json={"data": {"source": "existing"}}).json()["code"] == "AOI_MISSING"

    save_step(client, headers, mission["id"], 2, steps[2])
    for bad in ({"altitude_m": 500}, {"speed_mps": 0}, {"overlap_pct": 95}, {"pattern": "spiral"}):
        resp = client.put(f"/missions/{mission['id']}/wizard/steps/3", headers=headers, json={"data": {**steps[3], **bad}})
        assert resp.status_code == 422, bad
    save_step(client, headers, mission["id"], 3, steps[3])

    no_drones = client.put(f"/missions/{mission['id']}/wizard/steps/4", headers=headers,
                           json={"data": {"required_sensors": ["camera"], "drone_ids": []}})
    assert no_drones.status_code == 422
    # Sensor compatibility: lidar is required but nobody carries one.
    no_lidar = client.put(f"/missions/{mission['id']}/wizard/steps/4", headers=headers,
                          json={"data": {**steps[4], "required_sensors": ["lidar"]}})
    assert no_lidar.status_code == 422 and no_lidar.json()["code"] == "PAYLOAD_MISSING"
    # Assigning a payload the drone does not carry.
    wrong_payload = client.put(f"/missions/{mission['id']}/wizard/steps/4", headers=headers,
                               json={"data": {**steps[4], "payload_assignments": {str(fleet[0].id): "thermal"}}})
    assert wrong_payload.status_code == 422 and wrong_payload.json()["code"] == "PAYLOAD_INCOMPATIBLE"
    save_step(client, headers, mission["id"], 4, steps[4])

    # Launch minimum must be above the RTH reserve.
    thresholds = client.put(f"/missions/{mission['id']}/wizard/steps/5", headers=headers, json={"data": {"min_battery_pct": 15}})
    assert thresholds.status_code == 422 and thresholds.json()["code"] == "BATTERY_THRESHOLDS"

    state = client.get(f"/missions/{mission['id']}/wizard", headers=headers).json()
    assert state["completed_steps"] == [1, 2, 3, 4]  # nothing invalid was persisted


def test_template_prefills_later_steps(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission = create_mission(client, headers)
    state = save_step(client, headers, mission["id"], 1, {"template": "search"})
    assert state["config"]["flight"]["altitude_m"] == 40
    assert state["config"]["flight"]["overlap_pct"] == 30
    assert state["config"]["sensors"]["required_sensors"] == ["thermal"]
    assert state["completed_steps"] == [1]
    detail = client.get(f"/missions/{mission['id']}", headers=headers).json()
    assert detail["mission_type"] == "SAR" and detail["template"] == "search"


def test_copy_existing_aoi_from_another_mission(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    source_id = run_wizard(client, headers, fleet)
    mission = create_mission(client, headers, "Copy target")
    save_step(client, headers, mission["id"], 1, {"template": "custom"})
    state = save_step(client, headers, mission["id"], 2, {"source": "copied", "copy_from_mission_id": source_id})
    assert state["aoi_geometry"] == client.get(f"/missions/{source_id}/wizard", headers=headers).json()["aoi_geometry"]


def test_speed_change_updates_eta_without_manual_replan(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)
    steps = default_steps(fleet)
    slow = client.post(f"/missions/{mission_id}/cbba/allocate", headers=headers).json()

    state = save_step(client, headers, mission_id, 3, {**steps[3], "speed_mps": 14})
    assert state["replanned"] and not state["plan_stale"]
    fast = client.get(f"/missions/{mission_id}/cbba", headers=headers).json()
    assert fast["id"] != slow["id"] and fast["trigger"] == "parameters_changed"
    assert fast["estimated_duration_s"] < slow["estimated_duration_s"]
    for bundle in fast["bundles"]:
        assert bundle["eta_seconds"] > 0


def test_editing_after_validation_returns_to_planning(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)
    assert client.post(f"/missions/{mission_id}/validate", headers=headers).json()["status"] == "validating"

    state = save_step(client, headers, mission_id, 3, {**default_steps(fleet)[3], "altitude_m": 70})
    assert state["status"] == "planning"
    assert state["validation"] is None
    history = client.get(f"/missions/{mission_id}/state-history", headers=headers).json()
    assert [h["new_state"] for h in history] == ["planning", "validating", "planning"]


def test_wizard_locked_once_armed(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)
    client.post(f"/missions/{mission_id}/validate", headers=headers)
    armed = client.post(f"/missions/{mission_id}/transition", headers=headers, json={"target_state": "armed"})
    assert armed.status_code == 200, armed.text

    resp = client.put(f"/missions/{mission_id}/wizard/steps/3", headers=headers, json={"data": default_steps(fleet)[3]})
    assert resp.status_code == 409
    assert client.get(f"/missions/{mission_id}/wizard", headers=headers).json()["editable"] is False
