"""Drone MatrX hardware bridge: real ArduPilot aircraft over MAVLink.

Runs on the operations laptop (it needs the radios' COM ports), listens on
127.0.0.1 only, and is called by the backend -- never by browsers.

    python -m hwbridge.server            (or start it from Drone MatrX: Drone Setup)

API (all JSON; header X-Bridge-Token when a token is configured):

    GET  /health
    GET  /fc                                  discovered flight controllers
    POST /fc/{fc_id}/bind    {drone_id, uin, name}
    POST /fc/{fc_id}/unbind
    GET  /fc/{fc_id}/prearm                   raw health for the pre-arm checklist
    POST /fc/{fc_id}/command {command, params}
    POST /fc/{fc_id}/authorize {until_epoch, by}   flight permission (backend-granted)
    DELETE /fc/{fc_id}/authorize
    POST /fc/{fc_id}/override/release {by}
    POST /fc/{fc_id}/mission {items}
    GET  /video/sources                       configured aircraft cameras (RTSP)

Guided setup (used by Drone MatrX's Drone Setup page; replaces Mission Planner):

    GET  /ports                               USB serial ports (radios, autopilots)
    POST /links {endpoint, baud}              connect a port (baud 0 = automatic)
    DELETE /links/{name}
    PUT|DELETE /cameras/{sysid} {url}         aircraft camera (RTSP)
    GET  /fc/{fc_id}/setup                    parameters, RC channels, calibration state
    POST /fc/{fc_id}/params {values}          set + confirm parameters
    POST /fc/{fc_id}/sysid {system_id}        unique MAVLink id (writes + reboots)
    POST /fc/{fc_id}/reboot
    POST /fc/{fc_id}/calibration/{accel-start|accel-next|level|compass-start|compass-cancel}
    POST /fc/{fc_id}/motor-test               propellers removed only
    GET  /radio/{port}, POST /radio/{port}/netid {netid}   SiK radio pairing

The config file is created on first start with a random token, which is also
written to hwbridge/.bridge_token for the backend on the same laptop.

Telemetry of bound aircraft is published at 2 Hz on NATS as
drone.{UIN}.telemetry, alerts (override, failsafe text, mode changes) as
drone.{UIN}.alert -- the same subjects the simulator uses, so the whole
platform (map, fleet, mission control, TimescaleDB) consumes them unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from hwbridge.ardupilot import MAV_RESULT, SETUP_PARAMS, Limits, Vehicle, VehicleError
from hwbridge.links import ENDPOINT_RE, Link

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hwbridge")

CONFIG: dict = {}
state: dict = {"links": [], "published": 0, "publish_errors": 0, "config_path": None}
_config_lock = threading.Lock()
DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.json"


def token_file(config_path: Path) -> Path:
    """The backend on this laptop reads the token from here (HWBRIDGE_TOKEN_FILE)."""
    return config_path.parent / ".bridge_token"


def load_config(path: str | None) -> dict:
    """Load the config, creating a default one on first start. The config is
    edited from Drone MatrX's Drone Setup page; hand-editing is optional."""
    p = Path(path or os.environ.get("HWBRIDGE_CONFIG") or DEFAULT_CONFIG)
    cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    cfg.setdefault("nats_url", "nats://127.0.0.1:4222")
    cfg.setdefault("nats_stream", "DRONES")
    cfg.setdefault("http_port", 8110)
    cfg.setdefault("links", [])
    cfg.setdefault("cameras", {})
    cfg.setdefault("limits", {"max_alt_m": 30, "max_distance_m": 150})
    cfg.setdefault("radios", {})
    cfg.setdefault("avoidance", {"enabled": True, "rate_hz": 10.0})
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(24)
    state["config_path"] = p
    _write_config(cfg, p)
    return cfg


def _write_config(cfg: dict, p: Path) -> None:
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    token_file(p).write_text(cfg["token"], encoding="utf-8")


def save_config() -> None:
    with _config_lock:
        _write_config(CONFIG, state["config_path"])


def vehicles() -> dict[str, Vehicle]:
    return {v.fc_id: v for link in state["links"] for v in link.vehicles.values()}


def _vehicle(fc_id: str) -> Vehicle:
    v = vehicles().get(fc_id)
    if v is None:
        raise HTTPException(404, f"Flight controller {fc_id} not found (no heartbeat on any link)")
    return v


def _check_token(token: str | None) -> None:
    want = CONFIG.get("token")
    if want and token != want:
        raise HTTPException(401, "Bad or missing X-Bridge-Token")


# ---------------------------------------------------------------- NATS


async def _nats_connect() -> None:
    import nats

    try:
        nc = await nats.connect(CONFIG["nats_url"], name="dronematrx-hwbridge", max_reconnect_attempts=-1, connect_timeout=3)
        state["nc"], state["js"] = nc, nc.jetstream()
        state["nats_error"] = None
        logger.info("NATS connected %s", CONFIG["nats_url"])
    except Exception as exc:
        state["nats_error"] = str(exc)
        logger.error("NATS unavailable (%s): telemetry will not reach Drone MatrX", exc)


async def _publish(uin: str, kind: str, payload: dict) -> None:
    js = state.get("js")
    if js is None or not uin:
        return
    subject = f"drone.{uin}.{kind}"
    headers = {"Nats-Msg-Id": uuid.uuid4().hex, "Dm-Published-Ns": str(time.time_ns()), "Dm-Link": "hardware"}
    try:
        await js.publish(subject, json.dumps(payload, default=str).encode(), headers=headers, stream=CONFIG["nats_stream"], timeout=2.0)
        state["published"] += 1
    except Exception as exc:
        state["publish_errors"] += 1
        logger.warning("publish failed %s: %s", subject, exc)


def _on_event(v: Vehicle, kind: str, data: dict) -> None:
    logger.info("%s %s %s", v.fc_id, kind, data)
    if not v.bound:
        return
    loop = state.get("loop")
    payload = {"type": "hardware", "kind": kind, "uin": v.uin, "drone_id": v.drone_id, "fc_id": v.fc_id, "wall": time.time(), **data}
    if loop is not None:
        asyncio.run_coroutine_threadsafe(_publish(v.uin, "alert", payload), loop)


async def _telemetry_loop() -> None:
    while True:
        for v in list(vehicles().values()):
            if v.state.last_rx and time.time() - v.state.last_rx < 10:
                p = v.telemetry()
                if p is not None:
                    await _publish(v.uin, "telemetry", p)
        await asyncio.sleep(0.5)


def _start_link(endpoint: str, baud: int) -> Link:
    link = Link(endpoint, baud, Limits.from_dict(CONFIG.get("limits")), _on_event)
    link.start()
    state["links"].append(link)
    return link


# ----------------------------------------------------------------- app


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["loop"] = asyncio.get_running_loop()
    for spec in CONFIG["links"]:
        try:
            _start_link(spec["endpoint"], int(spec.get("baud", 0)))
        except ValueError as exc:
            logger.error("skipping link: %s", exc)
    # In the background: with NATS down the bridge still serves the setup API
    # (nats-py keeps retrying the first connection when reconnects are unlimited).
    from hwbridge.avoidance import HardwareOrca

    av = CONFIG.get("avoidance") or {}
    state["orca"] = HardwareOrca(vehicles, rate_hz=float(av.get("rate_hz", 10.0)), on_event=_on_event, enabled=bool(av.get("enabled", True)))
    state["orca"].start()
    state["nats_error"] = "connecting"
    tasks = [asyncio.create_task(_nats_connect()), asyncio.create_task(_telemetry_loop())]
    yield
    for task in tasks:
        task.cancel()
    state["orca"].stop()
    for link in state["links"]:
        link.stop()
    if state.get("nc"):
        await state["nc"].close()


app = FastAPI(title="Drone MatrX hardware bridge", lifespan=lifespan)


class BindIn(BaseModel):
    drone_id: str
    uin: str
    name: str


class CommandIn(BaseModel):
    command: str
    params: dict = Field(default_factory=dict)


class AuthorizeIn(BaseModel):
    until_epoch: float
    by: str


class ReleaseIn(BaseModel):
    by: str


class MissionIn(BaseModel):
    items: list[dict]


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "kind": "hardware", "links": [link.status() for link in state["links"]],
            "nats": {"url": CONFIG.get("nats_url"), "connected": state.get("js") is not None, "error": state.get("nats_error"),
                     "published": state["published"], "publish_errors": state["publish_errors"]},
            "limits": CONFIG.get("limits"), "cameras": len(CONFIG.get("cameras", {}))}


@app.get("/fc")
def flight_controllers(x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    return {"controllers": [v.info() for v in vehicles().values()]}


@app.post("/fc/{fc_id}/bind")
def bind(fc_id: str, body: BindIn, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    v = _vehicle(fc_id)
    v.drone_id, v.uin, v.name = body.drone_id, body.uin, body.name
    v.request_streams()
    return v.info()


@app.post("/fc/{fc_id}/unbind")
def unbind(fc_id: str, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    v = _vehicle(fc_id)
    v.drone_id = v.uin = v.name = None
    v.authorized_until, v.authorized_by = 0.0, None
    return v.info()


@app.get("/fc/{fc_id}/prearm")
def prearm(fc_id: str, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    return _vehicle(fc_id).prearm()


@app.post("/fc/{fc_id}/command")
def command(fc_id: str, body: CommandIn, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    received = time.perf_counter()
    v = _vehicle(fc_id)
    if not v.bound:
        raise HTTPException(409, "Controller is not bound to a Drone MatrX drone")
    v.last_command_timing = {}
    try:
        result = v.execute(body.command, body.params)
    except VehicleError as exc:
        raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc
    done = time.perf_counter()
    t = v.last_command_timing or {}
    timing = {"handler_ms": round((done - received) * 1000, 3)}
    if t.get("sent"):
        timing["recv_to_send_ms"] = round((t["sent"] - received) * 1000, 3)
        if t.get("ack"):
            timing["send_to_ack_ms"] = round((t["ack"] - t["sent"]) * 1000, 3)
    return {"fc_id": fc_id, "command": body.command, "result": result, "result_text": MAV_RESULT.get(result, str(result)),
            "timing": timing, "mode": v.state.mode, "armed": v.state.armed}


@app.post("/fc/{fc_id}/authorize")
def authorize(fc_id: str, body: AuthorizeIn, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    v = _vehicle(fc_id)
    if body.until_epoch - time.time() > 3600:
        raise HTTPException(422, "A flight permission lasts at most 60 minutes")
    v.authorized_until, v.authorized_by = body.until_epoch, body.by
    logger.warning("%s flight authorized by %s until %s", fc_id, body.by, time.ctime(body.until_epoch))
    return v.info()


@app.delete("/fc/{fc_id}/authorize")
def revoke(fc_id: str, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    v = _vehicle(fc_id)
    v.authorized_until, v.authorized_by = 0.0, None
    return v.info()


@app.post("/fc/{fc_id}/override/release")
def release_override(fc_id: str, body: ReleaseIn, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    v = _vehicle(fc_id)
    logger.warning("%s override released by %s (was: %s)", fc_id, body.by, v.override)
    v.override = None
    return v.info()


@app.post("/fc/{fc_id}/mission")
def mission(fc_id: str, body: MissionIn, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    v = _vehicle(fc_id)
    try:
        n = v.upload_mission(body.items)
    except VehicleError as exc:
        raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc
    return {"fc_id": fc_id, "uploaded": n, "mission_count": v.state.mission_count}


@app.get("/video/sources")
def video_sources(x_bridge_token: str | None = Header(default=None)) -> dict:
    """Aircraft cameras from the config, keyed by MAVLink system id."""
    _check_token(x_bridge_token)
    by_sysid = {v.sysid: v for v in vehicles().values()}
    out = []
    for sysid, url in CONFIG.get("cameras", {}).items():
        v = by_sysid.get(int(sysid))
        out.append({"system_id": int(sysid), "url": url, "path": f"hw/sys{int(sysid)}", "fc_id": v.fc_id if v else None,
                    "identity": v.identity if v else None, "drone_id": v.drone_id if v else None})
    return {"hardware_cameras": out}


# ------------------------------------------------------------ guided setup
# Everything Drone MatrX's "Drone Setup" page does, so no other ground
# station (Mission Planner) is needed: connections, radios, parameters,
# calibrations, motor test, cameras. All changes need a disarmed aircraft.

# USB vendor ids: autopilots connected directly, and telemetry radio chips.
AUTOPILOT_VIDS = {0x1209: "ArduPilot", 0x2DAE: "CubePilot", 0x26AC: "3DR / Pixhawk", 0x3162: "Holybro", 0x1FC9: "NXP", 0x0483: "STM32"}
RADIO_VIDS = {0x10C4: "Silicon Labs CP210x", 0x0403: "FTDI"}
CAMERA_URL_RE = re.compile(r"^(rtsps?|rtmp|srt|https?)://\S{3,300}$")


def _link(name: str) -> Link:
    for link in state["links"]:
        if link.name == name:
            return link
    raise HTTPException(404, f"No connection named {name}")


def _setup_call(fn):
    try:
        return fn()
    except VehicleError as exc:
        raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc


def _require_unbound(v: Vehicle) -> None:
    if v.bound:
        raise HTTPException(409, {"code": "BOUND", "message": "This flight controller is bound to a Drone MatrX drone; unbind it first"})


@app.get("/ports")
def ports(x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    from serial.tools import list_ports

    used = {link.endpoint.split(":", 1)[1]: link.name for link in state["links"] if link.endpoint.startswith("serial:")}
    out = []
    for p in sorted(list_ports.comports(), key=lambda p: p.device):
        kind = "autopilot" if p.vid in AUTOPILOT_VIDS or re.search(r"ardu|pixhawk|cube|px4|fmu", f"{p.description} {p.manufacturer}", re.I) \
            else "radio" if p.vid in RADIO_VIDS else "unknown"
        out.append({"device": p.device, "description": p.description, "manufacturer": p.manufacturer,
                    "vid": p.vid, "pid": p.pid, "kind": kind, "connected": p.device in used,
                    "suggested_baud": 115200 if kind == "autopilot" else 57600})
    return {"ports": out}


class LinkIn(BaseModel):
    endpoint: str
    baud: int = Field(0, ge=0, le=1_500_000)  # 0 = automatic


@app.post("/links")
def add_link(body: LinkIn, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    if not ENDPOINT_RE.match(body.endpoint):
        raise HTTPException(422, "Use serial:COM5, udp:0.0.0.0:14550 or tcp:127.0.0.1:5760")
    if any(link.endpoint == body.endpoint for link in state["links"]):
        raise HTTPException(409, f"{body.endpoint} is already connected")
    link = _start_link(body.endpoint, body.baud)
    CONFIG["links"].append({"endpoint": body.endpoint, "baud": body.baud})
    save_config()
    return link.status()


@app.delete("/links/{name}")
def remove_link(name: str, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    link = _link(name)
    if any(v.bound for v in link.vehicles.values()):
        raise HTTPException(409, {"code": "BOUND", "message": "A drone on this connection is bound; unbind it before disconnecting"})
    link.stop()
    state["links"].remove(link)
    CONFIG["links"] = [s for s in CONFIG["links"] if s["endpoint"] != link.endpoint]
    save_config()
    return {"removed": link.endpoint}


class CameraIn(BaseModel):
    url: str


@app.put("/cameras/{sysid}")
def set_camera(sysid: int, body: CameraIn, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    if not 1 <= sysid <= 254 or not CAMERA_URL_RE.match(body.url.strip()):
        raise HTTPException(422, "Camera URL must look like rtsp://192.168.1.101:8554/live")
    CONFIG["cameras"][str(sysid)] = body.url.strip()
    save_config()
    return {"system_id": sysid, "url": CONFIG["cameras"][str(sysid)]}


@app.delete("/cameras/{sysid}")
def delete_camera(sysid: int, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    CONFIG["cameras"].pop(str(sysid), None)
    save_config()
    return {"system_id": sysid, "url": None}


@app.get("/fc/{fc_id}/setup")
def setup_view(fc_id: str, refresh: bool = False, x_bridge_token: str | None = Header(default=None)) -> dict:
    """Live setup state; parameters are re-read when asked (they are slow over a radio)."""
    _check_token(x_bridge_token)
    v = _vehicle(fc_id)
    v.rc_fast()
    cached = v._setup_params
    if refresh or cached is None or any(cached.get(n) is None for n in ("SYSID_THISMAV", "FS_GCS_ENABLE")):
        cached = v.read_params_batch(SETUP_PARAMS, timeout_s=6.0)
        v._setup_params = cached
    view = v.setup_view(dict(cached))
    view["camera_url"] = CONFIG["cameras"].get(str(v.sysid))
    view["link"] = next((link.status() for link in state["links"] if v.fc_id.startswith(link.name + "-")), None)
    view["other_system_ids"] = sorted({o.sysid for o in vehicles().values() if o is not v})
    port = v.endpoint.split(":", 1)[1] if v.endpoint.startswith("serial:") else None
    view["port"] = port
    view["radio_netid"] = CONFIG["radios"].get(port) if port else None
    return view


class ParamsIn(BaseModel):
    values: dict[str, float]


@app.post("/fc/{fc_id}/params")
def set_params(fc_id: str, body: ParamsIn, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    v = _vehicle(fc_id)
    if "SYSID_THISMAV" in body.values:
        raise HTTPException(422, "Change the system id with POST /fc/{fc_id}/sysid")
    confirmed = _setup_call(lambda: v.write_params(body.values))
    cached = v._setup_params
    if cached is not None:
        cached.update(confirmed)
    return {"fc_id": fc_id, "confirmed": confirmed}


@app.post("/fc/{fc_id}/reboot")
def reboot(fc_id: str, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    v = _vehicle(fc_id)
    r = _setup_call(v.reboot)
    v._setup_params = None
    return {"fc_id": fc_id, "result": MAV_RESULT.get(r, str(r))}


class SysidIn(BaseModel):
    system_id: int = Field(..., ge=1, le=250)


@app.post("/fc/{fc_id}/sysid")
def set_sysid(fc_id: str, body: SysidIn, x_bridge_token: str | None = Header(default=None)) -> dict:
    """Give the aircraft a new unique MAVLink system id, then reboot it so it takes effect."""
    _check_token(x_bridge_token)
    v = _vehicle(fc_id)
    _require_unbound(v)
    if any(o.sysid == body.system_id for o in vehicles().values() if o is not v):
        raise HTTPException(409, {"code": "SYSID_IN_USE", "message": f"Another connected drone already uses system id {body.system_id}"})
    old = v.sysid
    if body.system_id != old:
        _setup_call(lambda: v.write_params({"SYSID_THISMAV": body.system_id}))
        _setup_call(v.reboot)
        if str(old) in CONFIG["cameras"] and str(body.system_id) not in CONFIG["cameras"]:
            CONFIG["cameras"][str(body.system_id)] = CONFIG["cameras"].pop(str(old))
            save_config()
        for link in state["links"]:
            if link.vehicles.get(old) is v:
                link.forget(old)
    link_name = v.fc_id.rsplit("-", 1)[0]
    return {"previous": old, "system_id": body.system_id, "new_fc_id": f"{link_name}-{body.system_id}",
            "message": "Rebooting; the drone reappears with its new id in about 15 s"}


@app.post("/fc/{fc_id}/calibration/{kind}")
def calibration(fc_id: str, kind: str, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    v = _vehicle(fc_id)
    actions = {"accel-start": v.accel_cal_start, "accel-next": v.accel_cal_confirm, "level": v.level_cal,
               "compass-start": v.compass_cal_start, "compass-cancel": v.compass_cal_cancel}
    if kind not in actions:
        raise HTTPException(404, f"Unknown calibration action {kind}")
    r = _setup_call(actions[kind])
    if kind != "accel-next" and r not in (0, None):
        raise HTTPException(409, {"code": "REFUSED", "message": f"The flight controller refused: {MAV_RESULT.get(r, r)}"})
    v._setup_params = None
    return {"fc_id": fc_id, "action": kind, "accelcal": v.state.accelcal, "magcal": {str(k): x for k, x in v.state.magcal.items()}}


class MotorTestIn(BaseModel):
    motor: int = Field(..., ge=1, le=12)
    throttle_pct: float = Field(7, gt=0, le=15)
    seconds: float = Field(2, gt=0, le=3)
    propellers_removed: bool


@app.post("/fc/{fc_id}/motor-test")
def motor_test(fc_id: str, body: MotorTestIn, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    if not body.propellers_removed:
        raise HTTPException(422, "Remove the propellers and confirm before testing motors")
    v = _vehicle(fc_id)
    r = _setup_call(lambda: v.motor_test(body.motor, body.throttle_pct, body.seconds))
    return {"fc_id": fc_id, "motor": body.motor, "result": MAV_RESULT.get(r, str(r))}


def _radio_port(port: str) -> tuple[str, Link | None]:
    if not re.fullmatch(r"COM\d{1,3}|/dev/tty[A-Za-z0-9]{1,20}", port.replace("%2F", "/")):
        raise HTTPException(422, "Port must look like COM5")
    port = port.replace("%2F", "/")
    return port, next((link for link in state["links"] if link.endpoint == f"serial:{port}"), None)


def _with_paused(link: Link | None, fn):
    from hwbridge.radio import RadioError

    if link is not None and any(v.state.armed for v in link.vehicles.values()):
        raise HTTPException(409, {"code": "ARMED", "message": "A drone on this radio is armed"})
    if link is not None:
        link.pause()
    try:
        return fn()
    except RadioError as exc:
        raise HTTPException(409, {"code": "RADIO", "message": str(exc)}) from exc
    finally:
        if link is not None:
            link.resume()


@app.get("/radio/{port}")
def radio_info(port: str, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    from hwbridge.radio import read_info

    port, link = _radio_port(port)
    info = _with_paused(link, lambda: read_info(port))
    used = {p: n for p, n in CONFIG["radios"].items() if p != port}
    info["netids_in_use"] = used
    info["suggested_netid"] = next(n for n in range(25, 500) if n not in used.values())
    return info


class NetidIn(BaseModel):
    netid: int = Field(..., ge=0, le=499)


@app.post("/radio/{port}/netid")
def radio_netid(port: str, body: NetidIn, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    from hwbridge.radio import set_netid

    port, link = _radio_port(port)
    clash = [p for p, n in CONFIG["radios"].items() if n == body.netid and p != port]
    if clash:
        raise HTTPException(409, {"code": "NETID_IN_USE", "message": f"NETID {body.netid} is already used by the radio on {clash[0]}"})
    result = _with_paused(link, lambda: set_netid(port, body.netid))
    CONFIG["radios"][port] = body.netid
    save_config()
    return result


# ------------------------------------------------- ORCA collision avoidance


class OrcaIn(BaseModel):
    enabled: bool


@app.get("/orca")
def orca_status(x_bridge_token: str | None = Header(default=None)) -> dict:
    """The simulator's ORCA, running on the real aircraft (hwbridge/avoidance.py)."""
    _check_token(x_bridge_token)
    return state["orca"].status()


@app.post("/orca")
def orca_toggle(body: OrcaIn, x_bridge_token: str | None = Header(default=None)) -> dict:
    _check_token(x_bridge_token)
    state["orca"].set_enabled(body.enabled)
    CONFIG["avoidance"] = {**(CONFIG.get("avoidance") or {}), "enabled": body.enabled}
    save_config()
    logger.warning("ORCA collision avoidance %s", "ENABLED" if body.enabled else "DISABLED")
    return state["orca"].status()


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="Drone MatrX hardware bridge (ArduPilot over MAVLink)")
    ap.add_argument("--config", default=None, help="path to config.json (default hwbridge/config.json)")
    args = ap.parse_args()
    CONFIG.update(load_config(args.config))
    uvicorn.run(app, host="127.0.0.1", port=int(CONFIG["http_port"]), log_level="info")


if __name__ == "__main__":
    main()
