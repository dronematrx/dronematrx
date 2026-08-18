"""Telemetry provider interface.

Every telemetry source -- the local simulator today, MAVLink/MAVSDK or NATS
tomorrow -- implements this same tiny contract. Nothing downstream (the
WebSocket layer, the DB persistence step, the frontend) is allowed to know
which concrete provider is running; they only ever see `TelemetryUpdate`
objects delivered to the registered callback.
"""
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from app.schemas.telemetry import TelemetryUpdate

TelemetryCallback = Callable[[TelemetryUpdate], Awaitable[None]]


class TelemetryProvider(ABC):
    @abstractmethod
    async def start(self, on_update: TelemetryCallback) -> None:
        """Begin producing telemetry, invoking on_update for every reading."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop producing telemetry and release any resources."""
