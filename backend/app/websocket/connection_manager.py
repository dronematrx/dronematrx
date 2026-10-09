import asyncio
import json

from fastapi import WebSocket


class ConnectionManager:
    """Single shared WebSocket fan-out. Each connection remembers the
    organization of the token that opened it so tenant-scoped events
    (mission updates) only reach that organization's clients."""

    def __init__(self):
        self._connections: dict[WebSocket, str | None] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, organization_id: str | None = None) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[websocket] = organization_id

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.pop(websocket, None)

    async def broadcast_json(self, payload: dict, organization_id: str | None = None) -> None:
        """Send to every client, or only to `organization_id`'s clients when given."""
        if not self._connections:
            return
        message = json.dumps(payload, default=str)
        dead: list[WebSocket] = []
        async with self._lock:
            connections = [
                ws for ws, org in self._connections.items() if organization_id is None or org == organization_id
            ]
        for ws in connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.pop(ws, None)

    @property
    def active_count(self) -> int:
        return len(self._connections)


connection_manager = ConnectionManager()
