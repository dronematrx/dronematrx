from tests.conftest import auth_headers


def test_list_drones(client, admin_user, sample_drone):
    headers = auth_headers(client, admin_user.email)
    resp = client.get("/drones", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "DMX-001"
    assert body["items"][0]["latitude"] == 23.2156
    assert body["items"][0]["longitude"] == 72.6369


def test_get_drone_detail_includes_payloads_and_maintenance(client, admin_user, sample_drone):
    headers = auth_headers(client, admin_user.email)
    resp = client.get(f"/drones/{sample_drone.id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(sample_drone.id)
    assert body["payloads"] == []
    assert body["maintenance_records"] == []


def test_get_drone_not_found(client, admin_user):
    headers = auth_headers(client, admin_user.email)
    resp = client.get("/drones/00000000-0000-0000-0000-000000000000", headers=headers)
    assert resp.status_code == 404


def test_create_drone_requires_fleet_write_role(client, observer_user):
    headers = auth_headers(client, observer_user.email)
    resp = client.post("/drones", headers=headers, json={"name": "DMX-999", "model": "M30"})
    assert resp.status_code == 403


def test_operator_can_create_and_update_drone(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    create = client.post(
        "/drones", headers=headers, json={"name": "DMX-050", "model": "M30", "latitude": 23.2, "longitude": 72.6}
    )
    assert create.status_code == 201
    drone_id = create.json()["id"]

    update = client.patch(f"/drones/{drone_id}", headers=headers, json={"status": "maintenance"})
    assert update.status_code == 200
    assert update.json()["status"] == "maintenance"


def test_create_drone_duplicate_uin_conflicts(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    payload = {"name": "DMX-A", "model": "M30", "uin": "DUP-001"}
    first = client.post("/drones", headers=headers, json=payload)
    assert first.status_code == 201

    payload["name"] = "DMX-B"
    second = client.post("/drones", headers=headers, json=payload)
    assert second.status_code == 409


def test_deactivate_drone_soft_deletes(client, operator_user, sample_drone):
    headers = auth_headers(client, operator_user.email)
    resp = client.delete(f"/drones/{sample_drone.id}", headers=headers)
    assert resp.status_code == 204

    listed = client.get("/drones", headers=headers).json()
    assert listed["total"] == 0

    listed_all = client.get("/drones?include_inactive=true", headers=headers).json()
    assert listed_all["total"] == 1
    assert listed_all["items"][0]["status"] == "offline"


def test_add_payload_and_maintenance_record(client, operator_user, sample_drone):
    headers = auth_headers(client, operator_user.email)
    payload_resp = client.post(
        f"/drones/{sample_drone.id}/payloads",
        headers=headers,
        json={"type": "camera", "name": "Wide Camera", "status": "calibrated"},
    )
    assert payload_resp.status_code == 201
    assert payload_resp.json()["name"] == "Wide Camera"

    maintenance_resp = client.post(
        f"/drones/{sample_drone.id}/maintenance",
        headers=headers,
        json={
            "service_date": "2026-01-15T00:00:00Z",
            "category": "routine",
            "technician": "A. Mehta",
            "notes": "Routine check",
        },
    )
    assert maintenance_resp.status_code == 201

    detail = client.get(f"/drones/{sample_drone.id}", headers=headers).json()
    assert len(detail["payloads"]) == 1
    assert len(detail["maintenance_records"]) == 1
