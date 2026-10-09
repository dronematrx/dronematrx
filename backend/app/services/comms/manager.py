"""Communication fallback manager (2 Hz link-quality monitor).

Every 500 ms, for every drone, all three links are sampled and classified.

Fallback (down the chain) -- immediate, no debounce beyond the 500 ms poll:
    active link DOWN -> switch to the best lower link that is not DOWN.
    Timeline recorded per switch: breach injected/observed -> detected (this
    poll) -> decision -> transport activated.

Recovery (up the chain) -- hysteresis:
    a higher-preference link must be GOOD (recover thresholds, stricter than
    the breach line) continuously for `recovery_hold_s` (default 3 s) AND the
    current link must have been active for `min_dwell_s` (default 2 s).
    Recovery jumps straight to the best such link (LoRa -> Wi-Fi if Wi-Fi is
    healthy again), never to a link that is merely DEGRADED.

If every link is DOWN, the active transport is kept, an `all_links_lost`
alert is raised, and the FC's onboard failsafe is expected to act (PRD).
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.core.logging import log_event
from app.services.comms.links import CHAIN, DEFAULT_THRESHOLDS, LinkEnvironment, LinkSample, LinkThresholds, LinkType, classify
from app.services.comms.transports import Transport, default_transports

logger = logging.getLogger("dronematrx.comms")


@dataclass(frozen=True)
class CommsPolicy:
    poll_hz: float = 2.0
    recovery_hold_s: float = 3.0
    min_dwell_s: float = 2.0


@dataclass
class DroneLink:
    drone_id: str
    uin: str
    name: str
    index: int
    active: LinkType = LinkType.WIFI
    activated_at: float = 0.0
    samples: dict[LinkType, LinkSample] = field(default_factory=dict)
    quality: dict[LinkType, str] = field(default_factory=dict)
    good_since: dict[LinkType, float | None] = field(default_factory=dict)
    breach_seen_at: float | None = None
    all_lost: bool = False

    def as_dict(self) -> dict[str, Any]:
        active = self.samples.get(self.active)
        return {
            "drone_id": self.drone_id,
            "uin": self.uin,
            "name": self.name,
            "active_link": self.active.value,
            "rssi_dbm": round(active.rssi_dbm, 1) if active else None,
            "latency_ms": round(active.latency_ms, 1) if active else None,
            "packet_loss_pct": round(active.packet_loss_pct, 2) if active else None,
            "link_state": self.quality.get(self.active, "unknown"),
            "all_links_lost": self.all_lost,
            "links": {link.value: {**s.as_dict(), "quality": self.quality.get(link)} for link, s in self.samples.items()},
            "since": datetime.fromtimestamp(self.activated_at, timezone.utc).isoformat() if self.activated_at else None,
        }


EventSink = Callable[[dict[str, Any]], Awaitable[None]]


class CommunicationManager:
    def __init__(
        self,
        environment: LinkEnvironment | None = None,
        thresholds: dict[LinkType, LinkThresholds] | None = None,
        policy: CommsPolicy | None = None,
        transports: dict[LinkType, Transport] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.environment = environment or LinkEnvironment()
        self.thresholds = thresholds or DEFAULT_THRESHOLDS
        self.policy = policy or CommsPolicy()
        self.transports = transports or default_transports()
        self.clock = clock
        self.drones: dict[str, DroneLink] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=500)
        self.switches: deque[dict[str, Any]] = deque(maxlen=200)
        self.poll_times: deque[float] = deque(maxlen=200)
        self.poll_compute_ms: deque[float] = deque(maxlen=200)
        self.sinks: list[EventSink] = []
        self.bus: Any = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._seq = 0

    # --------------------------------------------------------------- roster

    def register(self, drone_id: str, uin: str, name: str) -> DroneLink:
        if drone_id not in self.drones:
            self.drones[drone_id] = DroneLink(drone_id, uin, name, index=len(self.drones), activated_at=self.clock())
        return self.drones[drone_id]

    def active_transport(self, drone_id: str) -> Transport:
        link = self.drones[drone_id].active if drone_id in self.drones else LinkType.WIFI
        return self.transports[link]

    async def send(self, drone_id: str, uin: str, kind: str, payload: dict[str, Any], msg_id: str | None = None) -> bool:
        """Route an outbound drone message through that drone's active transport."""
        state = self.drones.get(drone_id)
        if state is not None:
            sample = state.samples.get(state.active)
            if sample is not None and kind == "telemetry":
                payload = {**payload, "rssi": round(sample.rssi_dbm, 1)}
        return await self.active_transport(drone_id).send(self.bus, uin, kind, payload, msg_id=msg_id)

    # ---------------------------------------------------------------- core

    def _emit(self, kind: str, state: DroneLink, **details: Any) -> dict[str, Any]:
        self._seq += 1
        event = {
            "seq": self._seq,
            "kind": kind,
            "drone_id": state.drone_id,
            "uin": state.uin,
            "name": state.name,
            "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            **details,
        }
        self.events.append(event)
        level = logging.WARNING if kind in ("threshold_breach", "fallback", "all_links_lost") else logging.INFO
        log_event(logger, f"comms.{kind}", level, drone=state.name, **{k: v for k, v in details.items() if not isinstance(v, (dict, list))})
        return event

    def _activate(self, state: DroneLink, new: LinkType, now: float, reason: str, timeline: dict[str, float]) -> dict[str, Any]:
        previous = state.active
        decision_at = time.time()
        state.active = new  # the transport used by send() changes right here
        state.activated_at = now
        activated_at = time.time()
        injected = timeline.get("injected_at")
        record = {
            "drone_id": state.drone_id,
            "name": state.name,
            "from": previous.value,
            "to": new.value,
            "reason": reason,
            "breach_injected_at": injected,
            "detected_at": timeline.get("detected_at"),
            "decision_at": decision_at,
            "activated_at": activated_at,
            "detect_ms": round((timeline["detected_at"] - injected) * 1000, 1) if injected and timeline.get("detected_at") else None,
            "decision_ms": round((decision_at - timeline["detected_at"]) * 1000, 3) if timeline.get("detected_at") else None,
            "activation_ms": round((activated_at - decision_at) * 1000, 3),
            "total_ms": round((activated_at - injected) * 1000, 1) if injected else None,
        }
        self.switches.append(record)
        kind = "fallback" if CHAIN.index(new) > CHAIN.index(previous) else "recovery"
        return self._emit(kind, state, **{k: v for k, v in record.items() if k not in ("drone_id", "name")})

    def poll_once(self, now: float | None = None) -> list[dict[str, Any]]:
        now = self.clock() if now is None else now
        events: list[dict[str, Any]] = []
        for state in self.drones.values():
            for link in CHAIN:
                sample = self.environment.sample(state.drone_id, link, now, state.index)
                quality = classify(sample, self.thresholds[link])
                previous_quality = state.quality.get(link)
                state.samples[link] = sample
                state.quality[link] = quality
                if quality == "good":
                    if state.good_since.get(link) is None:
                        state.good_since[link] = now
                else:
                    state.good_since[link] = None
                if previous_quality is not None and previous_quality != quality and link == state.active:
                    events.append(self._emit("rssi_change", state, link=link.value, quality=quality, rssi_dbm=round(sample.rssi_dbm, 1)))

            active_quality = state.quality[state.active]
            detected_at = time.time()
            injected = self.environment.injected_at.get((state.drone_id, state.active))
            if active_quality == "down":
                if state.breach_seen_at is None:
                    state.breach_seen_at = now
                    events.append(self._emit("threshold_breach", state, link=state.active.value, rssi_dbm=round(state.samples[state.active].rssi_dbm, 1)))
                fallback = next((link for link in CHAIN[CHAIN.index(state.active) + 1 :] if state.quality[link] != "down"), None)
                if fallback is None:
                    # Try anything still alive (e.g. a higher link came back).
                    fallback = next((link for link in CHAIN if link != state.active and state.quality[link] != "down"), None)
                if fallback is not None:
                    events.append(self._activate(state, fallback, now, f"{state.active.value} below threshold", {"injected_at": injected, "detected_at": detected_at}))
                    state.breach_seen_at = None
                    state.all_lost = False
                elif not state.all_lost:
                    state.all_lost = True
                    events.append(self._emit("all_links_lost", state))
                continue

            state.breach_seen_at = None
            state.all_lost = False
            if now - state.activated_at < self.policy.min_dwell_s:
                continue
            for link in CHAIN[: CHAIN.index(state.active)]:
                since = state.good_since.get(link)
                if since is not None and now - since >= self.policy.recovery_hold_s:
                    restored = self.environment.injected_at.get((state.drone_id, link))
                    events.append(self._activate(state, link, now, f"{link.value} healthy for {self.policy.recovery_hold_s:g}s",
                                                 {"injected_at": restored, "detected_at": detected_at}))
                    break
        return events

    # ----------------------------------------------------------- 2 Hz loop

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        period = 1.0 / self.policy.poll_hz
        next_tick = time.monotonic()
        while self._running:
            started = time.perf_counter()
            self.poll_times.append(time.time())
            events = self.poll_once()
            self.poll_compute_ms.append((time.perf_counter() - started) * 1000)
            for event in events:
                for sink in self.sinks:
                    try:
                        await sink(event)
                    except Exception:  # pragma: no cover - sinks must not kill the loop
                        log_event(logger, "comms.sink_failed", logging.ERROR, kind=event["kind"])
            next_tick += period
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))

    def timing(self) -> dict[str, Any]:
        times = list(self.poll_times)
        intervals = [b - a for a, b in zip(times, times[1:])]
        return {
            "target_hz": self.policy.poll_hz,
            "measured_hz": round(1.0 / statistics.fmean(intervals), 3) if intervals else None,
            "max_interval_ms": round(max(intervals) * 1000, 1) if intervals else None,
            "poll_compute_ms_avg": round(statistics.fmean(self.poll_compute_ms), 3) if self.poll_compute_ms else None,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "drones": [s.as_dict() for s in sorted(self.drones.values(), key=lambda s: s.name)],
            "transports": {link.value: t.stats for link, t in self.transports.items()},
            "timing": self.timing(),
            "thresholds": {link.value: t.__dict__ for link, t in self.thresholds.items()},
            "policy": self.policy.__dict__,
        }
