"""Simulation host HTTP API (runs inside the sim container).

The Drone MatrX backend's simulation orchestrator is the only client. It is
simulation-only by construction: this service can reach nothing but the
PX4 SITL processes it launched itself.

    GET  /health                         versions, capacity
    POST /sessions                       start world + SITL fleet (blocks until ready)
    GET  /sessions/{id}                  status, instances, gz stats, guardrail, truth vs reported
    POST /sessions/{id}/missions         upload per-drone MAVLink missions
    POST /sessions/{id}/command          abstract flight command for one drone
    POST /sessions/{id}/pause|resume     Gazebo world control
    POST /sessions/{id}/speed            real-time factor
    POST /sessions/{id}/failures         kill | link | battery | gps_drift | wind | obstacle
    POST /sessions/{id}/stop             stop everything, clean up processes
    GET  /processes                      every live PX4 process (orphan inspection)
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from simhost.bridge import BridgeError
from simhost.gazebo import GazeboError, gz_version
from simhost.session import SPEED_LEVELS, DroneSpec, SessionError, SessionSpec, SimSession
from simhost.sitl_manager import SitlError, SitlProcessManager, running_px4_processes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("simhost")

PX4_BUILD = Path(os.environ.get("PX4_BUILD", "/opt/PX4-Autopilot/build/px4_sitl_default"))
WORKSPACE = Path(os.environ.get("SIM_WORKSPACE", "/workspace"))
MAX_INSTANCES = int(os.environ.get("SIM_MAX_INSTANCES", "8"))
NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
NATS_STREAM = os.environ.get("NATS_STREAM", "DRONES")
NATS_PREFIX = os.environ.get("NATS_SUBJECT_PREFIX", "")

sessions: dict[str, SimSession] = {}
state: dict = {}


def _on_sitl_exit(inst, returncode, expected) -> None:
    sess = sessions.get(inst.session_id)
    if sess is not None:
        sess.process_exited(inst.uin, returncode, expected)


manager = SitlProcessManager(PX4_BUILD, MAX_INSTANCES, on_exit=_on_sitl_exit)


# ------------------------------------------------------------------ NATS


async def _nats_connect():
    import nats

    try:
        nc = await nats.connect(NATS_URL, connect_timeout=3, max_reconnect_attempts=-1, reconnect_time_wait=1)
        state["nc"], state["js"] = nc, nc.jetstream()
        logger.info("nats connected %s", NATS_URL)
    except Exception as exc:  # pragma: no cover - reported in /health
        state["nats_error"] = str(exc)
        logger.error("nats unavailable: %s", exc)


async def _publish(uin: str, kind: str, payload: dict) -> None:
    js = state.get("js")
    if js is None:
        return
    subject = f"{NATS_PREFIX + '.' if NATS_PREFIX else ''}drone.{uin}.{kind}"
    headers = {"Nats-Msg-Id": uuid.uuid4().hex, "Dm-Published-Ns": str(time.time_ns()), "Dm-Link": "sim"}
    try:
        await js.publish(subject, json.dumps(payload, default=str).encode(), headers=headers, stream=NATS_STREAM, timeout=2.0)
        state["published"] = state.get("published", 0) + 1
    except Exception as exc:
        state["publish_errors"] = state.get("publish_errors", 0) + 1
        logger.warning("publish failed %s: %s", subject, exc)


def publish_threadsafe(uin: str, kind: str, payload: dict) -> None:
    loop = state.get("loop")
    if loop is not None:
        asyncio.run_coroutine_threadsafe(_publish(uin, kind, payload), loop)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["loop"] = asyncio.get_running_loop()
    killed = manager.sweep_orphans()
    if killed:
        logger.warning("killed %d orphan PX4 processes at startup", len(killed))
    await _nats_connect()
    state["versions"] = {"gazebo": gz_version(), "px4": _px4_version()}
    yield
    for s in list(sessions.values()):
        s.stop()
    manager.shutdown()
    if state.get("nc"):
        await state["nc"].close()


def _px4_version() -> str:
    try:
        root = PX4_BUILD.parent.parent
        tag = (root / ".git" / "HEAD").read_text().strip()
        import subprocess

        out = subprocess.run(["git", "-C", str(root), "describe", "--tags", "--always"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or tag
    except Exception:
        return "unknown"


app = FastAPI(title="Drone MatrX simulation host", lifespan=lifespan)


def _session(session_id: str) -> SimSession:
    sess = sessions.get(session_id)
    if sess is None:
        raise HTTPException(404, "Unknown simulation session")
    return sess


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (SessionError, SitlError, GazeboError, BridgeError) as exc:
        raise HTTPException(409, str(exc)) from exc


# ------------------------------------------------------------------ models


class DroneIn(BaseModel):
    uin: str
    drone_id: str
    name: str
    pose: str


class SessionIn(BaseModel):
    session_id: str
    world_sdf: str
    world_name: str
    origin: dict
    drones: list[DroneIn] = Field(..., min_length=1, max_length=16)
    speed: float = 1.0
    link_loss_policy: str = "RTH"
    seed: int = 1
    cruise_speed_mps: float = 8.0
    battery_drain_s: float = 1800.0
    params: dict = Field(default_factory=dict)
    # False for simulator-only integration tests: nothing is put on the shared
    # NATS bus (test drones are not Drone MatrX drones).
    publish: bool = True


class MissionsIn(BaseModel):
    missions: dict[str, list[dict]]  # uin -> mission items
    start: bool = False


class CommandIn(BaseModel):
    uin: str
    command: str  # arm | disarm | takeoff | hold | goto | mode | rtl | land | mission_start
    params: dict = Field(default_factory=dict)


class SpeedIn(BaseModel):
    speed: float


class FailureIn(BaseModel):
    type: str
    uin: str | None = None
    params: dict = Field(default_factory=dict)


# -------------------------------------------------------------- endpoints


@app.get("/health")
def health() -> dict:
    import psutil

    return {
        "status": "ok",
        "versions": state.get("versions"),
        "nats": {"url": NATS_URL, "connected": bool(state.get("nc") and state["nc"].is_connected), "error": state.get("nats_error"),
                 "published": state.get("published", 0), "publish_errors": state.get("publish_errors", 0)},
        "capacity": {
            "max_instances": MAX_INSTANCES,
            "running_instances": sum(len(manager.health(sid)) for sid in sessions),
            "cpus": os.cpu_count(),
            "memory_gb": round(psutil.virtual_memory().total / 1e9, 1),
            "memory_available_gb": round(psutil.virtual_memory().available / 1e9, 1),
        },
        "speed_levels": SPEED_LEVELS,
        "sessions": {sid: s.state for sid, s in sessions.items()},
    }


@app.post("/sessions")
def create_session(body: SessionIn) -> dict:
    if body.session_id in sessions and sessions[body.session_id].state not in ("stopped", "failed"):
        raise HTTPException(409, "Session already running")
    if not Path(body.world_sdf).exists():
        raise HTTPException(422, f"World file not found in the simulator workspace: {body.world_sdf}")
    spec = SessionSpec(
        session_id=body.session_id,
        world_sdf=body.world_sdf,
        world_name=body.world_name,
        origin=body.origin,
        drones=[DroneSpec(**d.model_dump()) for d in body.drones],
        speed=body.speed,
        link_loss_policy=body.link_loss_policy,
        seed=body.seed,
        cruise_speed_mps=body.cruise_speed_mps,
        battery_drain_s=body.battery_drain_s,
        params=body.params,
    )
    sess = SimSession(spec, manager, Path(body.world_sdf).parent / "run", publish_threadsafe if body.publish else (lambda *a: None))
    sessions[body.session_id] = sess
    started = time.time()
    _guard(sess.start)
    return {**sess.status(), "startup_s": round(time.time() - started, 2)}


@app.get("/sessions/{session_id}")
def session_status(session_id: str) -> dict:
    return _session(session_id).status()


@app.post("/sessions/{session_id}/missions")
def upload_missions(session_id: str, body: MissionsIn) -> dict:
    sess = _session(session_id)
    out = {}
    for uin, items in body.missions.items():
        link = _guard(sess.link, uin)
        out[uin] = {"items": _guard(link.upload_mission, items)}
        out[uin]["gen"] = link.state.mission_gen
    if body.start:
        for uin in body.missions:
            link = sess.link(uin)
            out[uin]["start"] = _guard(link.start_mission)
        sess.state = "running"
    return out


@app.post("/sessions/{session_id}/command")
def command(session_id: str, body: CommandIn) -> dict:
    sess = _session(session_id)
    link = _guard(sess.link, body.uin)
    p = body.params
    c = body.command
    if c == "arm":
        result = _guard(link.arm_when_ready, float(p.get("timeout_s", 120)))
    elif c == "disarm":
        result = _guard(link.disarm)
    elif c == "takeoff":
        result = _guard(link.takeoff, float(p.get("altitude_m", 10)))
    elif c == "hold":
        result = _guard(link.set_mode, "HOLD")
    elif c == "rtl":
        result = _guard(link.set_mode, "RTL")
    elif c == "land":
        result = _guard(link.set_mode, "LAND")
    elif c == "mode":
        result = _guard(link.set_mode, str(p["mode"]).upper())
    elif c == "mission_start":
        result = _guard(link.start_mission)
    elif c == "goto":
        result = _guard(link.goto, float(p["latitude"]), float(p["longitude"]), float(p.get("altitude_m", 30)))
    else:
        raise HTTPException(422, f"Unsupported command {c}")
    return {"uin": body.uin, "command": c, "result": result}


@app.post("/sessions/{session_id}/pause")
def pause(session_id: str) -> dict:
    return _guard(_session(session_id).pause, True)


@app.post("/sessions/{session_id}/resume")
def resume(session_id: str) -> dict:
    return _guard(_session(session_id).pause, False)


@app.post("/sessions/{session_id}/speed")
def speed(session_id: str, body: SpeedIn) -> dict:
    return _guard(_session(session_id).set_speed, body.speed)


@app.post("/sessions/{session_id}/failures")
def failure(session_id: str, body: FailureIn) -> dict:
    sess = _session(session_id)
    p = body.params
    record = {"type": body.type, "uin": body.uin, "params": p, "wall": time.time(), "sim_time_s": sess.sim_time()}
    if body.type == "kill":
        inst = _guard(manager.kill, session_id, sess.index_of[body.uin])
        record["pid"] = inst.pid
    elif body.type == "link":
        link = _guard(sess.link, body.uin)
        cfg = link.emulator.configure(latency_ms=p.get("latency_ms"), jitter_ms=p.get("jitter_ms"), loss_pct=p.get("loss_pct"), down=p.get("down"))
        record["link"] = vars(cfg).copy()
    elif body.type == "battery":
        link = _guard(sess.link, body.uin)
        target = float(p.get("target_pct", 15))
        # PX4 battery simulator: drain quickly down to the forced floor.
        record["SIM_BAT_MIN_PCT"] = _guard(link.set_param, "SIM_BAT_MIN_PCT", target)
        record["SIM_BAT_DRAIN"] = _guard(link.set_param, "SIM_BAT_DRAIN", float(p.get("drain_s", 20)))
    elif body.type == "gps_drift":
        link = _guard(sess.link, body.uin)
        record["result"] = _guard(_gps_drift, link, p)
    elif body.type == "wind":
        fx, fy = _wind_force(p)
        targets = [body.uin] if body.uin else list(sess.links)
        models = [f"x500_{sess.index_of[u]}" for u in targets]
        for m in models:
            _guard(sess.world.apply_force, m, fx, fy, 0.0, True)
        duration = float(p.get("duration_s", 10))
        record.update({"force_n": [round(fx, 2), round(fy, 2)], "models": models, "duration_s": duration})

        def clear():
            time.sleep(duration)
            for m in models:
                try:
                    sess.world.clear_force(m)
                except Exception:
                    pass

        import threading

        threading.Thread(target=clear, daemon=True).start()
    elif body.type == "obstacle":
        from enu import EnuFrame  # noqa: F401 - frame lives on the session

        lat, lon = float(p["latitude"]), float(p["longitude"])
        x, y, _ = sess.frame.to_enu(lat, lon, sess.frame.h0)
        size = p.get("size_m", [6.0, 6.0, 30.0])
        ground = float(p.get("ground_z", 0.0))
        name = p.get("name") or f"obstacle_{len(sess.obstacles) + 1}"
        sdf = (
            f'<sdf version="1.9"><model name="{name}"><static>true</static><pose>{x:.3f} {y:.3f} {ground + size[2] / 2:.3f} 0 0 0</pose>'
            f'<link name="link"><collision name="c"><geometry><box><size>{size[0]} {size[1]} {size[2]}</size></box></geometry></collision>'
            f'<visual name="v"><geometry><box><size>{size[0]} {size[1]} {size[2]}</size></box></geometry>'
            '<material><ambient>0.82 0.23 0.18 1</ambient><diffuse>0.82 0.23 0.18 1</diffuse></material></visual></link></model></sdf>'
        )
        ok = _guard(sess.world.create_entity, sdf, name)
        exists = sess.world.entity_exists(name)
        pose = sess.world.entity_pose(name) if exists else None
        back = sess.frame.to_geodetic(pose[0], pose[1], 0.0) if pose else None
        obstacle = {"name": name, "requested": {"latitude": lat, "longitude": lon}, "gazebo_xy": [round(x, 3), round(y, 3)],
                    "created": ok, "exists_in_gazebo": exists, "gazebo_pose": pose,
                    "gazebo_latlon": [back[0], back[1]] if back else None}
        sess.obstacles.append(obstacle)
        record["obstacle"] = obstacle
    else:
        raise HTTPException(422, f"Unknown failure type {body.type}")
    sess.failures.append(record)
    return record


def _wind_force(p: dict) -> tuple[float, float]:
    """Gust as a horizontal force on base_link. `speed_mps` + `from_deg`
    (meteorological: direction the wind comes FROM) -> drag ~ 0.5*rho*Cd*A*v^2."""
    v = float(p.get("speed_mps", 8.0))
    from_deg = float(p.get("from_deg", 270.0))
    force = 0.5 * 1.225 * 1.0 * float(p.get("area_m2", 0.12)) * v * v
    to = math.radians((from_deg + 180.0) % 360.0)  # compass bearing the air moves towards
    return force * math.sin(to), force * math.cos(to)  # ENU: x east, y north


def _gps_drift(link, p: dict) -> dict:
    """GPS drift as a controlled bias on PX4's simulated GPS measurement
    (estimator input). PX4 v1.15's GPS failure injection only exists in the
    legacy simulator_mavlink path, so the image carries a small patch
    (sim/px4_patches/gps_drift.py) adding SIM_GPS_DRIFT_N / SIM_GPS_DRIFT_E.
    Gazebo ground truth is untouched, so true vs reported stay distinguishable."""
    enabled = bool(p.get("enabled", True))
    north = float(p.get("north_m", 25.0)) if enabled else 0.0
    east = float(p.get("east_m", 15.0)) if enabled else 0.0
    result = {"enabled": enabled, "SIM_GPS_DRIFT_N": link.set_param("SIM_GPS_DRIFT_N", north),
              "SIM_GPS_DRIFT_E": link.set_param("SIM_GPS_DRIFT_E", east)}
    link.gps_drift = result if enabled else None
    return result


@app.post("/sessions/{session_id}/stop")
def stop(session_id: str) -> dict:
    sess = _session(session_id)
    result = sess.stop()
    manager.forget_session(session_id)
    leftovers = [p for p in running_px4_processes() if p["session"] == session_id]
    return {**result, "leftover_processes": leftovers, "state": sess.state}


@app.get("/processes")
def processes() -> dict:
    return {"px4": running_px4_processes(), "tracked": {sid: manager.health(sid) for sid in sessions}}


# ------------------------------------------------- evidence (GUI capture)
# The Gazebo GUI runs on a virtual X display (Xvfb, Mesa llvmpipe). These
# endpoints only observe the running world; they never change simulation state.

DISPLAY = os.environ.get("SIM_DISPLAY", ":99")
GUI_CROP = "1000x844"  # the Gazebo GUI window opens at this size on the virtual display
_gui: dict = {}


class CameraIn(BaseModel):
    x: float
    y: float
    z: float
    look_at: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])


class CaptureIn(BaseModel):
    filename: str = Field(..., pattern=r"^[A-Za-z0-9_.-]{1,80}$")


@app.post("/sessions/{session_id}/gui/start")
def gui_start(session_id: str, width: int = 1600, height: int = 1000) -> dict:
    import subprocess

    sess = _session(session_id)
    if _gui.get("xvfb") is None or _gui["xvfb"].poll() is not None:
        _gui["xvfb"] = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", f"{width}x{height}x24", "-nolisten", "tcp"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(1.5)
    sess.world.start_gui(DISPLAY, width, height)
    time.sleep(12)  # ogre2 on llvmpipe needs a moment for the first frame
    return {"display": DISPLAY, "gui_pid": sess.world.gui.pid if sess.world.gui else None}


@app.post("/sessions/{session_id}/gui/camera")
def gui_camera(session_id: str, body: CameraIn) -> dict:
    """Point the GUI camera from (x, y, z) at `look_at` (Gazebo world frame).
    Uses the gz CLI: the GUI's /gui/move_to/pose service answers it reliably."""
    import subprocess

    _session(session_id)
    dx, dy, dz = (body.look_at[0] - body.x, body.look_at[1] - body.y, body.look_at[2] - body.z)
    yaw = math.atan2(dy, dx)
    pitch = -math.atan2(dz, math.hypot(dx, dy))
    cy, sy, cp, sp = math.cos(yaw / 2), math.sin(yaw / 2), math.cos(pitch / 2), math.sin(pitch / 2)
    w, x, y, z = cy * cp, -sy * sp, cy * sp, sy * cp
    req = f"pose: {{position: {{x: {body.x}, y: {body.y}, z: {body.z}}}, orientation: {{w: {w}, x: {x}, y: {y}, z: {z}}}}}"
    res = subprocess.run(["gz", "service", "-s", "/gui/move_to/pose", "--reqtype", "gz.msgs.GUICamera", "--reptype", "gz.msgs.Boolean",
                          "--timeout", "20000", "--req", req], capture_output=True, text=True, timeout=30)
    return {"ok": "data: true" in res.stdout}


class FollowIn(BaseModel):
    model: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,64}$")
    offset: list[float] = Field(default_factory=lambda: [-8.0, -6.0, 4.0])


@app.post("/sessions/{session_id}/gui/follow")
def gui_follow(session_id: str, body: FollowIn) -> dict:
    """Chase-camera on a model (GUI /gui/follow + /gui/follow/offset)."""
    import subprocess

    _session(session_id)
    ox, oy, oz = body.offset
    a = subprocess.run(["gz", "service", "-s", "/gui/follow/offset", "--reqtype", "gz.msgs.Vector3d", "--reptype", "gz.msgs.Boolean",
                        "--timeout", "20000", "--req", f"x: {ox}, y: {oy}, z: {oz}"], capture_output=True, text=True, timeout=30)
    b = subprocess.run(["gz", "service", "-s", "/gui/follow", "--reqtype", "gz.msgs.StringMsg", "--reptype", "gz.msgs.Boolean",
                        "--timeout", "20000", "--req", f'data: "{body.model}"'], capture_output=True, text=True, timeout=30)
    return {"ok": "data: true" in a.stdout and "data: true" in b.stdout}


@app.post("/sessions/{session_id}/gui/screenshot")
def gui_screenshot(session_id: str, body: CaptureIn) -> dict:
    import subprocess

    sess = _session(session_id)
    out = Path(sess.spec.world_sdf).parent / "evidence" / body.filename
    out.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", GUI_CROP, "-i", f"{DISPLAY}.0", "-frames:v", "1", str(out)],
                         capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        raise HTTPException(500, f"Screenshot failed: {res.stderr[-300:]}")
    return {"path": str(out), "bytes": out.stat().st_size}


@app.post("/sessions/{session_id}/gui/record/start")
def record_start(session_id: str, body: CaptureIn) -> dict:
    import subprocess

    sess = _session(session_id)
    out = Path(sess.spec.world_sdf).parent / "evidence" / body.filename
    out.parent.mkdir(parents=True, exist_ok=True)
    _gui["rec"] = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-framerate", "10", "-video_size", GUI_CROP, "-i", f"{DISPLAY}.0",
                                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE, start_new_session=True)
    _gui["rec_path"] = out
    return {"recording": str(out)}


@app.post("/sessions/{session_id}/gui/record/stop")
def record_stop(session_id: str) -> dict:
    rec = _gui.pop("rec", None)
    if rec is None:
        raise HTTPException(409, "Not recording")
    try:
        rec.communicate(b"q", timeout=20)
    except Exception:
        rec.kill()
    path = _gui.pop("rec_path")
    return {"path": str(path), "bytes": path.stat().st_size if path.exists() else 0}
