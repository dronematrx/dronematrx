"""Real-time mission notifications over the existing /ws/telemetry socket.

Routes schedule these with FastAPI BackgroundTasks so the broadcast runs on
the event loop after the response is sent:

    {"type": "mission", "event": "mission.state_changed", "mission_id": ..., "data": {...}}
"""
import logging
import uuid
from typing import Any

from fastapi import BackgroundTasks

from app.core.logging import log_event
from app.websocket.connection_manager import connection_manager

logger = logging.getLogger("dronematrx.mission.events")

MISSION_EVENTS = (
    "mission.state_changed",
    "mission.plan_updated",
    "mission.validated",
    "mission.drones_changed",
    "mission.wizard_saved",
)


async def _broadcast(organization_id: str, payload: dict[str, Any]) -> None:
    await connection_manager.broadcast_json(payload, organization_id=organization_id)


def publish(
    background_tasks: BackgroundTasks,
    organization_id: uuid.UUID,
    event: str,
    mission_id: uuid.UUID,
    data: dict[str, Any] | None = None,
) -> None:
    assert event in MISSION_EVENTS, event
    payload = {"type": "mission", "event": event, "mission_id": str(mission_id), "data": data or {}}
    log_event(logger, "mission.event_published", logging.DEBUG, kind=event, mission_id=mission_id)
    background_tasks.add_task(_broadcast, str(organization_id), payload)
