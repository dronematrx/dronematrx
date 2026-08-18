import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import decode_token
from app.websocket.connection_manager import connection_manager

router = APIRouter()


@router.websocket("/ws/telemetry")
async def telemetry_ws(websocket: WebSocket, token: str | None = None) -> None:
    """Broadcast-only telemetry stream. Auth token is passed as a query param
    (?token=...) since browser WebSocket clients cannot set an Authorization
    header. Abstract enough that a future MAVLink/NATS-backed TelemetryManager
    can feed this same endpoint without any client-side changes.
    """
    if token is None:
        await websocket.close(code=4401, reason="Missing auth token")
        return

    try:
        decode_token(token)
    except jwt.PyJWTError:
        await websocket.close(code=4401, reason="Invalid or expired token")
        return

    await connection_manager.connect(websocket)
    try:
        while True:
            # Clients don't need to send anything; we just keep the socket
            # open and drop it if the client goes away.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await connection_manager.disconnect(websocket)
