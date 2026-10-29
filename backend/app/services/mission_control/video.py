"""Video wall backend: source registry, WHEP signalling, recording and DVR.

The browser never talks to the media relay's HTTP ports. It sends its WebRTC
offer to the backend (authenticated, org-scoped), which forwards it to
MediaMTX with the backend's own credentials and returns the answer. Media
then flows browser <-> MediaMTX (UDP/TCP 8189).

Feed status separates three failures the UI must not confuse:

    relay_unavailable   the media relay (signalling) cannot be reached
    source_unavailable  the relay is up but nothing is publishing this path
    live                a publisher is present (media problems are detected
                        in the browser from frame progress)
"""
from __future__ import annotations

import secrets
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.drone import Drone
from app.models.mission_control import FlightControllerBinding, VideoSource
from app.models.user import User
from app.services.simulation.simhost import SimhostClient, SimhostError


class VideoError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


_whep_sessions: dict[str, dict] = {}  # token -> {location, feed_id, org, created}
_http = httpx.Client(timeout=10.0)


def _auth() -> tuple[str, str]:
    s = get_settings()
    return s.MEDIAMTX_USER, s.MEDIAMTX_PASS


def relay_paths() -> dict[str, dict] | None:
    """Path name -> MediaMTX path state, or None when the relay is unreachable."""
    try:
        r = _http.get(f"{get_settings().MEDIAMTX_API_URL}/v3/paths/list", auth=_auth(), params={"itemsPerPage": 500}, timeout=3)
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    return {p["name"]: p for p in r.json().get("items", [])}


def sync_sources(db: Session, org_id: uuid.UUID, client: SimhostClient) -> dict:
    """Register the sources simhost publishes: test patterns and SITL bench
    cameras (associated with the drone their controller is bound to)."""
    existing = {v.path: v for v in db.execute(select(VideoSource).where(VideoSource.organization_id == org_id)).scalars()}
    hw = _sync_hardware_cameras(db, org_id, existing)
    try:
        listed = client.video_sources()
    except SimhostError as exc:
        db.commit()
        return {"synced": hw, "error": f"simulator video sources unavailable: {exc}"}
    bindings = {b.endpoint.rsplit(":", 1)[-1]: b for b in db.execute(select(FlightControllerBinding).where(
        FlightControllerBinding.organization_id == org_id, FlightControllerBinding.state.in_(("binding", "bound")))).scalars()}
    seen = 0
    listed_paths: set[str] = set()
    for t in listed.get("test_patterns", []):
        path = f"{t['path']}/hi"
        v = existing.get(path) or VideoSource(organization_id=org_id, kind="test_pattern", path=path, low_path=f"{t['path']}/lo",
                                              label=f"TEST PATTERN {t['number']:02d} (not a drone camera)", record=False)
        v.active = True
        listed_paths.add(path)
        db.add(v)
        seen += 1
    for c in listed.get("sitl_cameras", []):
        path = f"{c['path']}/hi"
        b = bindings.get(c["fc_id"])
        v = existing.get(path) or VideoSource(organization_id=org_id, kind="sitl_camera", path=path, low_path=f"{c['path']}/lo",
                                              label=f"SITL camera {c['fc_id']}", record=False)
        v.drone_id = b.drone_id if b is not None else None
        drone = db.get(Drone, b.drone_id) if b is not None else None
        v.label = f"SITL camera - {drone.name}" if drone else f"SITL camera {c['fc_id']} (controller not bound)"
        v.active = True
        listed_paths.add(path)
        db.add(v)
        seen += 1
    # Sources simhost no longer publishes (a stopped bench's cameras) leave the wall.
    retired = 0
    for path, v in existing.items():
        if path not in listed_paths and v.kind in ("sitl_camera", "test_pattern") and v.active:
            v.active = False
            retired += 1
    db.commit()
    return {"synced": seen + hw, "retired": retired}


def _sync_hardware_cameras(db: Session, org_id: uuid.UUID, existing: dict[str, VideoSource]) -> int:
    """Real aircraft cameras (RTSP, configured per MAVLink system id in the
    hardware bridge). MediaMTX pulls each URL itself (path `hw/sysN`); the
    browser plays it through the same authenticated WHEP proxy as any feed."""
    from app.services.mission_control.fc import hw_client
    from app.services.mission_control.hwbridge import HwBridgeError

    try:
        cams = hw_client().video_sources().get("hardware_cameras", [])
    except HwBridgeError:
        return 0  # bridge not running: hardware feeds keep their last registration
    s = get_settings()
    names = {str(d.id): d.name for d in db.execute(select(Drone).where(Drone.organization_id == org_id)).scalars()}
    for c in cams:
        path = c["path"]
        body = {"source": c["url"], "sourceOnDemand": False, "rtspTransport": "tcp"}
        try:
            r = _http.post(f"{s.MEDIAMTX_API_URL}/v3/config/paths/add/{path}", json=body, auth=_auth(), timeout=4)
            if r.status_code == 400 and "already exists" in r.text:
                _http.patch(f"{s.MEDIAMTX_API_URL}/v3/config/paths/patch/{path}", json=body, auth=_auth(), timeout=4)
        except httpx.HTTPError:
            pass  # relay down: the feed shows relay_unavailable
        v = existing.get(path) or VideoSource(organization_id=org_id, kind="hardware_camera", path=path, low_path=None, record=False)
        v.drone_id = uuid.UUID(c["drone_id"]) if c.get("drone_id") else None
        drone = names.get(c["drone_id"]) if c.get("drone_id") else None
        v.label = f"Camera - {drone}" if drone else f"Camera of MAVLink system {c['system_id']} (aircraft not bound)"
        v.active = True
        existing[path] = v
        db.add(v)
    return len(cams)


def feed_view(v: VideoSource, paths: dict[str, dict] | None, drone_names: dict[str, str]) -> dict:
    if paths is None:
        status = "relay_unavailable"
        p = None
    else:
        p = paths.get(v.path)
        status = "live" if p and p.get("ready") else "source_unavailable"
    return {"id": str(v.id), "kind": v.kind, "label": v.label, "path": v.path, "low_path": v.low_path,
            "drone_id": str(v.drone_id) if v.drone_id else None, "drone_name": drone_names.get(str(v.drone_id)) if v.drone_id else None,
            "status": status, "recording": v.record, "tracks": (p or {}).get("tracks", []), "readers": len((p or {}).get("readers", [])),
            "live_label": {"sitl_camera": "SITL CAMERA (simulated)", "test_pattern": "TEST PATTERN", "hardware_camera": "LIVE CAMERA"}[v.kind]}


def feeds(db: Session, org_id: uuid.UUID) -> dict:
    rows = db.execute(select(VideoSource).where(VideoSource.organization_id == org_id, VideoSource.active.is_(True))
                      .order_by(VideoSource.kind, VideoSource.path)).scalars().all()
    names = {str(d.id): d.name for d in db.execute(select(Drone).where(Drone.organization_id == org_id)).scalars()}
    paths = relay_paths()
    items = [feed_view(v, paths, names) for v in rows]
    return {"relay": "unavailable" if paths is None else "ok", "feeds": items,
            "camera_configured": any(f["kind"] in ("sitl_camera", "hardware_camera") for f in items)}


def _feed(db: Session, org_id: uuid.UUID, feed_id: uuid.UUID) -> VideoSource:
    v = db.get(VideoSource, feed_id)
    if v is None or v.organization_id != org_id or not v.active:
        raise VideoError("FEED_NOT_FOUND", "Video feed not found", 404)
    return v


def whep_offer(db: Session, user: User, feed_id: uuid.UUID, rendition: str, sdp: str) -> dict:
    v = _feed(db, user.organization_id, feed_id)
    if rendition not in ("hi", "lo"):
        raise VideoError("INVALID_RENDITION", "rendition must be hi or lo", 422)
    if not sdp.startswith("v=0"):
        raise VideoError("INVALID_SDP", "Expected an SDP offer", 422)
    path = v.path if rendition == "hi" else (v.low_path or v.path)
    url = f"{get_settings().MEDIAMTX_WHEP_URL}/{path}/whep"
    t0 = time.perf_counter()
    try:
        r = _http.post(url, content=sdp.encode(), headers={"Content-Type": "application/sdp"}, auth=_auth(), timeout=8)
    except httpx.HTTPError as exc:
        raise VideoError("RELAY_UNAVAILABLE", f"Media relay unreachable: {type(exc).__name__}", 502) from exc
    if r.status_code == 404:
        raise VideoError("SOURCE_UNAVAILABLE", f"No source is publishing {path}", 404)
    if r.status_code >= 400:
        raise VideoError("RELAY_ERROR", f"Media relay refused the offer ({r.status_code}): {r.text[:200]}", 502)
    token = secrets.token_urlsafe(18)
    _whep_sessions[token] = {"location": r.headers.get("Location"), "feed_id": str(v.id), "org": str(user.organization_id), "created": time.time()}
    return {"sdp": r.text, "session": token, "path": path, "signalling_ms": round((time.perf_counter() - t0) * 1000, 1)}


def whep_close(user: User, token: str) -> bool:
    s = _whep_sessions.pop(token, None)
    if s is None or s["org"] != str(user.organization_id):
        return False
    loc = s.get("location")
    if loc:
        try:
            _http.delete(f"{get_settings().MEDIAMTX_WHEP_URL}{loc}" if loc.startswith("/") else loc, auth=_auth(), timeout=3)
        except httpx.HTTPError:
            pass
    return True


def set_recording(db: Session, user: User, feed_id: uuid.UUID, record: bool) -> dict:
    v = _feed(db, user.organization_id, feed_id)
    api = get_settings().MEDIAMTX_API_URL
    try:
        r = _http.patch(f"{api}/v3/config/paths/patch/{v.path}", json={"record": record}, auth=_auth(), timeout=5)
        if r.status_code == 404:
            r = _http.post(f"{api}/v3/config/paths/add/{v.path}", json={"record": record}, auth=_auth(), timeout=5)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise VideoError("RELAY_UNAVAILABLE", f"Could not change recording on the media relay: {exc}", 502) from exc
    v.record = record
    db.add(AuditLog(user_id=user.id, action="video_recording_started" if record else "video_recording_stopped", resource="video_feed",
                    resource_id=str(v.id), event_metadata={"path": v.path}))
    db.commit()
    return {"feed_id": str(v.id), "recording": v.record}


def recordings(db: Session, org_id: uuid.UUID, feed_id: uuid.UUID) -> dict:
    v = _feed(db, org_id, feed_id)
    try:
        r = _http.get(f"{get_settings().MEDIAMTX_PLAYBACK_URL}/list", params={"path": v.path}, auth=_auth(), timeout=5)
    except httpx.HTTPError as exc:
        raise VideoError("RELAY_UNAVAILABLE", f"DVR playback service unreachable: {type(exc).__name__}", 502) from exc
    if r.status_code == 404:
        return {"feed_id": str(v.id), "path": v.path, "recording": v.record, "segments": []}
    r.raise_for_status()
    return {"feed_id": str(v.id), "path": v.path, "recording": v.record, "segments": r.json()}


def playback(db: Session, org_id: uuid.UUID, feed_id: uuid.UUID, start: str, duration_s: float) -> bytes:
    v = _feed(db, org_id, feed_id)
    if not 1 <= duration_s <= 600:
        raise VideoError("INVALID_RANGE", "duration must be 1-600 s", 422)
    try:
        r = _http.get(f"{get_settings().MEDIAMTX_PLAYBACK_URL}/get", params={"path": v.path, "start": start, "duration": f"{duration_s}s",
                                                                               "format": "mp4"}, auth=_auth(), timeout=30)
    except httpx.HTTPError as exc:
        raise VideoError("RELAY_UNAVAILABLE", f"DVR playback service unreachable: {type(exc).__name__}", 502) from exc
    if r.status_code == 404:
        raise VideoError("NO_RECORDING", "No recording covers that time range for this feed", 404)
    if r.status_code >= 400:
        raise VideoError("DVR_ERROR", f"DVR error {r.status_code}: {r.text[:200]}", 502)
    return r.content


def clock_offset(client: SimhostClient, samples: int = 8) -> dict:
    """simhost clock - backend clock (ms), NTP-style from the lowest-RTT sample.
    Test-pattern capture times are stamped with the simhost clock; the browser
    runs on the backend host's clock."""
    best: tuple[float, float] | None = None
    for _ in range(samples):
        t0 = time.time() * 1000
        try:
            remote = client.clock()["epoch_ms"]
        except SimhostError as exc:
            raise VideoError("SIMHOST_UNAVAILABLE", str(exc), 502) from exc
        t1 = time.time() * 1000
        rtt = t1 - t0
        if best is None or rtt < best[0]:
            best = (rtt, remote - (t0 + t1) / 2)
    return {"offset_ms": round(best[1], 3), "rtt_ms": round(best[0], 3), "uncertainty_ms": round(best[0] / 2, 3)}


