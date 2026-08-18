from tests.conftest import auth_headers


def test_login_success(client, admin_user):
    resp = client.post("/auth/login", json={"email": admin_user.email, "password": "Password123!"})
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["user"]["role"] == "ADMIN"


def test_login_wrong_password(client, admin_user):
    resp = client.post("/auth/login", json={"email": admin_user.email, "password": "wrong"})
    assert resp.status_code == 401


def test_login_unknown_email(client):
    resp = client.post("/auth/login", json={"email": "nobody@dronematrx-qa.com", "password": "whatever"})
    assert resp.status_code == 401


def test_me_requires_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_returns_current_user(client, admin_user):
    headers = auth_headers(client, admin_user.email)
    resp = client.get("/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == admin_user.email


def test_invalid_token_rejected(client):
    resp = client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_observer_can_read_but_not_write(client, observer_user, sample_drone):
    headers = auth_headers(client, observer_user.email)
    read_resp = client.get("/drones", headers=headers)
    assert read_resp.status_code == 200

    write_resp = client.patch(f"/drones/{sample_drone.id}", headers=headers, json={"status": "maintenance"})
    assert write_resp.status_code == 403
