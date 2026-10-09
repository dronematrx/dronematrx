"""Test group H -- RBAC on every Sprint 3 endpoint, enforced by the backend.

ADMIN / OPERATOR: create, edit, plan, validate, transition missions; manage geofences.
OBSERVER / ANALYST: read-only (403 on every mutation).
No token: 401 everywhere.
"""
import pytest

from tests.conftest import _make_user
from tests.planning_support import AOI, default_steps, fleet, headers_for, run_wizard  # noqa: F401

SQUARE = AOI


@pytest.fixture()
def analyst_user(db, organization):
    return _make_user(db, organization, "ANALYST")


@pytest.fixture()
def users(admin_user, operator_user, observer_user, analyst_user):
    return {"ADMIN": admin_user, "OPERATOR": operator_user, "OBSERVER": observer_user, "ANALYST": analyst_user}


@pytest.fixture()
def planned(client, operator_user, fleet):
    """A mission with steps 1-5 complete and a CBBA plan, owned by the operator's org."""
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet, drones=6)
    assert client.post(f"/missions/{mission_id}/cbba/allocate", headers=headers).status_code == 200
    zone = client.post("/geofences", headers=headers, json={"name": "Zone", "type": "exclusion", "geometry": SQUARE}).json()
    return {"mission_id": mission_id, "zone_id": zone["id"], "fleet": fleet}


def _mutations(ctx):
    m, z, fleet = ctx["mission_id"], ctx["zone_id"], ctx["fleet"]
    steps = default_steps(fleet, drones=6)
    return [
        ("POST", "/missions", {"name": "RBAC", "mission_type": "SURVEY"}),
        ("PATCH", f"/missions/{m}", {"name": "Renamed"}),
        ("PUT", f"/missions/{m}/wizard/steps/3", {"data": steps[3]}),
        ("POST", f"/missions/{m}/cbba/allocate", None),
        ("POST", f"/missions/{m}/drones", {"drone_id": str(fleet[7].id)}),
        ("POST", f"/missions/{m}/cbba/reallocate", {"remove_drone_ids": [str(fleet[7].id)]}),
        ("DELETE", f"/missions/{m}/drones/{fleet[5].id}", None),
        ("POST", f"/missions/{m}/validate", None),
        ("POST", f"/missions/{m}/transition", {"target_state": "armed"}),
        ("POST", "/geofences", {"name": "New", "type": "nfz", "geometry": SQUARE}),
        ("PATCH", f"/geofences/{z}", {"name": "Renamed zone"}),
        ("POST", "/geofences/import", {"format": "geojson", "content": {"type": "Feature", "properties": {"name": "I"}, "geometry": SQUARE}}),
        ("DELETE", f"/geofences/{z}", None),
    ]


def _reads(ctx):
    m = ctx["mission_id"]
    return [
        f"/missions/{m}",
        f"/missions/{m}/wizard",
        f"/missions/{m}/cbba",
        f"/missions/{m}/state-history",
        f"/missions/{m}/readiness",
        "/missions/templates",
        "/geofences",
        "/geofences/export?format=geojson",
        "/geofences/export?format=kml",
        f"/geofences/{ctx['zone_id']}",
    ]


@pytest.mark.parametrize("role", ["ADMIN", "OPERATOR"])
def test_write_roles_can_run_every_mutation(client, users, planned, role):
    headers = headers_for(client, users[role])
    for method, path, body in _mutations(planned):
        resp = client.request(method, path, headers=headers, json=body)
        assert resp.status_code in (200, 201, 204), f"{role} {method} {path}: {resp.status_code} {resp.text}"


@pytest.mark.parametrize("role", ["OBSERVER", "ANALYST"])
def test_read_only_roles_get_403_on_every_mutation(client, users, planned, role):
    headers = headers_for(client, users[role])
    before = client.get(f"/missions/{planned['mission_id']}/cbba", headers=headers).json()
    for method, path, body in _mutations(planned):
        resp = client.request(method, path, headers=headers, json=body)
        assert resp.status_code == 403, f"{role} {method} {path}: {resp.status_code}"
    after = client.get(f"/missions/{planned['mission_id']}/cbba", headers=headers).json()
    assert after["id"] == before["id"]  # nothing changed


@pytest.mark.parametrize("role", ["ADMIN", "OPERATOR", "OBSERVER", "ANALYST"])
def test_every_role_can_read(client, users, planned, role):
    headers = headers_for(client, users[role])
    for path in _reads(planned):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200, f"{role} GET {path}: {resp.status_code}"


def test_unauthenticated_requests_get_401(client, planned):
    for method, path, body in _mutations(planned):
        assert client.request(method, path, json=body).status_code == 401, f"{method} {path}"
    for path in _reads(planned):
        assert client.get(path).status_code == 401, path
    bad = {"Authorization": "Bearer forged.token.value"}
    assert client.post(f"/missions/{planned['mission_id']}/transition", headers=bad, json={"target_state": "aborted"}).status_code == 401


def test_other_organization_cannot_see_or_touch_mission(client, db, planned):
    from app.models import Organization

    other_org = Organization(name="Rival", slug="rival-org")
    db.add(other_org)
    db.commit()
    intruder = _make_user(db, other_org, "ADMIN")
    headers = headers_for(client, intruder)
    m = planned["mission_id"]
    assert client.get(f"/missions/{m}/cbba", headers=headers).status_code == 404
    assert client.post(f"/missions/{m}/transition", headers=headers, json={"target_state": "aborted"}).status_code == 404
    assert client.get(f"/geofences/{planned['zone_id']}", headers=headers).status_code == 404
