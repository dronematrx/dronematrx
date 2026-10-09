from tests.conftest import auth_headers

AOI = {
    "type": "Polygon",
    "coordinates": [[[72.60, 23.20], [72.62, 23.20], [72.62, 23.22], [72.60, 23.22], [72.60, 23.20]]],
}


def test_create_mission(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    resp = client.post("/missions", headers=headers, json={"name": "Survey 1", "mission_type": "SURVEY"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Survey 1"
    assert body["status"] == "draft"


def test_observer_cannot_create_mission(client, observer_user):
    headers = auth_headers(client, observer_user.email)
    resp = client.post("/missions", headers=headers, json={"name": "Survey 2", "mission_type": "SURVEY"})
    assert resp.status_code == 403


def test_set_and_get_aoi(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    mission = client.post("/missions", headers=headers, json={"name": "AOI Mission", "mission_type": "SURVEY"}).json()

    set_resp = client.post(f"/missions/{mission['id']}/aoi", headers=headers, json={"geometry": AOI})
    assert set_resp.status_code == 200
    # ~2.2 km x 2.2 km square: area must be in m², not square degrees.
    assert 4.0e6 < set_resp.json()["area_sq_meters"] < 5.5e6
    assert set_resp.json()["vertex_count"] == 5

    get_resp = client.get(f"/missions/{mission['id']}/aoi", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["geometry"]["type"] == "Polygon"


def test_patch_status_goes_through_state_machine(client, operator_user):
    """Sprint 3: PATCH can no longer write arbitrary statuses. DRAFT -> ACTIVE
    is rejected; legal transitions (e.g. DRAFT -> ABORTED) still work and set
    end_time. The full lifecycle is covered in test_mission_state_machine.py."""
    headers = auth_headers(client, operator_user.email)
    mission = client.post("/missions", headers=headers, json={"name": "Lifecycle", "mission_type": "SURVEY"}).json()

    skipped = client.patch(f"/missions/{mission['id']}", headers=headers, json={"status": "active"})
    assert skipped.status_code == 409
    assert skipped.json()["code"] == "INVALID_TRANSITION"
    assert client.get(f"/missions/{mission['id']}", headers=headers).json()["status"] == "draft"

    aborted = client.patch(f"/missions/{mission['id']}", headers=headers, json={"status": "aborted"})
    assert aborted.status_code == 200
    assert aborted.json()["status"] == "aborted"
    assert aborted.json()["end_time"] is not None

    renamed = client.patch(f"/missions/{mission['id']}", headers=headers, json={"name": "Renamed"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Renamed"


def test_create_mission_only_in_draft(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    resp = client.post("/missions", headers=headers, json={"name": "Sneaky", "mission_type": "SURVEY", "status": "active"})
    assert resp.status_code == 422


def test_delete_mission(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    mission = client.post("/missions", headers=headers, json={"name": "ToDelete", "mission_type": "SURVEY"}).json()

    resp = client.delete(f"/missions/{mission['id']}", headers=headers)
    assert resp.status_code == 204

    get_resp = client.get(f"/missions/{mission['id']}", headers=headers)
    assert get_resp.status_code == 404
