"""Mission-control command adapters: the only way a mission-control command
reaches a vehicle. Each adapter says what it can do and how its commands are
acknowledged, so the UI never claims more than the transport delivers.

    BenchSitlAdapter      PX4 SITL via simhost (MAVLink). Ack = PX4 COMMAND_ACK.
    KinematicDemoAdapter  Sprint 4 in-process kinematic simulator via the NATS
                          command topic. No flight controller: ack = JetStream
                          publish ack only; the state change is confirmed from
                          telemetry.
    LockedHardwareAdapter Real hardware: no transport configured, refuses all.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.services.simulation.simhost import SimhostClient, SimhostError

MAV_RESULT = {0: "ACCEPTED", 1: "TEMPORARILY_REJECTED", 2: "DENIED", 3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS", 6: "CANCELLED"}


class AdapterError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class DispatchResult:
    accepted: bool
    ack_kind: str  # mavlink_command_ack | nats_publish_ack
    ack_result: int | None = None
    detail: str = ""
    timings: dict = field(default_factory=dict)


class CommandAdapter(ABC):
    name = "abstract"
    mode = "none"
    capabilities: frozenset[str] = frozenset()

    @abstractmethod
    def dispatch(self, command: str, params: dict) -> DispatchResult: ...


class BenchSitlAdapter(CommandAdapter):
    name = "bench_sitl"
    mode = "sitl"
    capabilities = frozenset({"ARM", "DISARM", "TAKEOFF", "HOLD", "RTL", "LAND", "GOTO"})
    SIMHOST_COMMAND = {"ARM": "arm_once", "DISARM": "disarm", "TAKEOFF": "takeoff", "HOLD": "hold", "RTL": "rtl", "LAND": "land", "GOTO": "goto"}

    def __init__(self, client: SimhostClient, session_id: str, fc_id: str):
        self.client, self.session_id, self.fc_id = client, session_id, fc_id

    def dispatch(self, command: str, params: dict) -> DispatchResult:
        p: dict[str, Any] = {}
        if command == "TAKEOFF":
            p = {"altitude_m": params["altitude_m"]}
        elif command == "GOTO":
            p = {"latitude": params["latitude"], "longitude": params["longitude"], "altitude_m": params["altitude_m"]}
        t0 = time.perf_counter()
        try:
            out = self.client.command(self.session_id, self.fc_id, self.SIMHOST_COMMAND[command], p)
        except SimhostError as exc:
            raise AdapterError("FC_UNREACHABLE" if exc.status is None else "FC_ERROR", str(exc)) from exc
        rtt_ms = (time.perf_counter() - t0) * 1000
        sim = out.get("timing") or {}
        result = out.get("result")
        handler = sim.get("handler_ms", 0.0)
        one_way = max(0.0, (rtt_ms - handler) / 2)  # HTTP transfer each way, symmetric on localhost
        timings = {"http_rtt_ms": round(rtt_ms, 3), "simhost_handler_ms": handler, "http_one_way_est_ms": round(one_way, 3)}
        if "recv_to_send_ms" in sim:
            timings["simhost_recv_to_mavlink_send_ms"] = sim["recv_to_send_ms"]
        if "send_to_ack_ms" in sim:
            timings["fc_ack_ms"] = sim["send_to_ack_ms"]  # MAVLink send -> PX4 COMMAND_ACK (one clock)
        accepted = result in (0, 5)
        return DispatchResult(accepted, "mavlink_command_ack", result if isinstance(result, int) else None,
                              f"PX4 COMMAND_ACK {MAV_RESULT.get(result, result)}", timings)


class KinematicDemoAdapter(CommandAdapter):
    name = "kinematic_demo"
    mode = "kinematic_demo"
    capabilities = frozenset({"HOLD", "RTL", "RESUME"})
    RUNTIME_COMMAND = {"HOLD": "HOLD", "RTL": "RTH", "RESUME": "RESUME"}

    def __init__(self, runtime: Any, loop: asyncio.AbstractEventLoop, drone_id: str, issued_by: str):
        self.runtime, self.loop, self.drone_id, self.issued_by = runtime, loop, drone_id, issued_by

    def dispatch(self, command: str, params: dict) -> DispatchResult:
        t0 = time.perf_counter()
        fut = asyncio.run_coroutine_threadsafe(self.runtime.send_command(self.drone_id, self.RUNTIME_COMMAND[command], self.issued_by), self.loop)
        try:
            fut.result(timeout=5)
        except LookupError as exc:
            raise AdapterError("NOT_IN_SIMULATION", str(exc)) from exc
        except Exception as exc:  # NatsUnavailableError, timeouts
            raise AdapterError("COMMAND_BUS_UNAVAILABLE", str(exc)) from exc
        ms = (time.perf_counter() - t0) * 1000
        return DispatchResult(True, "nats_publish_ack", None, "Published on the NATS command topic (JetStream ack); no flight controller",
                              {"nats_publish_ack_ms": round(ms, 3)})


class LockedHardwareAdapter(CommandAdapter):
    name = "hardware_locked"
    mode = "hardware"
    capabilities = frozenset()

    def dispatch(self, command: str, params: dict) -> DispatchResult:
        raise AdapterError("HARDWARE_UNAVAILABLE", "No real flight-controller transport is configured; hardware commands are refused")
