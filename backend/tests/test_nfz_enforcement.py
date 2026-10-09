"""Test group F -- NFZ / geofence enforcement (PostGIS, backend-enforced)."""
from app.services.spatial_validation import SpatialValidator
from tests.planning_support import (  # noqa: F401
    AOI,
    BASE_LAT,
    BASE_LON,
    default_steps,
    fleet,
    headers_for,
    make_zone,
    rect,
    run_wizard,
    save_step,
    square,
)

# AOI spans lon 72.6404..72.6454; the fleet launches west of lon 72.6382.
NFZ_FAR = square(BASE_LAT + 0.05, BASE_LON + 0.05, 0.005)
NFZ_OVER_AOI = square(BASE_LAT, BASE_LON + 0.0075, 0.001)
NFZ_BETWEEN_FLEET_AND_AOI = rect(BASE_LON + 0.0021, BASE_LAT - 0.02, BASE_LON + 0.0029, BASE_LAT + 0.02)


def _validate(client, headers, mission_id):
    resp = client.post(f"/missions/{mission_id}/validate", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _codes(report, severity="error"):
    return {c["code"] for c in report["checks"] if c["severity"] == severity}


def test_case1_aoi_clear_of_nfz_can_proceed(client, db, organization, operator_user, fleet):
    make_zone(db, organization, "Far NFZ", "nfz", NFZ_FAR)
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)

    report = _validate(client, headers, mission_id)
    assert report["passed"] and not report["nfz_conflict"] and report["conflicts"] == []
    armed = client.post(f"/missions/{mission_id}/transition", headers=headers, json={"target_state": "armed"})
    assert armed.status_code == 200


def test_case2_aoi_intersecting_nfz_blocks_arming(client, db, organization, operator_user, fleet):
    zone = make_zone(db, organization, "Hospital NFZ", "nfz", NFZ_OVER_AOI)
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)

    report = _validate(client, headers, mission_id)
    assert not report["passed"] and report["nfz_conflict"]
    aoi_conflicts = [c for c in report["conflicts"] if c["scope"] == "aoi"]
    assert aoi_conflicts and aoi_conflicts[0]["kind"] == "nfz" and aoi_conflicts[0]["geofence_id"] == str(zone.id)
    assert aoi_conflicts[0]["intersection"]["type"] == "Polygon"  # overlay for the map
    nfz_check = next(c for c in report["checks"] if c["code"] == "NFZ_CONFLICT")
    assert "NFZ CONFLICT" in nfz_check["message"] and "Hospital NFZ" in nfz_check["message"]

    armed = client.post(f"/missions/{mission_id}/transition", headers=headers, json={"target_state": "armed"})
    assert armed.status_code == 422
    assert armed.json()["detail"] == "NFZ conflict blocks arming"
    assert client.get(f"/missions/{mission_id}", headers=headers).json()["status"] == "validating"


def test_case3_route_crossing_nfz_is_a_conflict(client, db, organization, operator_user, fleet):
    make_zone(db, organization, "Power Line Corridor", "nfz", NFZ_BETWEEN_FLEET_AND_AOI)
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)

    report = _validate(client, headers, mission_id)
    scopes = {c["scope"] for c in report["conflicts"]}
    assert "route" in scopes, report["conflicts"]
    assert "aoi" not in scopes and "launch" not in scopes  # the AOI itself is clear
    assert report["nfz_conflict"] and not report["passed"]
    route_conflicts = [c for c in report["conflicts"] if c["scope"] == "route"]
    assert all(c["drone_id"] for c in route_conflicts)
    assert client.post(f"/missions/{mission_id}/transition", headers=headers, json={"target_state": "armed"}).status_code == 422


def test_case4_backend_rejects_bypass_attempts(client, db, organization, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)

    # Skipping validation entirely: PLANNING -> ARMED is not a legal edge.
    skip = client.post(f"/missions/{mission_id}/transition", headers=headers, json={"target_state": "armed"})
    assert skip.status_code == 409
    assert client.patch(f"/missions/{mission_id}", headers=headers, json={"status": "armed"}).status_code == 409

    # Validated while clear, then an NFZ is published before arming: the cached
    # "passed" report must not be trusted.
    assert _validate(client, headers, mission_id)["passed"]
    make_zone(db, organization, "Late NOTAM", "nfz", NFZ_OVER_AOI)
    via_transition = client.post(f"/missions/{mission_id}/transition", headers=headers, json={"target_state": "armed"})
    assert via_transition.status_code == 422 and via_transition.json()["report"]["nfz_conflict"] is True
    via_patch = client.patch(f"/missions/{mission_id}", headers=headers, json={"status": "armed"})
    assert via_patch.status_code == 422
    assert client.get(f"/missions/{mission_id}", headers=headers).json()["status"] == "validating"


def test_launch_point_inside_nfz(client, db, organization, operator_user, fleet):
    first = fleet[0]
    make_zone(db, organization, "Pad NFZ", "nfz", square(BASE_LAT + 0.0010, BASE_LON - 0.0010, 0.0002))
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)
    report = _validate(client, headers, mission_id)
    launch = [c for c in report["conflicts"] if c["scope"] == "launch"]
    assert launch and launch[0]["drone_id"] == str(first.id)


def test_exclusion_zone_enforced_only_when_selected(client, db, organization, operator_user, fleet):
    zone = make_zone(db, organization, "Crowd Area", "exclusion", NFZ_OVER_AOI)
    headers = headers_for(client, operator_user)

    unselected = run_wizard(client, headers, fleet)
    assert _validate(client, headers, unselected)["passed"]

    selected = run_wizard(client, headers, fleet, constraints={"geofence_ids": [str(zone.id)]})
    report = _validate(client, headers, selected)
    assert not report["passed"] and not report["nfz_conflict"]
    assert "EXCLUSION_CONFLICT" in _codes(report)


def test_inclusion_zone_containment_and_ceiling(client, db, organization, operator_user, fleet):
    headers = headers_for(client, operator_user)
    tight = make_zone(db, organization, "Tight Fence", "inclusion", square(BASE_LAT, BASE_LON + 0.006, 0.001))
    report = _validate(client, headers, run_wizard(client, headers, fleet, constraints={"geofence_ids": [str(tight.id)]}))
    assert "INCLUSION_VIOLATION" in _codes(report)

    roomy = make_zone(db, organization, "Ops Box", "inclusion", square(BASE_LAT, BASE_LON + 0.003, 0.02), altitude_max=100)
    report = _validate(client, headers, run_wizard(client, headers, fleet, constraints={"geofence_ids": [str(roomy.id)]}))
    assert report["passed"], report["checks"]

    low = make_zone(db, organization, "Low Ceiling Box", "inclusion", square(BASE_LAT, BASE_LON + 0.003, 0.02), altitude_max=40)
    report = _validate(client, headers, run_wizard(client, headers, fleet, constraints={"geofence_ids": [str(low.id)]}))
    assert "ALTITUDE_CEILING" in _codes(report)


def test_nfz_cannot_be_unselected_in_constraints(client, db, organization, operator_user, fleet):
    nfz = make_zone(db, organization, "NFZ", "nfz", NFZ_FAR)
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet)
    resp = client.put(f"/missions/{mission_id}/wizard/steps/5", headers=headers,
                      json={"data": {**default_steps(fleet)[5], "geofence_ids": [str(nfz.id)]}})
    assert resp.status_code == 422 and resp.json()["code"] == "GEOFENCE_INVALID"


def test_inactive_nfz_is_ignored(client, db, organization, operator_user, fleet):
    zone = make_zone(db, organization, "Expired NOTAM", "nfz", NFZ_OVER_AOI)
    zone.active = False
    db.commit()
    headers = headers_for(client, operator_user)
    assert _validate(client, headers, run_wizard(client, headers, fleet))["passed"]


def test_spatial_validator_postgis_primitives(db, organization):
    zone = make_zone(db, organization, "Box", "nfz", square(BASE_LAT, BASE_LON, 0.01))
    other = make_zone(db, organization, "Other", "nfz", square(BASE_LAT + 1, BASE_LON + 1, 0.01))
    validator = SpatialValidator(db, organization.id)

    inside = {"type": "Point", "coordinates": [BASE_LON, BASE_LAT]}
    outside = {"type": "Point", "coordinates": [BASE_LON + 0.5, BASE_LAT]}
    assert [z.id for z in validator.covering(inside, ["nfz"])] == [zone.id]
    assert validator.covering(outside, ["nfz"]) == []

    crossing = {"type": "LineString", "coordinates": [[BASE_LON - 0.05, BASE_LAT], [BASE_LON + 0.05, BASE_LAT]]}
    hits = validator.intersecting(crossing, ["nfz"])
    assert [z.id for z, _ in hits] == [zone.id]
    assert hits[0][1]["type"] == "LineString"  # clipped segment inside the zone
    assert validator.intersecting(crossing, ["exclusion"]) == []

    assert validator.covered_by_union(inside, [zone.id]) is True
    assert validator.covered_by_union(crossing, [zone.id]) is False
    assert validator.covered_by_union(crossing, [zone.id, other.id]) is False
