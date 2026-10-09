"""NATS JetStream drone bus.

Stream `DRONES` (configurable) captures every drone subject:
    drone.*.telemetry  drone.*.state  drone.*.alert  drone.*.command
Retention: limits-based, file storage, max_age = 24 h (the replay window),
duplicate_window = 2 min (publishers set Nats-Msg-Id so retries are
de-duplicated server-side), discard old.

Durable PUSH consumers, each delivered to its own queue group (= the PRD's
"consumer groups": several orchestrator / UI-gateway instances can share a
group). Explicit ack, ack_wait 30 s, max_deliver 5, so a crashed consumer's
un-acked messages are redelivered and a restarted consumer resumes from its
ack floor:
    orchestrator      drone.> (all kinds)             edge orchestrator / audit
    ui                telemetry + state + alert       feeds the browser WebSocket fan-out
    telemetry-writer  telemetry                       TimescaleDB persistence
    command-dispatch  command                         drone-side command handling
Push (not pull) because pull fetches are request/reply round trips that add
tens of ms; push delivery is immediate (measured in tests/benchmark).
Replay uses a separate ephemeral pull consumer, so it never disturbs durables.

Every message carries headers:
    Nats-Msg-Id      de-duplication id
    Dm-Published-Ns  publisher wall clock (ns) -> end-to-end latency on receive
    Dm-Link          transport that carried it (wifi | lte | lora)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable
from typing import Any

import nats
from nats.errors import ConnectionClosedError, NoServersError, TimeoutError as NatsTimeoutError
from nats.js import api
from nats.js.errors import NotFoundError

from app.core.logging import log_event
from app.messaging import subjects

logger = logging.getLogger("dronematrx.nats")

ACK_WAIT_S = 30.0
MAX_DELIVER = 5
DUPLICATE_WINDOW_S = 120.0


class NatsUnavailableError(RuntimeError):
    """The NATS server cannot be reached (callers degrade gracefully)."""


@dataclass
class DroneMessage:
    subject: str
    uin: str
    kind: str
    payload: dict[str, Any]
    stream_seq: int
    num_delivered: int
    published_ns: int | None
    received_ns: int
    link: str | None
    msg_id: str | None
    raw: Any = field(default=None, repr=False)

    @property
    def latency_ms(self) -> float | None:
        if self.published_ns is None:
            return None
        return (self.received_ns - self.published_ns) / 1e6

    async def ack(self) -> None:
        if self.raw is not None:
            await self.raw.ack()

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "topic": subjects.topic(self.uin, self.kind),
            "uin": self.uin,
            "kind": self.kind,
            "stream_seq": self.stream_seq,
            "delivered": self.num_delivered,
            "published_at": datetime.fromtimestamp(self.published_ns / 1e9, timezone.utc).isoformat() if self.published_ns else None,
            "latency_ms": round(self.latency_ms, 3) if self.latency_ms is not None else None,
            "link": self.link,
            "msg_id": self.msg_id,
            "payload": self.payload,
        }


MessageHandler = Callable[["DroneMessage"], Awaitable[None]]


def consumer_specs(prefix: str) -> dict[str, dict[str, Any]]:
    k = lambda kind: subjects.kind_filter(kind, prefix)  # noqa: E731
    return {
        "orchestrator": {"filters": [k(kind) for kind in subjects.KINDS], "deliver": api.DeliverPolicy.ALL},
        "ui": {"filters": [k("telemetry"), k("state"), k("alert")], "deliver": api.DeliverPolicy.NEW},
        "telemetry-writer": {"filters": [k("telemetry")], "deliver": api.DeliverPolicy.ALL},
        "command-dispatch": {"filters": [k("command")], "deliver": api.DeliverPolicy.NEW},
    }


class DroneBus:
    def __init__(self, url: str, stream: str = "DRONES", prefix: str = "", max_age_hours: float = 24.0, connect_timeout_s: float = 2.0):
        self.url = url
        self.stream = stream
        self.prefix = prefix
        self.max_age_hours = max_age_hours
        self.connect_timeout_s = connect_timeout_s
        self.nc: Any = None
        self.js: Any = None
        self._subs: dict[str, Any] = {}
        self.stats = {"published": 0, "publish_errors": 0, "consumed": 0, "duplicates_rejected": 0}
        from collections import deque

        self.publish_rtt_ms: deque[float] = deque(maxlen=1000)

    @property
    def connected(self) -> bool:
        return self.nc is not None and self.nc.is_connected

    async def connect(self) -> None:
        async def on_error(exc: Exception) -> None:
            log_event(logger, "nats.connection_error", logging.WARNING, error=type(exc).__name__)

        async def on_disconnect() -> None:
            log_event(logger, "nats.disconnected", logging.WARNING, url=self.url)

        async def on_reconnect() -> None:
            self._enable_nodelay()
            log_event(logger, "nats.reconnected", url=self.url)

        try:
            # Unlimited *re*connects once up, but the initial connect is bounded:
            # nats-py otherwise retries a dead server forever and startup hangs.
            self.nc = await asyncio.wait_for(nats.connect(
                self.url,
                connect_timeout=self.connect_timeout_s,
                drain_timeout=2,
                max_reconnect_attempts=-1,
                reconnect_time_wait=1,
                allow_reconnect=True,
                error_cb=on_error,
                disconnected_cb=on_disconnect,
                reconnected_cb=on_reconnect,
            ), timeout=self.connect_timeout_s * 2)
        except (NoServersError, OSError, asyncio.TimeoutError, NatsTimeoutError) as exc:
            self.nc = None
            log_event(logger, "nats.connection_failed", logging.WARNING, url=self.url, error=type(exc).__name__)
            raise NatsUnavailableError(f"NATS unavailable at {self.url}") from exc
        self.js = self.nc.jetstream()
        nodelay = self._enable_nodelay()
        log_event(logger, "nats.connected", url=self.url, stream=self.stream, tcp_nodelay=nodelay)

    def _enable_nodelay(self) -> bool:
        """Force TCP_NODELAY on the client socket.

        asyncio only sets it when getaddrinfo reports proto=IPPROTO_TCP; on
        Windows it reports 0, Nagle stays on, and every small JetStream
        publish then waits ~40 ms for a delayed ACK (measured: 43 ms vs 0.7 ms).
        """
        import socket

        transport = getattr(self.nc, "_transport", None)
        for attr in ("_io_writer", "_writer", "_transport"):
            inner = getattr(transport, attr, None)
            if inner is None:
                continue
            get = getattr(inner, "get_extra_info", None) or getattr(getattr(inner, "transport", None), "get_extra_info", None)
            sock = get("socket") if get else None
            if sock is not None:
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    return bool(sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY))
                except OSError:
                    return False
        return False

    async def close(self) -> None:
        if self.nc is not None:
            try:
                await self.nc.drain()
            except Exception:  # pragma: no cover - best effort shutdown
                pass
        self.nc = self.js = None
        self._subs.clear()

    def _require(self) -> None:
        if not self.connected:
            raise NatsUnavailableError("Not connected to NATS")

    # ------------------------------------------------------------ topology

    def deliver_subject(self, durable: str) -> str:
        return f"dmx.deliver.{self.stream}.{durable}"

    def stream_config(self) -> api.StreamConfig:
        return api.StreamConfig(
            name=self.stream,
            description="Drone MatrX drone telemetry/state/alert/command (Sprint 4)",
            subjects=subjects.stream_subjects(self.prefix),
            retention=api.RetentionPolicy.LIMITS,
            storage=api.StorageType.FILE,
            discard=api.DiscardPolicy.OLD,
            max_age=self.max_age_hours * 3600,
            duplicate_window=DUPLICATE_WINDOW_S,
            allow_direct=True,
        )

    async def ensure_topology(self) -> None:
        self._require()
        config = self.stream_config()
        try:
            await self.js.stream_info(self.stream)
            await self.js.update_stream(config)
        except NotFoundError:
            await self.js.add_stream(config)
        for name, spec in consumer_specs(self.prefix).items():
            consumer = api.ConsumerConfig(
                durable_name=name,
                description=f"Drone MatrX {name}",
                deliver_policy=spec["deliver"],
                ack_policy=api.AckPolicy.EXPLICIT,
                ack_wait=ACK_WAIT_S,
                max_deliver=MAX_DELIVER,
                filter_subjects=spec["filters"],
                max_ack_pending=20_000,
                deliver_subject=self.deliver_subject(name),
                deliver_group=name,
            )
            try:
                existing = await self.js.consumer_info(self.stream, name)
                if existing.config.deliver_subject != consumer.deliver_subject:
                    await self.js.delete_consumer(self.stream, name)  # stale definition: recreate
                    await self.js.add_consumer(self.stream, consumer)
            except NotFoundError:
                await self.js.add_consumer(self.stream, consumer)
        log_event(logger, "nats.topology_ready", stream=self.stream, subjects=",".join(config.subjects or []))

    async def delete_stream(self) -> None:
        self._require()
        try:
            await self.js.delete_stream(self.stream)
        except NotFoundError:
            pass

    # ---------------------------------------------------------- publishing

    async def publish(self, uin: str, kind: str, payload: dict[str, Any], msg_id: str | None = None, link: str | None = None) -> Any:
        """Publish to drone.{uin}.{kind}; returns the JetStream PubAck."""
        subject_name = subjects.subject(uin, kind, self.prefix)  # validates first
        self._require()
        headers = {
            "Nats-Msg-Id": msg_id or uuid.uuid4().hex,
            "Dm-Published-Ns": str(time.time_ns()),
        }
        if link:
            headers["Dm-Link"] = link
        body = json.dumps(payload, separators=(",", ":"), default=str).encode()
        started = time.perf_counter()
        try:
            ack = await self.js.publish(subject_name, body, headers=headers, stream=self.stream, timeout=2.0)
        except (ConnectionClosedError, NatsTimeoutError, NoServersError) as exc:
            self.stats["publish_errors"] += 1
            log_event(logger, "nats.publish_failed", logging.WARNING, subject=subject_name, error=type(exc).__name__)
            raise NatsUnavailableError(str(exc)) from exc
        self.publish_rtt_ms.append((time.perf_counter() - started) * 1000)
        self.stats["published"] += 1
        if getattr(ack, "duplicate", False):
            self.stats["duplicates_rejected"] += 1
        log_event(logger, "nats.publish", logging.DEBUG, subject=subject_name, seq=ack.seq, duplicate=getattr(ack, "duplicate", False))
        return ack

    # ----------------------------------------------------------- consuming

    def _decode(self, msg: Any) -> DroneMessage:
        received = time.time_ns()
        parsed = subjects.parse(msg.subject, self.prefix)
        headers = msg.headers or {}
        try:
            payload = json.loads(msg.data or b"{}")
        except json.JSONDecodeError:
            payload = {"_invalid_json": (msg.data or b"")[:200].decode(errors="replace")}
        meta = msg.metadata
        published = headers.get("Dm-Published-Ns")
        return DroneMessage(
            subject=msg.subject,
            uin=parsed.uin,
            kind=parsed.kind,
            payload=payload,
            stream_seq=meta.sequence.stream,
            num_delivered=meta.num_delivered,
            published_ns=int(published) if published else None,
            received_ns=received,
            link=headers.get("Dm-Link"),
            msg_id=headers.get("Nats-Msg-Id"),
            raw=msg,
        )

    async def subscribe(self, durable: str, handler: MessageHandler) -> None:
        """Join `durable`'s queue group; `handler` gets every delivered message
        and must ack it (un-acked messages are redelivered after ack_wait)."""
        self._require()
        if durable not in consumer_specs(self.prefix):
            raise ValueError(f"Unknown consumer {durable!r}")

        async def on_msg(msg: Any) -> None:
            try:
                decoded = self._decode(msg)
            except subjects.InvalidSubjectError:
                await msg.term()
                return
            self.stats["consumed"] += 1
            log_event(logger, "nats.consume", logging.DEBUG, consumer=durable, subject=decoded.subject, seq=decoded.stream_seq)
            try:
                await handler(decoded)
            except Exception:
                log_event(logger, "nats.handler_failed", logging.ERROR, consumer=durable, subject=decoded.subject)
                await msg.nak(delay=1.0)

        self._subs[durable] = await self.nc.subscribe(self.deliver_subject(durable), queue=durable, cb=on_msg, pending_msgs_limit=100_000)

    async def unsubscribe(self, durable: str) -> None:
        """Leave the queue group (e.g. consumer shutdown); the durable keeps its position."""
        sub = self._subs.pop(durable, None)
        if sub is not None:
            await sub.unsubscribe()

    async def replay(self, kind: str | None = None, uin: str | None = None, start_time: datetime | None = None,
                     start_seq: int | None = None, limit: int = 1000, timeout: float = 1.0) -> list[DroneMessage]:
        """Re-read stored messages with an ephemeral consumer (no effect on durables).

        `start_time` replays everything since then (within the 24 h retention);
        `start_seq` replays from a stream sequence.
        """
        self._require()
        if uin is not None and kind is not None:
            filter_subject = subjects.subject(uin, kind, self.prefix)
        elif kind is not None:
            filter_subject = subjects.kind_filter(kind, self.prefix)
        elif uin is not None:
            filter_subject = f"{self.prefix + '.' if self.prefix else ''}drone.{subjects.validate_uin(uin)}.*"
        else:
            filter_subject = f"{self.prefix + '.' if self.prefix else ''}drone.>"
        if start_seq is not None:
            config = api.ConsumerConfig(deliver_policy=api.DeliverPolicy.BY_START_SEQUENCE, opt_start_seq=start_seq)
        elif start_time is not None:
            config = api.ConsumerConfig(deliver_policy=api.DeliverPolicy.BY_START_TIME, opt_start_time=start_time.astimezone(timezone.utc).isoformat())
        else:
            config = api.ConsumerConfig(deliver_policy=api.DeliverPolicy.ALL)
        config.ack_policy = api.AckPolicy.NONE
        config.inactive_threshold = 30.0
        sub = await self.js.pull_subscribe(filter_subject, durable=None, stream=self.stream, config=config)
        messages: list[DroneMessage] = []
        try:
            while len(messages) < limit:
                wanted = min(500, limit - len(messages))
                try:
                    batch = await sub.fetch(wanted, timeout=timeout)
                except NatsTimeoutError:
                    break
                messages.extend(self._decode(m) for m in batch)
                if len(batch) < wanted:
                    break  # caught up with the stream: no need to wait out another fetch
        finally:
            await sub.unsubscribe()
        log_event(logger, "nats.replay", filter=filter_subject, messages=len(messages))
        return messages

    # -------------------------------------------------------------- status

    async def status(self) -> dict[str, Any]:
        if not self.connected:
            return {"connected": False, "url": self.url, "stream": self.stream, "stats": self.stats}
        info = await self.js.stream_info(self.stream)
        consumers = []
        for name in consumer_specs(self.prefix):
            try:
                ci = await self.js.consumer_info(self.stream, name)
                consumers.append(
                    {
                        "name": name,
                        "filters": ci.config.filter_subjects or [ci.config.filter_subject],
                        "delivered": ci.delivered.stream_seq if ci.delivered else 0,
                        "ack_floor": ci.ack_floor.stream_seq if ci.ack_floor else 0,
                        "num_pending": ci.num_pending,
                        "num_ack_pending": ci.num_ack_pending,
                        "num_redelivered": ci.num_redelivered,
                    }
                )
            except NotFoundError:
                consumers.append({"name": name, "missing": True})
        state = info.state
        return {
            "connected": True,
            "url": self.url,
            "stream": self.stream,
            "subjects": info.config.subjects,
            "topics": [f"drone/{{UIN}}/{k}" for k in subjects.KINDS],
            "retention": getattr(info.config.retention, "value", info.config.retention),
            "max_age_hours": (info.config.max_age or 0) / 3600,
            "duplicate_window_s": info.config.duplicate_window,
            "storage": getattr(info.config.storage, "value", info.config.storage),
            "messages": state.messages,
            "bytes": state.bytes,
            "first_seq": state.first_seq,
            "last_seq": state.last_seq,
            "consumers": consumers,
            "stats": self.stats,
            "publish_rtt_ms_avg": round(sum(self.publish_rtt_ms) / len(self.publish_rtt_ms), 3) if self.publish_rtt_ms else None,
            "publish_rtt_ms_max": round(max(self.publish_rtt_ms), 3) if self.publish_rtt_ms else None,
        }
