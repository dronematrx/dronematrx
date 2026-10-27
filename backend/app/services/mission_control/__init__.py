"""Sprint 6 mission control: fleet view, flight-controller binding, pre-arm,
commands, video wall and detection feed. See docs/sprint6/IMPLEMENTATION_PLAN.md."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.orm import sessionmaker

from app.services.simulation.simhost import SimhostClient

logger = logging.getLogger("dronematrx.mission_control")


class MissionControl:
    """Long-lived services owned by the app (app.state.mc)."""

    def __init__(self, session_factory: sessionmaker, connections: Any, runtime: Any, client: SimhostClient | None = None):
        from app.services.mission_control.commands import CommandService
        from app.services.mission_control.detections import DetectionService

        self.session_factory = session_factory
        self.connections = connections
        self.runtime = runtime
        self.client = client or SimhostClient()
        self.commands = CommandService(session_factory, connections, lambda: self.runtime, self.client)
        self.detections = DetectionService(session_factory, connections)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self.commands.loop = asyncio.get_running_loop()
        if self.runtime is not None:
            self.runtime.message_listeners.append(self.detections.on_bus_message)
        self._task = asyncio.create_task(self._reconcile_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _reconcile_loop(self) -> None:
        from app.services.mission_control import fc as fcsvc

        def once():
            with self.session_factory() as db:
                return fcsvc.reconcile(db, self.client)

        while True:
            try:
                changed = await asyncio.to_thread(once)
                if any(changed.values()):
                    logger.info("binding reconcile: %s", changed)
            except Exception:  # never let reconcile kill the app
                logger.exception("binding reconcile failed")
            await asyncio.sleep(10)
