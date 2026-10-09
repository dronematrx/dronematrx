"""Test group E -- mission lifecycle state machine, guards, audit trail."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import AuditLog
from app.services.mission_state_service import TRANSITIONS, is_valid_transition
from tests.planning_support import create_mission, fleet, headers_for, run_wizard  # noqa: F401

STATES = ["draft", "planning", "validating", "armed", "active", "completed", "aborted"]
LEGAL = {
    ("draft", "planning"), ("draft", "aborted"),
    ("planning", "validating"), ("planning", "aborted"),
    ("validating", "armed"), ("validating", "planning"), ("validating", "aborted"),
    ("armed", "active"), ("armed", "validating"), ("armed", "aborted"),
    ("active", "completed"), ("active", "aborted"),
}


@pytest.mark.parametrize("current", STATES)
@pytest.mark.parametrize("target", STATES)
def test_transition_table_is_exact(current, target):
    assert is_valid_transition(current, target) == ((current, target) in LEGAL)


def test_terminal_states_have_no_exits():
    assert TRANSITIONS["completed"] == frozenset() and TRANSITIONS["aborted"] == frozenset()


def _transition(client, headers, mission_id, target, reason=None):
    return client.post(f"/missions/{mission_id}/transition", headers=headers, json={"target_state": target, "reason": reason})


def test_happy_path_lifecycle_with_audit_and_history(client, db, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)  # DRAFT -> PLANNING happens on the first wizard save

    report = client.post(f"/missions/{mission_id}/validate", headers=headers).json()  # PLANNING -> VALIDATING
    assert report["passed"]

    for target in ("armed", "active", "completed"):
        resp = _transition(client, headers, mission_id, target, f"go {target}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == target

    mission = client.get(f"/missions/{mission_id}", headers=headers).json()
    assert mission["start_time"] is not None and mission["end_time"] is not None

    history = client.get(f"/missions/{mission_id}/state-history", headers=headers).json()
    assert [(h["previous_state"], h["new_state"]) for h in history] == [
        ("draft", "planning"), ("planning", "validating"), ("validating", "armed"), ("armed", "active"), ("active", "completed"),
    ]
    assert all(h["user_id"] == str(operator_user.id) for h in history)
    assert history[-1]["reason"] == "go completed"

    audit = db.execute(
        select(AuditLog).where(AuditLog.resource_id == mission_id, AuditLog.action == "mission_state_changed").order_by(AuditLog.timestamp)
    ).scalars().all()
    assert len(audit) == 5
    assert {(a.event_metadata["from"], a.event_metadata["to"]) for a in audit} == {(h["previous_state"], h["new_state"]) for h in history}
    assert all(a.user_id == operator_user.id for a in audit)


@pytest.mark.parametrize(
    "setup,target",
    [
        ([], "active"),
        ([], "completed"),
        ([], "armed"),
        (["aborted"], "active"),
        (["aborted"], "planning"),
    ],
)
def test_invalid_transitions_rejected(client, operator_user, setup, target):
    headers = headers_for(client, operator_user)
    mission = create_mission(client, headers)
    for step in setup:
        assert _transition(client, headers, mission["id"], step).status_code == 200
    before = client.get(f"/missions/{mission['id']}", headers=headers).json()["status"]

    resp = _transition(client, headers, mission["id"], target)
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "INVALID_TRANSITION" and body["current"] == before and body["target"] == target
    assert client.get(f"/missions/{mission['id']}", headers=headers).json()["status"] == before


def test_completed_cannot_restart_and_active_cannot_go_to_draft(client, db, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)
    client.post(f"/missions/{mission_id}/validate", headers=headers)
    _transition(client, headers, mission_id, "armed")
    _transition(client, headers, mission_id, "active")
    assert _transition(client, headers, mission_id, "draft").status_code == 409
    assert _transition(client, headers, mission_id, "completed").status_code == 200
    assert _transition(client, headers, mission_id, "active").status_code == 409
    assert _transition(client, headers, mission_id, "aborted").status_code == 409


def test_validating_requires_complete_wizard(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission = create_mission(client, headers)
    client.put(f"/missions/{mission['id']}/wizard/steps/1", headers=headers, json={"data": {"template": "survey"}})
    resp = _transition(client, headers, mission["id"], "validating")
    assert resp.status_code == 422 and resp.json()["code"] == "TRANSITION_BLOCKED"


def test_arming_reruns_validation_server_side(client, db, operator_user, fleet):
    """Even with a passing cached report, arming re-checks the live state."""
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet, drones=4)
    assert client.post(f"/missions/{mission_id}/validate", headers=headers).json()["passed"]

    # Every assigned drone goes into maintenance after validation: arming must notice.
    for drone in fleet[:4]:
        drone.status = "maintenance"
    db.commit()
    resp = _transition(client, headers, mission_id, "armed")
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "TRANSITION_BLOCKED"
    assert body["report"]["passed"] is False
    assert client.get(f"/missions/{mission_id}", headers=headers).json()["status"] == "validating"


def test_activation_requires_fresh_telemetry_and_battery(client, db, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet, drones=3)
    client.post(f"/missions/{mission_id}/validate", headers=headers)
    assert _transition(client, headers, mission_id, "armed").status_code == 200

    fleet[0].last_telemetry_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    fleet[1].battery = 10
    db.commit()
    readiness = client.get(f"/missions/{mission_id}/readiness", headers=headers).json()
    assert readiness["ready"] is False
    codes = {c["code"] for c in readiness["checks"]}
    assert {"TELEMETRY_STALE", "BATTERY_LOW"} <= codes

    blocked = _transition(client, headers, mission_id, "active")
    assert blocked.status_code == 422 and blocked.json()["code"] == "TRANSITION_BLOCKED"

    fleet[0].last_telemetry_at = datetime.now(timezone.utc)
    fleet[1].battery = 90
    db.commit()
    assert client.get(f"/missions/{mission_id}/readiness", headers=headers).json()["ready"] is True
    assert _transition(client, headers, mission_id, "active").status_code == 200


def test_disarm_and_abort_paths(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)
    client.post(f"/missions/{mission_id}/validate", headers=headers)
    assert _transition(client, headers, mission_id, "armed").status_code == 200
    assert _transition(client, headers, mission_id, "validating", "disarm").status_code == 200
    assert _transition(client, headers, mission_id, "aborted", "weather").status_code == 200
    mission = client.get(f"/missions/{mission_id}", headers=headers).json()
    assert mission["status"] == "aborted" and mission["end_time"] is not None
