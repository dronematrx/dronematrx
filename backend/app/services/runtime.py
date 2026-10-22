"""Sprint 4 fleet runtime: wires simulator, ORCA, comms, NATS and TimescaleDB.

    simulator (10 Hz motion, ORCA velocity filter)
       | 1 Hz telemetry, 5 Hz ORCA state, ORCA/comms alerts
       v
    CommunicationManager.send -> active transport (Wi-Fi / LTE / LoRa)
       v
    NATS JetStream  drone.{UIN}.telemetry|state|alert|command
       |-- telemetry-writer -> TelemetryIngestor -> TimescaleDB hypertable
       |-- ui               -> TelemetryManager (WS + Drone row) and the ORCA/alert fan-out
       |-- orchestrator     -> message accounting / audit trail
       `-- command-dispatch -> applies HOLD / RTH / RESUME to the drone

If NATS is unreachable at startup the runtime runs DEGRADED: telemetry is
handed to the TelemetryManager directly (UI keeps working) but nothing is
persisted to TimescaleDB, and /messaging/status reports the outage.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from collections import Counter, deque
from typing import Any

from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.logging import log_event
from app.messaging.bus import DroneBus, DroneMessage, NatsUnavailableError
from app.schemas.telemetry import TelemetryUpdate
from app.services.comms.manager import CommunicationManager
from app.services.demo_scenario import DemoController
from app.services.orca.runtime import OrcaRuntime
from app.services.telemetry.ingestor import TelemetryIngestor
from app.services.telemetry.manager import TelemetryManager
from app.services.telemetry.simulator import SimulationTelemetryProvider
from app.services.telemetry.store import TelemetryStore
from app.websocket.connection_manager import ConnectionManager

logger = logging.getLogger("dronematrx.runtime")

COMMANDS = ("HOLD", "RTH", "RESUME")


def telemetry_payload(update: TelemetryUpdate) -> dict[str, Any]:
    data = update.model_dump(mode="json")
    data.update(lat=update.latitude, lon=update.longitude, alt=update.altitude, soc=update.battery)
    return data


def telemetry_from_payload(payload: dict[str, Any], previous: TelemetryUpdate | None) -> TelemetryUpdate:
    """Rebuild a TelemetryUpdate from a NATS frame (LoRa frames are compact)."""
    base = previous.model_dump() if previous else {"flight_mode": "MISSION", "gps_status": "3D_FIX", "armed": True, "link_quality": 50.0, "status": "active"}
    merged = {
        **base,
        **{k: v for k, v in payload.items() if k in TelemetryUpdate.model_fields},
        "latitude": payload.get("latitude", payload.get("lat")),
        "longitude": payload.get("longitude", payload.get("lon")),
        "altitude": payload.get("altitude", payload.get("alt")),
        "battery": payload.get("battery", payload.get("soc")),
    }
    return TelemetryUpdate.model_validate(merged)


class FleetRuntime:
    def __init__(self, settings: Settings, session_factory: sessionmaker, connections: ConnectionManager):
        self.settings = settings
        self.session_factory = session_factory
        self.connections = connections
        bus_args = (settings.NATS_URL, settings.NATS_STREAM, settings.NATS_SUBJECT_PREFIX, settings.NATS_MAX_AGE_HOURS, settings.NATS_CONNECT_TIMEOUT_S)
        # Two connections on purpose: drone-side publishing and orchestrator-side
        # consuming. Sharing one socket interleaves push deliveries/acks with
        # publish request/reply (measured: 18 ms vs 1.7 ms publish RTT).
        self.bus = DroneBus(*bus_args)
        self.consumer_bus = DroneBus(*bus_args)
        self.comms = CommunicationManager()
        self.comms.bus = self.bus
        self.store = TelemetryStore(session_factory)
        self.ingestor = TelemetryIngestor(self.consumer_bus, self.store)
        self.orca = OrcaRuntime(comms=self.comms, rate_hz=settings.ORCA_RATE_HZ)
        self.simulator = SimulationTelemetryProvider(session_factory, tick_seconds=settings.TELEMETRY_TICK_SECONDS, motion_hz=settings.ORCA_RATE_HZ)
        self.telemetry_manager = TelemetryManager(self.simulator, connections, session_factory)
        self.demo = DemoController(self)
        self.nats_ok = False
        self.state_cache: dict[str, dict[str, Any]] = {}
        self.alerts: deque[dict[str, Any]] = deque(maxlen=300)
        self.orchestrator_stats: Counter = Counter()
        self.orchestrator_latency_ms: deque[float] = deque(maxlen=2000)
        self.recent_messages: deque[dict[str, Any]] = deque(maxlen=60)
        self.commands_applied: deque[dict[str, Any]] = deque(maxlen=50)
        self._tasks: list[asyncio.Task] = []
        # The 10 Hz control loop never awaits the network: it only enqueues.
        # A separate publisher task drains this outbox to NATS.
        self._outbox: asyncio.Queue = asyncio.Queue(maxsize=20_000)
        self.outbox_dropped = 0
        self.running = False
        self.started_ns = time.time_ns()
        # Sprint 5: extra consumers of the `ui` stream (simulation orchestrator).
        self.message_listeners: list = []

    # ---------------------------------------------------------------- life

    async def start(self) -> None:
        self.started_ns = time.time_ns()
        self._loop = asyncio.get_running_loop()
        try:
            await self.bus.connect()
            await self.bus.ensure_topology()
            await self.consumer_bus.connect()
            self.nats_ok = True
        except NatsUnavailableError:
            self.nats_ok = False
            log_event(logger, "runtime.degraded", logging.WARNING, reason="nats_unavailable")
        if self.settings.ORCA_ENABLED:
            self.simulator.velocity_filter = self.orca.filter
            self.simulator.on_tick_committed = self.orca.commit_tick
        self.simulator.state_listener = self._on_states
        self.comms.sinks.append(self._on_comms_event)
        if self.nats_ok:
            await self.ingestor.start()
            await self.consumer_bus.subscribe("ui", self._on_ui_message)
            await self.consumer_bus.subscribe("orchestrator", self._on_orchestrator_message)
            await self.consumer_bus.subscribe("command-dispatch", self._on_command)
        await self.simulator.start(self._publish_telemetry)
        for s in self.simulator.states:
            self.comms.register(s.drone_id, s.uin, s.name)
        await self.comms.start()
        self._tasks.append(asyncio.create_task(self._publisher_loop()))
        self._tasks.append(asyncio.create_task(self._broadcast_loop()))
        self._tasks.append(asyncio.create_task(self._retention_loop()))
        self.running = True
        # Move everything allocated during startup out of GC tracking: shorter
        # collection pauses on the event loop that runs the 10 Hz control cycle.
        import gc

        gc.collect()
        gc.freeze()
        log_event(logger, "runtime.started", nats=self.nats_ok, orca=self.settings.ORCA_ENABLED, drones=len(self.simulator.states))

    async def stop(self) -> None:
        self.running = False
        await self.demo.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.comms.stop()
        await self.simulator.stop()
        if self.nats_ok:
            await self.ingestor.stop()
        await self.consumer_bus.close()
        await self.bus.close()

    # ------------------------------------------------------------ outbound

    async def _send(self, drone_id: str, uin: str, kind: str, payload: dict[str, Any], msg_id: str | None = None) -> bool:
        try:
            return await self.comms.send(drone_id, uin, kind, payload, msg_id=msg_id)
        except NatsUnavailableError:
            return False

    def _enqueue(self, drone_id: str, uin: str, kind: str, payload: dict[str, Any], msg_id: str | None = None, fallback: TelemetryUpdate | None = None) -> None:
        try:
            self._outbox.put_nowait((drone_id, uin, kind, payload, msg_id, fallback))
        except asyncio.QueueFull:
            self.outbox_dropped += 1
            log_event(logger, "runtime.outbox_full", logging.WARNING, kind=kind)

    async def _publisher_loop(self) -> None:
        while True:
            drone_id, uin, kind, payload, msg_id, fallback = await self._outbox.get()
            ok = await self._send(drone_id, uin, kind, payload, msg_id)
            if not ok and fallback is not None:
                await self.telemetry_manager._on_update(fallback)  # keep the UI alive if NATS drops

    async def _publish_telemetry(self, update: TelemetryUpdate) -> None:
        if not self.nats_ok:
            await self.telemetry_manager._on_update(update)  # degraded mode: UI only
            return
        if update.drone_id not in self.comms.drones:
            self.comms.register(update.drone_id, update.uin or update.drone_id, update.name or update.drone_id)
        uin = update.uin or update.drone_id
        self._enqueue(update.drone_id, uin, "telemetry", telemetry_payload(update), msg_id=f"{uin}-{update.timestamp.isoformat()}", fallback=update)

    async def _on_states(self, states: list[Any]) -> None:
        """5 Hz: publish ORCA state per drone, and ORCA events as alerts."""
        if not self.nats_ok:
            for s in states:
                self.state_cache[s.drone_id] = self.orca.drone_state(s)
            return
        for s in states:
            self._enqueue(s.drone_id, s.uin, "state", self.orca.drone_state(s))
        by_id = {s.drone_id: s for s in states}
        for event in self.orca.drain_events():
            owner = by_id.get(event.drones[0]) if event.drones else None
            if owner is not None:
                self._enqueue(owner.drone_id, owner.uin, "alert", {"type": "orca", **self.orca.event_dict(event)})

    async def _on_comms_event(self, event: dict[str, Any]) -> None:
        if event["kind"] in ("fallback", "recovery", "threshold_breach", "all_links_lost") and self.nats_ok:
            await self._send(event["drone_id"], event["uin"], "alert", {"type": "comms", **event})
        if event["kind"] in ("fallback", "recovery", "all_links_lost"):
            await self.connections.broadcast_json({"type": "comms_event", "data": event})

    async def reset_fleet(self, organization_id: Any) -> dict[str, Any]:
        """Reset the simulated fleet to its seeded state (deterministic demos/tests)."""
        from app.services.demo_fleet import reset_fleet

        await self.demo.stop()

        def db_reset() -> tuple[int, dict]:
            with self.session_factory() as db:
                count = reset_fleet(db, organization_id)
            return count, self.simulator.initial_states()

        count, states = await asyncio.to_thread(db_reset)
        # In-memory swap on the loop thread (the 10 Hz loop iterates these).
        self.simulator._states = states
        self.orca.bands.assignments.clear()
        self.orca.rth = type(self.orca.rth)(self.orca.rth.policy)
        self.comms.environment.overrides.clear()
        self.comms.drones.clear()
        for s in states.values():
            self.comms.register(s.drone_id, s.uin, s.name)
        self.state_cache.clear()
        log_event(logger, "runtime.fleet_reset", drones=count, flying=len(states))
        return {"reset": count, "simulated": len(states)}

    def add_drone(self, drone_id: Any) -> None:
        """A drone registered through the API joins the live simulation/comms.

        Called from FastAPI's threadpool: the DB read happens here, the state
        mutation is handed to the event loop (the 10 Hz loop iterates these
        dicts, so they must only change on the loop thread).
        """
        states = self.simulator.load_states([drone_id])

        def attach() -> None:
            self.simulator.attach(states)
            for s in states:
                self.comms.register(s.drone_id, s.uin, s.name)

        self._loop.call_soon_threadsafe(attach)

    def remove_drone(self, drone_id: Any) -> None:
        def detach() -> None:
            self.simulator.unregister_drone(drone_id)
            self.comms.drones.pop(str(drone_id), None)
            self.orca.bands.release(str(drone_id))
            self.orca.rth.complete(str(drone_id))

        self._loop.call_soon_threadsafe(detach)

    async def send_command(self, drone_id: str, command: str, issued_by: str) -> dict[str, Any]:
        if command not in COMMANDS:
            raise ValueError(f"command must be one of {COMMANDS}")
        state = next((s for s in self.simulator.states if s.drone_id == drone_id), None)
        if state is None:
            raise LookupError("Drone is not active in the simulation")
        payload = {"command": command, "drone_id": drone_id, "issued_by": issued_by, "issued_at": time.time()}
        if not self.nats_ok:
            raise NatsUnavailableError("Command bus unavailable")
        await self.bus.publish(state.uin, "command", payload)  # commands go on the command topic, not over the drone link
        return payload

    # ------------------------------------------------------------- inbound

    async def _on_ui_message(self, msg: DroneMessage) -> None:
        try:
            for listener in self.message_listeners:
                try:
                    await listener(msg)
                except Exception:  # never let a listener stall the UI stream
                    logger.exception("ui message listener failed")
            if msg.kind == "telemetry":
                previous = self.telemetry_manager.latest.get(str(msg.payload.get("drone_id")))
                update = telemetry_from_payload(msg.payload, previous)
                await self.telemetry_manager._on_update(update)
            elif msg.kind == "state":
                self.state_cache[str(msg.payload.get("drone_id"))] = {
                    **msg.payload, "link": msg.link, "latency_ms": round(msg.latency_ms or 0, 2), "_rx": time.monotonic(),
                }
            elif msg.kind == "alert":
                alert = {**msg.payload, "uin": msg.uin, "link": msg.link, "stream_seq": msg.stream_seq}
                self.alerts.append(alert)
                await self.connections.broadcast_json({"type": "drone_alert", "data": alert})
        finally:
            await msg.ack()

    async def _on_orchestrator_message(self, msg: DroneMessage) -> None:
        self.orchestrator_stats[msg.kind] += 1
        # Delivery latency only for live, first-delivery messages. Backlog a
        # durable consumer catches up on after a restart is replay, not latency.
        if msg.latency_ms is not None and msg.num_delivered == 1 and (msg.published_ns or 0) >= self.started_ns:
            self.orchestrator_latency_ms.append(msg.latency_ms)
        elif msg.published_ns and msg.published_ns < self.started_ns:
            self.orchestrator_stats["replayed_backlog"] += 1
        self.recent_messages.append({k: v for k, v in msg.as_dict().items() if k != "payload"} | {"summary": _summary(msg)})
        await msg.ack()

    async def _on_command(self, msg: DroneMessage) -> None:
        command = str(msg.payload.get("command", "")).upper()
        state = next((s for s in self.simulator.states if s.drone_id == msg.payload.get("drone_id")), None)
        if state is not None and command in COMMANDS:
            if command == "HOLD":
                state.resume_mode = state.mode if state.mode not in ("hold", "rth", "landed") else state.resume_mode
                state.mode = "hold"
            elif command == "RESUME" and state.mode == "hold":
                state.mode = state.resume_mode or "patrol"
            elif command == "RTH":
                self.orca.request_rth(state, "normal")
            self.commands_applied.append({"command": command, "drone": state.name, "uin": msg.uin, "seq": msg.stream_seq, "latency_ms": round(msg.latency_ms or 0, 2)})
            log_event(logger, "runtime.command_applied", command=command, drone=state.name)
        await msg.ack()

    # --------------------------------------------------------------- loops

    async def _broadcast_loop(self) -> None:
        while True:
            await asyncio.sleep(0.2)
            if self.connections.active_count == 0:
                continue
            await self.connections.broadcast_json({"type": "orca", "data": self.orca_view()})
            await self.connections.broadcast_json({"type": "comms", "data": self.comms.snapshot()})

    async def _retention_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.store.enforce_retention)
            except Exception:  # pragma: no cover - never kill the runtime over housekeeping
                log_event(logger, "telemetry.retention_failed", logging.ERROR)
            await asyncio.sleep(3600)

    # ------------------------------------------------------------- views

    def orca_view(self) -> dict[str, Any]:
        """UI snapshot: per-drone ORCA state as received over NATS + live engine summary."""
        live = self.orca.snapshot(self.simulator.states)
        now = time.monotonic()
        drones = []
        for d in live["drones"]:
            cached = self.state_cache.get(d["drone_id"])
            # Prefer the NATS-delivered state; fall back to the engine view when
            # it is stale (e.g. on LoRa, where full state frames are not sent).
            if cached is not None and now - cached.get("_rx", 0) < 1.0:
                drones.append({k: v for k, v in cached.items() if k != "_rx"} | {"via": "nats"})
            else:
                drones.append(d | {"via": "engine"})
        return {**live, "drones": drones, "source": "nats" if self.nats_ok else "direct", "demo": self.demo.status.as_dict()}

    def messaging_view(self) -> dict[str, Any]:
        lat = sorted(self.orchestrator_latency_ms)
        return {
            "orchestrator": {
                "by_kind": dict(self.orchestrator_stats),
                "latency_ms_avg": round(statistics.fmean(lat), 3) if lat else None,
                "latency_ms_p95": round(lat[max(0, int(0.95 * len(lat)) - 1)], 3) if lat else None,
                "latency_ms_max": round(lat[-1], 3) if lat else None,
                "over_50ms": sum(1 for x in lat if x > 50),
                "samples": len(lat),
            },
            "recent": list(self.recent_messages)[-25:],
            "ingestor": self.ingestor.snapshot(),
            "outbox": {"queued": self._outbox.qsize(), "dropped": self.outbox_dropped},
            "commands": list(self.commands_applied),
        }


def _summary(msg: DroneMessage) -> str:
    p = msg.payload
    if msg.kind == "telemetry":
        return f"lat {p.get('lat')} lon {p.get('lon')} alt {p.get('alt')} soc {p.get('soc')} rssi {p.get('rssi')}"
    if msg.kind == "state":
        return f"mode {p.get('mode')} band {p.get('band')} v {p.get('safe_velocity')} predicted {p.get('collision_predicted')}"
    if msg.kind == "alert":
        return f"{p.get('type')}:{p.get('kind')} {p.get('drones') or p.get('name') or ''}"
    return f"{p.get('command')}"
