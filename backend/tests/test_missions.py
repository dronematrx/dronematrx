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
    assert set_resp.json()["area_sq_meters"] > 0
    assert set_resp.json()["vertex_count"] == 5

    get_resp = client.get(f"/missions/{mission['id']}/aoi", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["geometry"]["type"] == "Polygon"


def test_update_mission_status_lifecycle(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    mission = client.post("/missions", headers=headers, json={"name": "Lifecycle", "mission_type": "SURVEY"}).json()

    started = client.patch(f"/missions/{mission['id']}", headers=headers, json={"status": "active"})
    assert started.status_code == 200
    assert started.json()["status"] == "active"
    assert started.json()["start_time"] is not None

    stopped = client.patch(f"/missions/{mission['id']}", headers=headers, json={"status": "completed"})
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "completed"
    assert stopped.json()["end_time"] is not None


def test_delete_mission(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    mission = client.post("/missions", headers=headers, json={"name": "ToDelete", "mission_type": "SURVEY"}).json()

    resp = client.delete(f"/missions/{mission['id']}", headers=headers)
    assert resp.status_code == 204

    get_resp = client.get(f"/missions/{mission['id']}", headers=headers)
    assert get_resp.status_code == 404
