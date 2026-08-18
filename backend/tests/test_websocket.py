import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import auth_headers


def test_websocket_rejects_missing_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/telemetry"):
            pass


def test_websocket_rejects_invalid_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/telemetry?token=not-a-real-token"):
            pass


def test_websocket_accepts_valid_token(client, admin_user):
    headers = auth_headers(client, admin_user.email)
    token = headers["Authorization"].split(" ", 1)[1]

    with client.websocket_connect(f"/ws/telemetry?token={token}") as ws:
        assert ws is not None
