import asyncio
import json
import logging

from fastapi import WebSocket

logger = logging.getLogger("dronematrx.websocket")
SEND_TIMEOUT_S = 5.0


class ConnectionManager:
    """Single shared WebSocket fan-out. Each connection remembers the
    organization of the token that opened it so tenant-scoped events
    (mission updates) only reach that organization's clients.

    Several tasks broadcast at once (telemetry, ORCA, comms, mission-control
    events), so sends to one socket are serialized by a per-connection lock.
    A client whose send fails or stalls is *closed*, not just forgotten: a
    forgotten-but-open socket would leave the browser connected to a stream
    that never sends again, with no close event to make it reconnect."""

    def __init__(self):
        self._connections: dict[WebSocket, str | None] = {}
        self._send_locks: dict[WebSocket, asyncio.Lock] = {}
        self._lock = asyncio.Lock()
        self.dropped = 0

    async def connect(self, websocket: WebSocket, organization_id: str | None = None) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[websocket] = organization_id
            self._send_locks[websocket] = asyncio.Lock()

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.pop(websocket, None)
            self._send_locks.pop(websocket, None)

    async def _send(self, ws: WebSocket, message: str) -> bool:
        lock = self._send_locks.get(ws)
        if lock is None:
            return True  # disconnected meanwhile
        try:
            async with lock:
                await asyncio.wait_for(ws.send_text(message), SEND_TIMEOUT_S)
            return True
        except Exception as exc:  # broken pipe, timeout, closed socket
            logger.warning("websocket send failed (%s): closing the connection so the client reconnects", type(exc).__name__)
            return False

    async def broadcast_json(self, payload: dict, organization_id: str | None = None) -> None:
        """Send to every client, or only to `organization_id`'s clients when given."""
        if not self._connections:
            return
        message = json.dumps(payload, default=str)
        async with self._lock:
            connections = [
                ws for ws, org in self._connections.items() if organization_id is None or org == organization_id
            ]
        results = await asyncio.gather(*(self._send(ws, message) for ws in connections))
        dead = [ws for ws, ok in zip(connections, results) if not ok]
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.pop(ws, None)
                    self._send_locks.pop(ws, None)
            self.dropped += len(dead)
            for ws in dead:
                try:
                    await asyncio.wait_for(ws.close(code=1011), 2.0)
                except Exception:
                    pass

    @property
    def active_count(self) -> int:
        return len(self._connections)


connection_manager = ConnectionManager()
