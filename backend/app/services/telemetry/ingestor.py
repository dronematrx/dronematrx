"""NATS `telemetry-writer` consumer -> TimescaleDB.

Messages are buffered and flushed every `flush_interval_s` (or at
`max_batch`). Acknowledgement semantics:
    valid + committed   -> ack
    invalid payload     -> term (never redelivered; counted + logged)
    DB failure          -> nak  (JetStream redelivers; inserts are idempotent)
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from collections import deque
from typing import Any

from app.core.logging import log_event
from app.messaging.bus import DroneBus, DroneMessage
from app.services.telemetry.store import InvalidTelemetryError, TelemetryStore, parse_record

logger = logging.getLogger("dronematrx.telemetry.ingest")


class TelemetryIngestor:
    def __init__(self, bus: DroneBus, store: TelemetryStore, durable: str = "telemetry-writer", flush_interval_s: float = 0.2, max_batch: int = 500):
        self.bus = bus
        self.store = store
        self.durable = durable
        self.flush_interval_s = flush_interval_s
        self.max_batch = max_batch
        self._buffer: list[DroneMessage] = []
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._running = False
        self.latencies_ms: deque[float] = deque(maxlen=5000)  # publish -> committed in TimescaleDB
        self.stats = {"received": 0, "inserted": 0, "duplicates": 0, "rejected": 0, "write_errors": 0}

    async def start(self) -> None:
        self._running = True
        await self.bus.subscribe(self.durable, self._on_message)
        self._task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        self._running = False
        await self.bus.unsubscribe(self.durable)
        if self._task is not None:
            self._wake.set()
            await self._task
            self._task = None
        await self.flush()

    async def _on_message(self, msg: DroneMessage) -> None:
        self._buffer.append(msg)
        self.stats["received"] += 1
        if len(self._buffer) >= self.max_batch:
            self._wake.set()

    async def _flush_loop(self) -> None:
        while self._running:
            try:
                await asyncio.wait_for(self._wake.wait(), self.flush_interval_s)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            await self.flush()

    async def flush(self) -> None:
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        known = await asyncio.to_thread(self.store.known_drones)
        ids = {str(m.payload.get("drone_id")) for m in batch if isinstance(m.payload, dict)}
        if any(i not in {str(k) for k in known} for i in ids):
            # Cache miss (e.g. a drone registered seconds ago): refresh once
            # before rejecting -- a rejected message is terminated, i.e. lost.
            known = await asyncio.to_thread(self.store.known_drones, 0.0)
        good, good_msgs = [], []
        for msg in batch:
            try:
                good.append(parse_record(msg.payload, known))
                good_msgs.append(msg)
            except InvalidTelemetryError as exc:
                self.stats["rejected"] += 1
                log_event(logger, "telemetry.ingest_rejected", logging.WARNING, subject=msg.subject, reason=str(exc))
                if msg.raw is not None:
                    await msg.raw.term()
        if not good:
            return
        try:
            inserted, duplicates = await asyncio.to_thread(self.store.write_batch, good)
        except Exception:
            self.stats["write_errors"] += 1
            for msg in good_msgs:
                if msg.raw is not None:
                    await msg.raw.nak(delay=1.0)
            return
        committed_ns = time.time_ns()
        for msg in good_msgs:
            await msg.ack()
            if msg.published_ns:
                self.latencies_ms.append((committed_ns - msg.published_ns) / 1e6)
        self.stats["inserted"] += inserted
        self.stats["duplicates"] += duplicates

    def snapshot(self) -> dict[str, Any]:
        lat = sorted(self.latencies_ms)
        return {
            **self.stats,
            "buffered": len(self._buffer),
            "ingest_latency_ms_avg": round(statistics.fmean(lat), 2) if lat else None,
            "ingest_latency_ms_p95": round(lat[max(0, int(0.95 * len(lat)) - 1)], 2) if lat else None,
            "ingest_latency_ms_max": round(lat[-1], 2) if lat else None,
        }
