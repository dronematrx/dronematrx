"""Tracked command lifecycle for single-drone and fleet-wide commands.

    request -> auth/RBAC (route) -> drone + org -> adapter for the drone's
    telemetry source -> capability -> state checks (prearm for ARM, zones for
    GOTO) -> drone_commands row `requested` -> adapter.dispatch ->
    `accepted` | `rejected` (PX4 COMMAND_ACK) or `dispatched` (bus ack only)
    -> watcher: telemetry shows the expected state -> `confirmed` | `timed_out`

An API response is never treated as proof that the aircraft did anything:
`confirmed` is set only from telemetry received after the dispatch.

Latency boundaries recorded in drone_commands.timings (ms):

    backend_pre_dispatch_ms        route entry -> adapter call (validation, DB)
    http_rtt_ms / http_one_way_est_ms   backend <-> simhost HTTP (one-way = (rtt - handler) / 2)
    simhost_recv_to_mavlink_send_ms     simhost receipt -> COMMAND_LONG written to PX4's socket
    request_to_fc_dispatch_ms      = pre_dispatch + one_way + recv_to_send  ("reaches the flight controller")
    fc_ack_ms                      MAVLink send -> PX4 COMMAND_ACK (same clock)
    request_to_ack_ms              route entry -> ack known in the backend
    observed_state_ms              dispatch -> first telemetry showing the expected state
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.drone import Drone
from app.models.mission_control import DroneCommand, FlightControllerBinding
from app.models.user import User
from app.services.mission_control import fc as fcsvc
from app.services.mission_control import fleet as fleetsvc
from app.services.mission_control import prearm as prearmsvc
from app.services.mission_control.adapters import (AdapterError, BenchSitlAdapter, CommandAdapter, KinematicDemoAdapter,
                                                   LockedHardwareAdapter)
from app.services.simulation.simhost import SimhostClient, SimhostError

logger = logging.getLogger("dronematrx.mission_control")

SINGLE_COMMANDS = ("ARM", "DISARM", "TAKEOFF", "HOLD", "RTL", "LAND", "GOTO", "RESUME")
FLEET_COMMANDS = {"HOLD_ALL": "HOLD", "RTL_ALL": "RTL", "LAND_ALL": "LAND"}
IN_FLIGHT = ("requested", "dispatched", "accepted")


class CommandError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, extra: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra or {}


def _ms(t0: float, t1: float) -> float:
    return round((t1 - t0) * 1000, 3)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    k = 111_320.0
    return math.hypot((lat2 - lat1) * k, (lon2 - lon1) * k * math.cos(math.radians(lat1)))


def _not_before(ts: Any, ref: Any) -> bool:
    try:
        return ts >= ref
    except TypeError:  # naive producer timestamp: treat as UTC
        return ts.replace(tzinfo=ref.tzinfo) >= ref


def command_view(c: DroneCommand, drone_name: str | None = None) -> dict:
    return {"id": str(c.id), "correlation_id": str(c.correlation_id), "fleet_command_id": str(c.fleet_command_id) if c.fleet_command_id else None,
            "drone_id": str(c.drone_id), "drone_name": drone_name, "command": c.command, "params": c.params, "mode": c.mode,
            "adapter": c.adapter, "state": c.state, "ack_result": c.ack_result, "failure_reason": c.failure_reason,
            "expected_state": c.expected_state, "timings": c.timings, "user_role": c.user_role, "user_id": str(c.user_id) if c.user_id else None,
            "requested_at": c.requested_at.isoformat() if c.requested_at else None,
            "dispatched_at": c.dispatched_at.isoformat() if c.dispatched_at else None,
            "acknowledged_at": c.acknowledged_at.isoformat() if c.acknowledged_at else None,
            "completed_at": c.completed_at.isoformat() if c.completed_at else None}


class CommandService:
    def __init__(self, session_factory: sessionmaker, connections: Any, runtime_getter, client: SimhostClient | None = None):
        self.session_factory = session_factory
        self.connections = connections
        self.runtime_getter = runtime_getter
        self.client = client or SimhostClient()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="mc-cmd")
        self._watchers: set[asyncio.Task] = set()

    # ------------------------------------------------------------ helpers

    def _live(self, drone_id: str) -> Any:
        return fleetsvc.latest_telemetry(self.runtime_getter()).get(drone_id)

    def _received(self, drone_id: str) -> Any:
        """Backend receive time of the drone's latest frame (freshness clock)."""
        return fleetsvc.received_times(self.runtime_getter()).get(drone_id)

    def resolve(self, db: Session, drone: Drone, user: User) -> tuple[CommandAdapter | None, str, FlightControllerBinding | None]:
        runtime = self.runtime_getter()
        binding = fcsvc.active_binding(db, drone.id)
        source = fleetsvc.telemetry_source(str(drone.id), binding, fleetsvc.kinematic_drone_ids(runtime))
        if source == "sitl":
            if binding.state != "bound":
                return None, source, binding
            parsed = fcsvc.validate_endpoint("sitl", binding.endpoint)
            return BenchSitlAdapter(self.client, parsed["session"], parsed["fc"]), source, binding
        if source == "kinematic_demo":
            return KinematicDemoAdapter(runtime, self.loop, str(drone.id), user.email), source, binding
        if source == "hardware":
            return LockedHardwareAdapter(), source, binding
        return None, source, binding

    def _flying(self, source: str, live: Any) -> bool:
        if live is None:
            return False
        if source == "kinematic_demo":
            return live.status == "active" and live.flight_mode != "STANDBY"
        return bool(live.armed) and (live.altitude or 0) > 0.5

    def fc_health(self, binding: FlightControllerBinding | None) -> tuple[dict | None, str | None]:
        if binding is None or binding.connection_type != "sitl" or binding.state != "bound":
            return None, None
        parsed = fcsvc.validate_endpoint("sitl", binding.endpoint)
        try:
            return self.client.prearm(parsed["session"], parsed["fc"]), None
        except SimhostError as exc:
            return None, str(exc)

    def prearm(self, db: Session, drone: Drone, expected_mode: str | None) -> dict:
        binding = fcsvc.active_binding(db, drone.id)
        fc, err = self.fc_health(binding)
        return prearmsvc.checklist(db, drone, binding, self._live(str(drone.id)), fc, err, expected_mode, self._received(str(drone.id)))

    # --------------------------------------------------------- validation

    def validate(self, db: Session, drone: Drone, user: User, command: str, params: dict, confirm_mode: str | None,
                 adapter: CommandAdapter | None, source: str) -> tuple[dict, dict]:
        """Returns (normalised params, expected telemetry state). Raises CommandError."""
        if adapter is None:
            raise CommandError("NO_FLIGHT_CONTROLLER", f"{drone.name} has no connected flight controller ({source})")
        if command not in adapter.capabilities:
            raise CommandError("UNSUPPORTED", f"{command} is not supported by {drone.name}'s {adapter.name} adapter", 409,
                               {"supported": sorted(adapter.capabilities)})
        if source in ("sitl", "hardware") and confirm_mode != source:
            raise CommandError("MODE_NOT_CONFIRMED", f"Confirm the operating mode: {drone.name} is {source.upper()}", 409)
        live = self._live(str(drone.id))
        state, age = fleetsvc.connection_state(fleetsvc.freshness_time(live, self._received(str(drone.id))), fleetsvc.now_utc())
        if state != "connected":
            raise CommandError("TELEMETRY_STALE", f"{drone.name} telemetry is {state}; commands need a live link", 409)
        flying = self._flying(source, live)
        p: dict = {}
        if command == "ARM":
            pre = self.prearm(db, drone, confirm_mode)
            if not pre["can_arm"]:
                raise CommandError("PREARM_FAILED", "Pre-arm checks block arming: " + ", ".join(pre["blocking"]), 409, {"prearm": pre})
            return p, {"armed": True}
        if command == "DISARM":
            fc, _ = self.fc_health(fcsvc.active_binding(db, drone.id))
            if fc is None or fc.get("landed_state") != 1:
                raise CommandError("NOT_ON_GROUND", "Disarm is allowed only on the ground (landed state reported by the flight controller)")
            return p, {"armed": False}
        if command == "TAKEOFF":
            alt = float(params.get("altitude_m", 15))
            if not 2 <= alt <= 120:
                raise CommandError("INVALID_PARAMS", "Take-off altitude must be 2-120 m", 422)
            if not live.armed:
                raise CommandError("INVALID_STATE", "Arm before take-off")
            return {"altitude_m": alt}, {"min_altitude_m": min(alt * 0.5, alt - 1)}
        if command in ("HOLD", "RTL", "LAND", "RESUME"):
            if not flying:
                raise CommandError("INVALID_STATE", f"{drone.name} is not flying; {command} does not apply")
            expect = {"HOLD": {"flight_mode": ["HOLD"]}, "RTL": {"flight_mode": ["RTL", "LAND"]},
                      "LAND": {"flight_mode": ["LAND"], "or_disarmed": True}, "RESUME": {"flight_mode_not": ["HOLD"]}}[command]
            return p, expect
        if command == "GOTO":
            try:
                lat, lon = float(params["latitude"]), float(params["longitude"])
                alt = float(params.get("altitude_m", 30))
            except (KeyError, TypeError, ValueError) as exc:
                raise CommandError("INVALID_PARAMS", "GOTO needs numeric latitude, longitude, altitude_m", 422) from exc
            if not (-90 <= lat <= 90 and -180 <= lon <= 180) or any(map(math.isnan, (lat, lon, alt))):
                raise CommandError("INVALID_PARAMS", "Coordinates out of range", 422)
            if not 5 <= alt <= 120:
                raise CommandError("INVALID_PARAMS", "GOTO altitude must be 5-120 m above launch", 422)
            if not flying:
                raise CommandError("INVALID_STATE", f"{drone.name} must be airborne for GOTO")
            if distance_m(live.latitude, live.longitude, lat, lon) > 5000:
                raise CommandError("OUT_OF_RANGE", "GOTO destination is more than 5 km away", 422)
            z = prearmsvc.zone_check(db, drone.organization_id, lon, lat, path_from=(live.longitude, live.latitude))
            if not z["ok"]:
                why = z["forbidden_zones"] and f"inside {', '.join(z['forbidden_zones'])}" or z["path_crosses"] and \
                    f"path crosses {', '.join(z['path_crosses'])}" or "outside the inclusion geofence"
                raise CommandError("ZONE_VIOLATION", f"GOTO rejected: destination {why}", 409, {"zones": z})
            return {"latitude": lat, "longitude": lon, "altitude_m": alt}, {"within_m": 6.0, "latitude": lat, "longitude": lon}
        raise CommandError("UNSUPPORTED", f"Unknown command {command}", 422)

    # ------------------------------------------------------------ execute

    def execute(self, user: User, drone_id: uuid.UUID, command: str, params: dict, confirm_mode: str | None,
                idempotency_key: str | None, t_received: float, fleet_command_id: uuid.UUID | None = None) -> dict:
        """Synchronous (runs in a worker thread). Returns the command view."""
        with self.session_factory() as db:
            user = db.merge(user)
            drone = db.get(Drone, drone_id)
            if drone is None or drone.organization_id != user.organization_id or not drone.active:
                raise CommandError("DRONE_NOT_FOUND", "Drone not found", 404)
            if idempotency_key:
                existing = db.execute(select(DroneCommand).where(DroneCommand.idempotency_key == idempotency_key)).scalars().first()
                if existing is not None:
                    if existing.drone_id != drone.id or existing.command != command or existing.user_id != user.id:
                        raise CommandError("IDEMPOTENCY_CONFLICT", "Idempotency key already used for a different command", 409)
                    return {**command_view(existing, drone.name), "replayed": True}
            adapter, source, _binding = self.resolve(db, drone, user)
            row = DroneCommand(organization_id=user.organization_id, idempotency_key=idempotency_key, fleet_command_id=fleet_command_id,
                               drone_id=drone.id, user_id=user.id, user_role=user.role, command=command, params=params or {},
                               mode=source, adapter=adapter.name if adapter else "none", state="requested")
            try:
                clean, expected = self.validate(db, drone, user, command, params or {}, confirm_mode, adapter, source)
            except CommandError as exc:
                row.state = "unsupported" if exc.code == "UNSUPPORTED" else "rejected"
                row.failure_reason = f"{exc.code}: {exc.message}"
                row.completed_at = fleetsvc.now_utc()
                self._persist_with_audit(db, row, user, "command_refused")
                exc.extra["command"] = command_view(row, drone.name)
                raise
            row.params, row.expected_state = clean, expected
            # A different in-flight command for this drone is superseded; the same one is a duplicate.
            for other in db.execute(select(DroneCommand).where(DroneCommand.drone_id == drone.id, DroneCommand.state.in_(IN_FLIGHT))).scalars():
                if other.command == command and other.params == clean:
                    raise CommandError("DUPLICATE_COMMAND", f"{command} is already in progress for {drone.name}", 409,
                                       {"command": command_view(other, drone.name)})
                other.state, other.failure_reason, other.completed_at = "cancelled", f"superseded by {command}", fleetsvc.now_utc()
            try:
                self._persist_with_audit(db, row, user, "command_requested")
            except IntegrityError as exc:
                db.rollback()
                raise CommandError("IDEMPOTENCY_CONFLICT", "Concurrent request with the same idempotency key", 409) from exc
            t_dispatch = time.perf_counter()
            row.dispatched_at = fleetsvc.now_utc()
            try:
                res = adapter.dispatch(command, clean)
            except AdapterError as exc:
                row.state, row.failure_reason, row.completed_at = "failed", f"{exc.code}: {exc}", fleetsvc.now_utc()
                row.timings = {"backend_pre_dispatch_ms": _ms(t_received, t_dispatch)}
                self._persist_with_audit(db, row, user, "command_failed")
                return command_view(row, drone.name)
            t_ack = time.perf_counter()
            timings = {"backend_pre_dispatch_ms": _ms(t_received, t_dispatch), **res.timings, "ack_kind": res.ack_kind}
            if res.ack_kind == "mavlink_command_ack":
                timings["request_to_fc_dispatch_ms"] = round(timings["backend_pre_dispatch_ms"] + res.timings.get("http_one_way_est_ms", 0)
                                                             + res.timings.get("simhost_recv_to_mavlink_send_ms", 0), 3)
                timings["request_to_ack_ms"] = _ms(t_received, t_ack)
            else:
                timings["request_to_bus_ack_ms"] = _ms(t_received, t_ack)
            row.timings = timings
            row.ack_result = res.ack_result
            row.acknowledged_at = fleetsvc.now_utc()
            if not res.accepted:
                row.state, row.failure_reason, row.completed_at = "rejected", res.detail, fleetsvc.now_utc()
            else:
                row.state = "accepted" if res.ack_kind == "mavlink_command_ack" else "dispatched"
            self._persist_with_audit(db, row, user, f"command_{row.state}")
            view = command_view(row, drone.name)
        if view["state"] in ("accepted", "dispatched") and self.loop is not None:
            self.loop.call_soon_threadsafe(self._spawn_watch, view["id"], t_dispatch)
        return view

    def _persist_with_audit(self, db: Session, row: DroneCommand, user: User, action: str) -> None:
        db.add(row)
        db.flush()
        db.add(AuditLog(user_id=user.id, action=action, resource="drone", resource_id=str(row.drone_id),
                        event_metadata={"command": row.command, "command_id": str(row.id), "correlation_id": str(row.correlation_id),
                                        "fleet_command_id": str(row.fleet_command_id) if row.fleet_command_id else None, "mode": row.mode,
                                        "state": row.state, "role": user.role, "failure_reason": row.failure_reason}))
        db.commit()
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._broadcast, command_view(row), str(user.organization_id))

    def _broadcast(self, view: dict, org_id: str) -> None:
        if self.connections is not None:
            asyncio.ensure_future(self.connections.broadcast_json({"type": "command", "data": view}, organization_id=org_id))

    # ------------------------------------------------------- confirmation

    def _spawn_watch(self, command_id: str, t_dispatch: float) -> None:
        task = asyncio.ensure_future(self.watch(command_id, t_dispatch))
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)

    async def watch(self, command_id: str, t_dispatch: float) -> None:
        timeout = get_settings().MC_COMMAND_CONFIRM_TIMEOUT_S
        with self.session_factory() as db:
            row = db.get(DroneCommand, uuid.UUID(command_id))
            if row is None:
                return
            expected, drone_id, dispatched_at = row.expected_state or {}, str(row.drone_id), row.dispatched_at
            if row.command == "GOTO":
                timeout = max(timeout, 90.0)
        deadline = time.perf_counter() + timeout
        observed = None
        while time.perf_counter() < deadline:
            live = self._live(drone_id)
            # A frame counts only if it *arrived* after the dispatch (backend clock on both sides).
            arrived = fleetsvc.freshness_time(live, self._received(drone_id)) if live is not None else None
            if live is not None and _not_before(arrived, dispatched_at) and self.matches(expected, live):
                observed = live
                break
            await asyncio.sleep(0.1)
        await asyncio.to_thread(self._finish_watch, command_id, observed, t_dispatch)

    @staticmethod
    def matches(expected: dict, live: Any) -> bool:
        if "armed" in expected:
            return bool(live.armed) == expected["armed"]
        if "min_altitude_m" in expected:
            return (live.altitude or 0) >= expected["min_altitude_m"]
        if "within_m" in expected:
            return distance_m(live.latitude, live.longitude, expected["latitude"], expected["longitude"]) <= expected["within_m"]
        if expected.get("or_disarmed") and not live.armed:
            return True
        if "flight_mode" in expected:
            return live.flight_mode in expected["flight_mode"]
        if "flight_mode_not" in expected:
            return live.flight_mode not in expected["flight_mode_not"]
        return False

    def _finish_watch(self, command_id: str, observed: Any, t_dispatch: float) -> None:
        with self.session_factory() as db:
            row = db.get(DroneCommand, uuid.UUID(command_id))
            if row is None or row.state not in ("accepted", "dispatched"):
                return  # superseded or cancelled meanwhile
            now = fleetsvc.now_utc()
            if observed is not None:
                row.state = "confirmed"
                row.timings = {**(row.timings or {}), "observed_state_ms": round((observed.timestamp - row.dispatched_at).total_seconds() * 1000, 1)}
            else:
                row.state, row.failure_reason = "timed_out", f"telemetry did not show {row.expected_state} within the confirmation window"
            row.completed_at = now
            db.add(AuditLog(user_id=row.user_id, action=f"command_{row.state}", resource="drone", resource_id=str(row.drone_id),
                            event_metadata={"command": row.command, "command_id": str(row.id), "correlation_id": str(row.correlation_id),
                                            "mode": row.mode, "role": row.user_role}))
            db.commit()
            org = str(row.organization_id)
            view = command_view(row)
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._broadcast, view, org)

    # ---------------------------------------------------------- fleet-wide

    def fleet_preview(self, db: Session, user: User, fleet_command: str) -> dict:
        if fleet_command not in FLEET_COMMANDS:
            raise CommandError("UNSUPPORTED", f"Fleet command must be one of {sorted(FLEET_COMMANDS)}", 422)
        command = FLEET_COMMANDS[fleet_command]
        runtime = self.runtime_getter()
        view = fleetsvc.fleet(db, user.organization_id, runtime)
        targets, excluded = [], []
        for d in view["drones"]:
            drone = db.get(Drone, uuid.UUID(d["id"]))
            adapter, source, _ = self.resolve(db, drone, user)
            live = self._live(d["id"])
            entry = {"drone_id": d["id"], "name": d["name"], "telemetry_source": source, "connection_state": d["connection_state"]}
            if d["connection_state"] != "connected":
                excluded.append({**entry, "reason": f"telemetry {d['connection_state']}"})
            elif adapter is None or command not in adapter.capabilities:
                excluded.append({**entry, "reason": f"{command} not supported by {adapter.name if adapter else 'no adapter'}"})
            elif not self._flying(source, live):
                excluded.append({**entry, "reason": "not flying"})
            else:
                targets.append(entry)
        return {"fleet_command": fleet_command, "command": command, "targets": targets, "excluded": excluded,
                "requires_text": "LAND ALL" if fleet_command == "LAND_ALL" else None}

    def fleet_execute(self, user: User, fleet_command: str, expected_targets: list[str], confirm_text: str | None,
                      confirm_modes: dict[str, str], idempotency_key: str, t_received: float) -> dict:
        with self.session_factory() as db:
            user_db = db.merge(user)
            preview = self.fleet_preview(db, user_db, fleet_command)
            if preview["requires_text"] and confirm_text != preview["requires_text"]:
                raise CommandError("CONFIRMATION_REQUIRED", f"Type '{preview['requires_text']}' to confirm", 409)
            current = sorted(t["drone_id"] for t in preview["targets"])
            if current != sorted(expected_targets):
                raise CommandError("TARGETS_CHANGED", "The set of affected drones changed since the confirmation dialog was opened; review again",
                                   409, {"preview": preview})
            if not current:
                raise CommandError("NO_TARGETS", "No drone can receive this command", 409, {"preview": preview})
            existing = db.execute(select(DroneCommand).where(DroneCommand.idempotency_key.like(f"{idempotency_key}:%"))).scalars().first()
            if existing is not None:
                fid = existing.fleet_command_id
                rows = db.execute(select(DroneCommand).where(DroneCommand.fleet_command_id == fid)).scalars().all()
                return {"fleet_command_id": str(fid), "replayed": True, "results": [command_view(r) for r in rows], "excluded": preview["excluded"]}
        fid = uuid.uuid4()
        command = preview["command"]

        def one(target: dict) -> dict:
            try:
                return self.execute(user, uuid.UUID(target["drone_id"]), command, {}, confirm_modes.get(target["drone_id"], target["telemetry_source"]),
                                    f"{idempotency_key}:{target['drone_id']}", t_received, fleet_command_id=fid)
            except CommandError as exc:
                return exc.extra.get("command") or {"drone_id": target["drone_id"], "drone_name": target["name"], "state": "rejected",
                                                    "failure_reason": f"{exc.code}: {exc.message}"}

        results = list(self.pool.map(one, preview["targets"]))
        return {"fleet_command_id": str(fid), "command": command, "results": results, "excluded": preview["excluded"],
                "summary": {s: sum(r["state"] == s for r in results) for s in ("accepted", "dispatched", "rejected", "failed", "unsupported")}}

    def list(self, db: Session, org_id: uuid.UUID, drone_id: uuid.UUID | None = None, fleet_command_id: uuid.UUID | None = None,
             limit: int = 100) -> list[dict]:
        q = select(DroneCommand, Drone.name).join(Drone, Drone.id == DroneCommand.drone_id).where(DroneCommand.organization_id == org_id)
        if drone_id:
            q = q.where(DroneCommand.drone_id == drone_id)
        if fleet_command_id:
            q = q.where(DroneCommand.fleet_command_id == fleet_command_id)
        return [command_view(c, n) for c, n in db.execute(q.order_by(DroneCommand.requested_at.desc()).limit(limit)).all()]
