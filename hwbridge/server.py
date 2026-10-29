"""Drone MatrX hardware bridge: real ArduPilot aircraft over MAVLink.

Runs on the operations laptop (it needs the radios' COM ports), listens on
127.0.0.1 only, and is called by the backend -- never by browsers.

    python -m hwbridge.server --config hwbridge/config.json

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
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from hwbridge.ardupilot import MAV_RESULT, Limits, Vehicle, VehicleError
from hwbridge.links import Link

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hwbridge")

CONFIG: dict = {}
state: dict = {"links": [], "published": 0, "publish_errors": 0}


def load_config(path: str | None) -> dict:
    p = Path(path or os.environ.get("HWBRIDGE_CONFIG", "hwbridge/config.json"))
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg.setdefault("nats_url", "nats://127.0.0.1:4222")
    cfg.setdefault("nats_stream", "DRONES")
    cfg.setdefault("http_port", 8110)
    cfg.setdefault("links", [])
    cfg.setdefault("cameras", {})
    cfg.setdefault("limits", {})
    return cfg


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


# ----------------------------------------------------------------- app


@asynccontextmanager
async def lifespan(app: FastAPI):
    state["loop"] = asyncio.get_running_loop()
    limits = Limits.from_dict(CONFIG.get("limits"))
    for spec in CONFIG["links"]:
        link = Link(spec["endpoint"], int(spec.get("baud", 57600)), limits, _on_event)
        link.start()
        state["links"].append(link)
    await _nats_connect()
    task = asyncio.create_task(_telemetry_loop())
    yield
    task.cancel()
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


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="Drone MatrX hardware bridge (ArduPilot over MAVLink)")
    ap.add_argument("--config", default=None, help="path to config.json (default hwbridge/config.json)")
    args = ap.parse_args()
    CONFIG.update(load_config(args.config))
    uvicorn.run(app, host="127.0.0.1", port=int(CONFIG["http_port"]), log_level="info")


if __name__ == "__main__":
    main()
