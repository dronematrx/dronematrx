"""Flight-controller discovery, binding and the SITL bench.

Discovery scope (no network scanning, ever):

* SITL: the PX4 instances of SITL *bench* sessions on the configured simhost
  (session ids `bench-*`). A bench is a set of PX4 SITL flight controllers
  in a flat Gazebo test field, started from mission control; it is not a
  mission simulation. Its controllers publish nothing until bound.
* Hardware: only the endpoints in the explicit allow-list
  FC_HARDWARE_ENDPOINTS. They are listed but not probed, and binding one
  fails with HARDWARE_UNAVAILABLE: no hardware transport is configured, and
  the hardware adapter stays locked.

Binding lifecycle (flight_controller_bindings.state):

    discovered (not a row) -> binding -> bound -> unbound
                                  `-> connection_failed -> (reconnect) -> bound

A bound SITL controller's telemetry is routed by simhost to the drone record
it is bound to. One active binding per controller identity and per drone
(partial unique indexes); binding never arms anything.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.drone import Drone
from app.models.mission_control import FlightControllerBinding
from app.models.user import User
from app.services.mission_control.fleet import ACTIVE_BINDING, binding_view, now_utc
from app.services.simulation.simhost import SimhostClient, SimhostError

BENCH_PREFIX = "bench-"
UDP_RE = re.compile(r"^udp:(?P<host>[A-Za-z0-9.\-]{1,63}):(?P<port>\d{2,5})$")
SERIAL_RE = re.compile(r"^serial:(?P<dev>/dev/tty[A-Za-z0-9]{1,20}|COM\d{1,3})$")
SITL_RE = re.compile(r"^sitl:(?P<session>bench-[a-f0-9]{6}):(?P<fc>FC-[a-f0-9]{6}-\d{2})$")


class FcError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass
class Discovered:
    fc_identity: str
    connection_type: str
    endpoint: str
    system_id: int | None
    firmware: str | None
    status: str  # connected | stale | not_probed | unreachable
    sitl_session_id: str | None = None
    fc_id: str | None = None
    heartbeat_age_s: float | None = None
    mode: str | None = None
    armed: bool | None = None
    simulation: bool = True
    note: str | None = None


def hardware_allow_list() -> list[str]:
    raw = get_settings().FC_HARDWARE_ENDPOINTS
    return [e.strip() for e in raw.split(",") if e.strip()]


def validate_endpoint(conn: str, endpoint: str) -> dict:
    """Server-side validation of a manually entered connection. Only fixed
    shapes are accepted, nothing is executed, and hardware endpoints must be
    on the allow-list."""
    if conn == "sitl":
        m = SITL_RE.match(endpoint)
        if not m:
            raise FcError("INVALID_ENDPOINT", "SITL endpoint must be 'sitl:bench-xxxxxx:FC-xxxxxx-NN' (a session-managed bench controller)")
        return {"session": m.group("session"), "fc": m.group("fc")}
    if conn == "udp":
        m = UDP_RE.match(endpoint)
        if not m or not 1024 <= int(m.group("port")) <= 65535:
            raise FcError("INVALID_ENDPOINT", "UDP endpoint must be 'udp:<host>:<port 1024-65535>'")
    elif conn == "serial":
        if not SERIAL_RE.match(endpoint):
            raise FcError("INVALID_ENDPOINT", "Serial endpoint must be 'serial:/dev/ttyXXX' or 'serial:COMn'")
    else:
        raise FcError("INVALID_CONNECTION_TYPE", "connection_type must be sitl, udp or serial")
    if endpoint not in hardware_allow_list():
        raise FcError("ENDPOINT_NOT_APPROVED", f"{endpoint} is not in the approved hardware endpoint list (FC_HARDWARE_ENDPOINTS)", 403)
    return {}


# --------------------------------------------------------------- bench


def start_bench(db: Session, user: User, count: int, client: SimhostClient, cameras: int | None = None) -> dict:
    """Start a SITL bench of `count` PX4 x500 (downward camera) in a flat test
    field with red ground markers for the detector. Blocks until PX4 is ready."""
    from app.services.simulation.dem import FlatSource
    from app.services.simulation.geo import EnuFrame
    from app.services.simulation.orchestrator import workspace_root
    from app.services.simulation.world import ObstacleSpec, SpawnRequest, WorldGenerator

    s = get_settings()
    if any(sid.startswith(BENCH_PREFIX) and state in ("ready", "running", "starting") for sid, state in client.sessions().items()):
        raise FcError("BENCH_RUNNING", "A SITL bench is already running", 409)
    sid = f"{BENCH_PREFIX}{uuid.uuid4().hex[:6]}"
    tag = sid[len(BENCH_PREFIX):]
    frame = EnuFrame(s.MC_BENCH_LAT, s.MC_BENCH_LON)
    half = 160.0
    ring = [frame.xy_to_lonlat(x, y) for x, y in ((-half, -half), (half, -half), (half, half), (-half, half), (-half, -half))]
    aoi = {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}
    spawns = []
    for n in range(count):
        lon, lat = frame.xy_to_lonlat(-24.0 + 8.0 * (n % 7), -30.0 - 8.0 * (n // 7))
        spawns.append(SpawnRequest(f"FC-{tag}-{n + 1:02d}", f"FC-{tag}-{n + 1:02d}", lat, lon))
    markers = []
    for i, (x, y) in enumerate(((0.0, 25.0), (30.0, 40.0), (-35.0, 45.0))):
        lon, lat = frame.xy_to_lonlat(x, y)
        markers.append(ObstacleSpec(f"marker_{i + 1}", lat, lon, (2.5, 2.5, 0.05)))
    gen = WorldGenerator(workspace_root(), uri_root=s.SIM_WORKSPACE_URI)
    world = gen.generate(sid, "bench", aoi, spawns, FlatSource(0.0, reason="SITL bench: flat test field"), obstacles=markers)
    body = {
        "session_id": sid, "world_sdf": f"{s.SIM_WORKSPACE_URI}/{sid}/world.sdf", "world_name": world.world_name,
        "origin": world.metadata["frame"]["origin"], "seed": 11, "bench": True, "camera": True, "publish": True,
        "drones": [{"uin": sp.uin, "drone_id": "", "name": sp.name, "pose": sp.pose_string()} for sp in world.spawns],
        "params": {"ground_z": 0.0, "camera_count": count if cameras is None else cameras},
    }
    t0 = time.perf_counter()
    status = client.create_session(body)
    # The bench test field is the drones' operating area: an inclusion geofence,
    # so pre-arm and GOTO zone checks treat it like any other area.
    from app.models.geofence import Geofence

    wkt = "POLYGON((" + ", ".join(f"{lon!r} {lat!r}" for lon, lat in ring) + "))"
    db.add(Geofence(organization_id=user.organization_id, name=f"SITL Bench Test Field {tag}", type="inclusion",
                    geometry=f"SRID=4326;{wkt}", altitude_min=0, altitude_max=120, active=True))
    db.add(AuditLog(user_id=user.id, action="sitl_bench_started", resource="sitl_bench", resource_id=sid,
                    event_metadata={"controllers": count, "startup_s": round(time.perf_counter() - t0, 2)}))
    db.commit()
    markers_out = [{"name": m.name, "latitude": m.latitude, "longitude": m.longitude} for m in markers]
    return {"session_id": sid, "state": status.get("state"), "controllers": [sp.uin for sp in world.spawns],
            "startup_s": status.get("startup_s"), "markers": markers_out, "origin": body["origin"]}


def stop_bench(db: Session, user: User, session_id: str, client: SimhostClient) -> dict:
    if not session_id.startswith(BENCH_PREFIX):
        raise FcError("NOT_A_BENCH", "Only SITL bench sessions can be stopped here", 403)
    out = client.stop(session_id)
    from app.models.geofence import Geofence

    for g in db.execute(select(Geofence).where(Geofence.organization_id == user.organization_id,
                                               Geofence.name == f"SITL Bench Test Field {session_id[len(BENCH_PREFIX):]}")).scalars():
        g.active = False
    for b in db.execute(select(FlightControllerBinding).where(FlightControllerBinding.sitl_session_id == session_id,
                                                               FlightControllerBinding.state.in_(ACTIVE_BINDING))).scalars():
        b.state, b.error = "connection_failed", "SITL bench stopped"
    db.add(AuditLog(user_id=user.id, action="sitl_bench_stopped", resource="sitl_bench", resource_id=session_id))
    db.commit()
    return out


# ------------------------------------------------------------- discovery


def discover(db: Session, org_id: uuid.UUID, client: SimhostClient) -> dict:
    t0 = time.perf_counter()
    found: list[Discovered] = []
    errors: list[str] = []
    try:
        sessions = client.sessions()
        for sid, state in sessions.items():
            if not sid.startswith(BENCH_PREFIX) or state not in ("ready", "running"):
                continue
            for fc in client.flight_controllers(sid)["controllers"]:
                age = fc.get("heartbeat_age_s")
                found.append(Discovered(
                    fc_identity=f"sitl:{sid}:{fc['fc_id']}", connection_type="sitl", endpoint=f"sitl:{sid}:{fc['fc_id']}",
                    system_id=fc.get("system_id"), firmware=fc.get("firmware"),
                    status="connected" if age is not None and age < get_settings().MC_TELEMETRY_STALE_S and not fc.get("closed") else "stale",
                    sitl_session_id=sid, fc_id=fc["fc_id"], heartbeat_age_s=age, mode=fc.get("mode"), armed=fc.get("armed"),
                    note=f"PX4 SITL (simhost, MAVLink {fc.get('endpoint')})"))
    except SimhostError as exc:
        errors.append(f"SITL discovery unavailable: {exc}")
    for ep in hardware_allow_list():
        conn = ep.split(":", 1)[0]
        found.append(Discovered(fc_identity=ep, connection_type=conn, endpoint=ep, system_id=None, firmware=None, status="not_probed",
                                simulation=False, note="Approved hardware endpoint. Not probed: no hardware transport configured (adapter locked)"))
    bound = {b.fc_identity: b for b in db.execute(select(FlightControllerBinding).where(
        FlightControllerBinding.organization_id == org_id, FlightControllerBinding.state.in_(ACTIVE_BINDING))).scalars()}
    names = {str(d.id): d.name for d in db.execute(select(Drone).where(Drone.organization_id == org_id)).scalars()}
    items = []
    for d in found:
        b = bound.get(d.fc_identity)
        items.append({**d.__dict__, "bound": b is not None, "bound_drone_id": str(b.drone_id) if b else None,
                      "bound_drone_name": names.get(str(b.drone_id)) if b else None})
    return {"controllers": items, "errors": errors, "discovery_ms": round((time.perf_counter() - t0) * 1000, 1),
            "scope": {"sitl": f"bench sessions on {get_settings().SIMHOST_URL}", "hardware_allow_list": hardware_allow_list()}}


# --------------------------------------------------------------- binding


def _org_drone(db: Session, org_id: uuid.UUID, drone_id: uuid.UUID) -> Drone:
    drone = db.get(Drone, drone_id)
    if drone is None or drone.organization_id != org_id or not drone.active:
        raise FcError("DRONE_NOT_FOUND", "Drone not found", 404)
    return drone


def bind(db: Session, user: User, drone_id: uuid.UUID, conn: str, endpoint: str, via: str, client: SimhostClient,
         system_id: int | None = None) -> dict:
    drone = _org_drone(db, user.organization_id, drone_id)
    parsed = validate_endpoint(conn, endpoint)
    if conn == "sitl" and not drone.simulated:
        raise FcError("REAL_DRONE_SITL", f"{drone.name} is a real-aircraft record; a SITL controller can only bind to a simulation drone", 409)
    if conn != "sitl" and drone.simulated:
        raise FcError("SIM_DRONE_HARDWARE", f"{drone.name} is a simulation drone; hardware controllers bind only to real-aircraft records", 409)
    active = select(FlightControllerBinding).where(FlightControllerBinding.state.in_(ACTIVE_BINDING))
    if db.execute(active.where(FlightControllerBinding.fc_identity == endpoint)).scalars().first():
        raise FcError("FC_ALREADY_BOUND", f"{endpoint} is already bound to a drone", 409)
    if db.execute(active.where(FlightControllerBinding.drone_id == drone.id)).scalars().first():
        raise FcError("DRONE_ALREADY_BOUND", f"{drone.name} already has a bound flight controller; unbind it first", 409)
    b = FlightControllerBinding(organization_id=user.organization_id, drone_id=drone.id, fc_identity=endpoint, connection_type=conn,
                                endpoint=endpoint, system_id=system_id, state="binding", bound_via=via, bound_by=user.id,
                                sitl_session_id=parsed.get("session"))
    db.add(b)
    try:
        db.flush()
    except IntegrityError as exc:  # lost a race with a concurrent bind
        db.rollback()
        raise FcError("FC_ALREADY_BOUND", "Controller or drone was bound concurrently", 409) from exc
    _connect(b, drone, client, parsed)
    if b.state == "bound":
        # Close earlier failed bindings of this drone / controller: the new one replaces them.
        for old in db.execute(select(FlightControllerBinding).where(
                FlightControllerBinding.state == "connection_failed", FlightControllerBinding.id != b.id,
                (FlightControllerBinding.drone_id == drone.id) | (FlightControllerBinding.fc_identity == endpoint))).scalars():
            old.state, old.unbound_at = "unbound", now_utc()
    db.add(AuditLog(user_id=user.id, action="fc_bind", resource="drone", resource_id=str(drone.id),
                    event_metadata={"fc_identity": endpoint, "via": via, "state": b.state, "error": b.error}))
    db.commit()
    db.refresh(b)
    return binding_view(b)


def _connect(b: FlightControllerBinding, drone: Drone, client: SimhostClient, parsed: dict | None = None) -> None:
    parsed = parsed or validate_endpoint(b.connection_type, b.endpoint)
    if b.connection_type != "sitl":
        b.state, b.error = "connection_failed", "HARDWARE_UNAVAILABLE: no hardware flight-controller transport is configured"
        return
    try:
        info = client.bind_fc(parsed["session"], parsed["fc"], str(drone.id), drone.uin or str(drone.id), drone.name)
    except SimhostError as exc:
        b.state, b.error = "connection_failed", f"SITL controller unreachable: {exc}"
        return
    b.state, b.error, b.bound_at = "bound", None, now_utc()
    b.system_id, b.firmware, b.sitl_index = info.get("system_id"), info.get("firmware"), info.get("index")


def unbind(db: Session, user: User, binding_id: uuid.UUID, client: SimhostClient) -> dict:
    b = db.get(FlightControllerBinding, binding_id)
    if b is None or b.organization_id != user.organization_id:
        raise FcError("BINDING_NOT_FOUND", "Binding not found", 404)
    if b.state == "unbound":
        return binding_view(b)
    drone = db.get(Drone, b.drone_id)
    if b.connection_type == "sitl" and drone is not None and drone.armed:
        raise FcError("ARMED", f"{drone.name} is armed; disarm before unbinding its flight controller", 409)
    if b.connection_type == "sitl":
        parsed = validate_endpoint("sitl", b.endpoint)
        try:
            client.unbind_fc(parsed["session"], parsed["fc"])
        except SimhostError:
            pass  # session already gone: the binding is closed regardless
    b.state, b.unbound_at = "unbound", now_utc()
    db.add(AuditLog(user_id=user.id, action="fc_unbind", resource="drone", resource_id=str(b.drone_id), event_metadata={"fc_identity": b.fc_identity}))
    db.commit()
    return binding_view(b)


def reconnect(db: Session, user: User, binding_id: uuid.UUID, client: SimhostClient) -> dict:
    b = db.get(FlightControllerBinding, binding_id)
    if b is None or b.organization_id != user.organization_id:
        raise FcError("BINDING_NOT_FOUND", "Binding not found", 404)
    if b.state not in ("connection_failed", "bound"):
        raise FcError("NOT_RECONNECTABLE", f"Binding is {b.state}", 409)
    _connect(b, db.get(Drone, b.drone_id), client)
    db.add(AuditLog(user_id=user.id, action="fc_reconnect", resource="drone", resource_id=str(b.drone_id),
                    event_metadata={"fc_identity": b.fc_identity, "state": b.state, "error": b.error}))
    db.commit()
    return binding_view(b)


def reconcile(db: Session, client: SimhostClient) -> dict:
    """Keep persisted bindings and the simulator consistent (after a backend
    or simhost restart): a vanished bench controller marks the binding
    connection_failed; a controller that lost its routing is re-bound."""
    changed = {"failed": 0, "rebound": 0, "recovered": 0}
    rows = db.execute(select(FlightControllerBinding).where(FlightControllerBinding.connection_type == "sitl",
                                                            FlightControllerBinding.state.in_(("bound", "connection_failed")))).scalars().all()
    if not rows:
        return changed
    try:
        sessions = client.sessions()
    except SimhostError:
        return changed
    cache: dict[str, dict] = {}
    for b in rows:
        sid = b.sitl_session_id or ""
        fc_id = b.endpoint.rsplit(":", 1)[-1]
        if sessions.get(sid) not in ("ready", "running"):
            if b.state == "bound":
                b.state, b.error = "connection_failed", "SITL bench session is not running"
                changed["failed"] += 1
            continue
        if sid not in cache:
            try:
                cache[sid] = {c["fc_id"]: c for c in client.flight_controllers(sid)["controllers"]}
            except SimhostError:
                continue
        fc = cache[sid].get(fc_id)
        if fc is None:
            b.state, b.error = "connection_failed", "controller no longer present in the bench"
            changed["failed"] += 1
        elif b.state == "bound" and fc.get("drone_id") != str(b.drone_id):
            _connect(b, db.get(Drone, b.drone_id), client)
            changed["rebound"] += 1
    db.commit()
    return changed


def bindings(db: Session, org_id: uuid.UUID, include_history: bool = False) -> list[dict]:
    q = select(FlightControllerBinding).where(FlightControllerBinding.organization_id == org_id)
    if not include_history:
        q = q.where(FlightControllerBinding.state != "unbound")
    return [binding_view(b) for b in db.execute(q.order_by(FlightControllerBinding.created_at.desc())).scalars()]


def active_binding(db: Session, drone_id: uuid.UUID) -> FlightControllerBinding | None:
    return db.execute(select(FlightControllerBinding).where(FlightControllerBinding.drone_id == drone_id,
                                                           FlightControllerBinding.state.in_(ACTIVE_BINDING))).scalars().first()


