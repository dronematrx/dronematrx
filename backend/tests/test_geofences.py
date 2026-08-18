from tests.conftest import auth_headers

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[72.60, 23.20], [72.62, 23.20], [72.62, 23.22], [72.60, 23.22], [72.60, 23.20]]],
}


def test_create_geofence(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    resp = client.post(
        "/geofences",
        headers=headers,
        json={"name": "Test Fence", "type": "geofence", "geometry": SQUARE, "altitude_max": 100},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Test Fence"
    assert body["type"] == "geofence"
    assert body["geometry"]["type"] == "Polygon"


def test_create_nfz(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    resp = client.post(
        "/geofences", headers=headers, json={"name": "Restricted Airspace", "type": "nfz", "geometry": SQUARE}
    )
    assert resp.status_code == 201
    assert resp.json()["type"] == "nfz"


def test_list_geofences_filtered_by_type(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    client.post("/geofences", headers=headers, json={"name": "F1", "type": "geofence", "geometry": SQUARE})
    client.post("/geofences", headers=headers, json={"name": "N1", "type": "nfz", "geometry": SQUARE})

    resp = client.get("/geofences?type=nfz", headers=headers)
    assert resp.status_code == 200
    assert all(g["type"] == "nfz" for g in resp.json())
    assert any(g["name"] == "N1" for g in resp.json())


def test_update_and_delete_geofence(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    create = client.post("/geofences", headers=headers, json={"name": "Temp", "type": "geofence", "geometry": SQUARE})
    geofence_id = create.json()["id"]

    update = client.patch(f"/geofences/{geofence_id}", headers=headers, json={"active": False})
    assert update.status_code == 200
    assert update.json()["active"] is False

    delete = client.delete(f"/geofences/{geofence_id}", headers=headers)
    assert delete.status_code == 204
