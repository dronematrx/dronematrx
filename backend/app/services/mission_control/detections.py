"""Detection feed: ingest inference events, operator review, map layers.

Producers publish `drone.{UIN}.detection` on NATS (the existing bus). The
runtime's `ui` consumer hands them to `DetectionService.on_bus_message`,
which persists them (idempotent on event_id) and pushes a `detection`
WebSocket event. Providers:

    sim_colour_detector  simhost: colour segmentation on SITL camera frames
    test_fixture         deterministic fixtures (admin, test mode), labelled TEST FIXTURE

A location is stored only when the producer supplied valid coordinates; a
point geometry (PostGIS geography, SRID 4326) is written only when an
operator ACCEPTS the detection. Rejections keep the event.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, defer, sessionmaker

from app.models.audit_log import AuditLog
from app.models.drone import Drone
from app.models.mission_control import Detection
from app.models.user import User
from app.services.mission_control import fleet as fleetsvc

logger = logging.getLogger("dronematrx.detections")
MAX_THUMBNAIL_BYTES = 200_000
PNG_MAGIC, JPEG_MAGIC = b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"


class DetectionError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _ts(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    raise ValueError("inferred_at must be epoch seconds or ISO-8601")


def valid_location(lat: Any, lon: Any) -> tuple[float | None, float | None, str | None]:
    if lat is None and lon is None:
        return None, None, None
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None, "invalid coordinates (not numbers)"
    if not (math.isfinite(la) and math.isfinite(lo)) or not (-90 <= la <= 90 and -180 <= lo <= 180):
        return None, None, f"invalid coordinates ({lat}, {lon})"
    if la == 0 and lo == 0:
        return None, None, "invalid coordinates (0, 0: no fix)"
    return la, lo, None


def view(d: Detection, drone_name: str | None = None, has_thumbnail: bool | None = None) -> dict:
    return {"id": str(d.id), "event_id": d.event_id, "drone_id": str(d.drone_id) if d.drone_id else None, "drone_name": drone_name,
            "mission_id": str(d.mission_id) if d.mission_id else None, "provider": d.provider, "label": d.label, "confidence": d.confidence,
            "bbox": d.bbox, "has_thumbnail": (d.thumbnail is not None) if has_thumbnail is None else has_thumbnail, "latitude": d.latitude, "longitude": d.longitude,
            "location_error": d.location_error, "inferred_at": d.inferred_at.isoformat(), "received_at": d.received_at.isoformat() if d.received_at else None,
            "review_status": d.review_status, "reviewed_by": str(d.reviewed_by) if d.reviewed_by else None,
            "reviewed_at": d.reviewed_at.isoformat() if d.reviewed_at else None, "reject_reason": d.reject_reason,
            "test_fixture": d.provider == "test_fixture", "details": d.details}


def ingest(db: Session, payload: dict) -> tuple[Detection | None, bool, str | None]:
    """Persist one inference event. Returns (row, created, error)."""
    try:
        drone_id = uuid.UUID(str(payload.get("drone_id")))
    except ValueError:
        return None, False, "event has no valid drone_id (unbound controller?)"
    drone = db.get(Drone, drone_id)
    if drone is None:
        return None, False, f"unknown drone {drone_id}"
    event_id = str(payload.get("event_id") or "")
    if not event_id or len(event_id) > 80:
        return None, False, "missing or too long event_id"
    try:
        confidence = float(payload["confidence"])
        inferred_at = _ts(payload["inferred_at"])
    except (KeyError, TypeError, ValueError) as exc:
        return None, False, f"invalid event: {exc}"
    if not 0 <= confidence <= 1:
        return None, False, "confidence must be within 0..1"
    lat, lon, loc_err = valid_location(payload.get("latitude"), payload.get("longitude"))
    loc_err = loc_err or (payload.get("location_error") if lat is None else None) or (None if lat is not None else "no location supplied")
    details = {k: payload[k] for k in ("location_uncertainty_m", "camera_height_m", "enu", "session_id", "fc_id", "source", "sim_time_s") if k in payload}
    thumb, thumb_type = None, None
    raw = payload.get("thumbnail_png_b64") or payload.get("thumbnail_b64")
    if raw:
        try:
            data = base64.b64decode(raw, validate=True)
            if len(data) > MAX_THUMBNAIL_BYTES:
                raise ValueError("thumbnail too large")
            if data.startswith(PNG_MAGIC):
                thumb, thumb_type = data, "image/png"
            elif data.startswith(JPEG_MAGIC):
                thumb, thumb_type = data, "image/jpeg"
            else:
                raise ValueError("thumbnail is not PNG or JPEG")
        except (ValueError, TypeError) as exc:
            details["thumbnail_error"] = str(exc)
    else:
        details["thumbnail_error"] = "no thumbnail supplied"
    mission = fleetsvc.mission_assignments(db, drone.organization_id).get(str(drone.id))
    values = dict(id=uuid.uuid4(), organization_id=drone.organization_id, event_id=event_id, drone_id=drone.id,
                  mission_id=uuid.UUID(mission["mission_id"]) if mission else None, provider=str(payload.get("provider", "unknown"))[:40],
                  label=str(payload.get("label", "object"))[:40], confidence=confidence, bbox=payload.get("bbox") or {}, thumbnail=thumb,
                  thumbnail_type=thumb_type, latitude=lat, longitude=lon, location_error=loc_err, inferred_at=inferred_at,
                  received_at=fleetsvc.now_utc(), review_status="pending", details=details)
    res = db.execute(insert(Detection).values(**values).on_conflict_do_nothing(index_elements=["event_id"]).returning(Detection.id))
    new_id = res.scalar()
    db.commit()
    row = db.execute(select(Detection).where(Detection.event_id == event_id)).scalars().first()
    return row, new_id is not None, None


def review(db: Session, user: User, detection_id: uuid.UUID, decision: str, reason: str | None) -> tuple[dict, bool]:
    """Accept or reject in one transaction (row lock). Repeating the same
    decision is idempotent (no second audit row); a different decision on an
    already reviewed detection is refused."""
    if decision not in ("accepted", "rejected"):
        raise DetectionError("INVALID_DECISION", "decision must be accepted or rejected", 422)
    d = db.execute(select(Detection).where(Detection.id == detection_id).with_for_update()).scalars().first()
    if d is None or d.organization_id != user.organization_id:
        raise DetectionError("DETECTION_NOT_FOUND", "Detection not found", 404)
    if d.review_status == decision:
        db.commit()  # nothing changed: just release the row lock
        return view(d), False
    if d.review_status != "pending":
        db.commit()
        raise DetectionError("ALREADY_REVIEWED", f"Detection was already {d.review_status}", 409)
    d.review_status, d.reviewed_by, d.reviewed_at = decision, user.id, fleetsvc.now_utc()
    d.reject_reason = ((reason or "").strip()[:500] or None) if decision == "rejected" else None
    if decision == "accepted" and d.latitude is not None and d.longitude is not None:
        d.accepted_location = func.ST_GeogFromText(f"SRID=4326;POINT({d.longitude!r} {d.latitude!r})")
    db.add(AuditLog(user_id=user.id, action=f"detection_{decision}", resource="detection", resource_id=str(d.id),
                    event_metadata={"event_id": d.event_id, "label": d.label, "confidence": d.confidence, "reason": d.reject_reason,
                                    "geometry": d.latitude is not None and decision == "accepted"}))
    db.commit()
    db.refresh(d)
    return view(d), True


def listing(db: Session, org_id: uuid.UUID, status: str | None = None, mission_id: uuid.UUID | None = None, limit: int = 100) -> list[dict]:
    # Thumbnail bytes are served by their own endpoint: never load them for a list.
    q = (select(Detection, Drone.name, Detection.thumbnail.is_not(None)).options(defer(Detection.thumbnail))
         .outerjoin(Drone, Drone.id == Detection.drone_id).where(Detection.organization_id == org_id))
    if status:
        q = q.where(Detection.review_status == status)
    if mission_id:
        q = q.where(Detection.mission_id == mission_id)
    return [view(d, n, bool(has)) for d, n, has in db.execute(q.order_by(Detection.inferred_at.desc()).limit(limit)).all()]


def pending_count(db: Session, org_id: uuid.UUID) -> int:
    return db.execute(select(func.count()).select_from(Detection).where(Detection.organization_id == org_id,
                                                                        Detection.review_status == "pending")).scalar_one()


def accepted_geojson(db: Session, org_id: uuid.UUID, mission_id: uuid.UUID | None = None) -> dict:
    q = select(Detection.id, Detection.label, Detection.confidence, Detection.inferred_at, Detection.drone_id,
               func.ST_AsGeoJSON(Detection.accepted_location)).where(
        Detection.organization_id == org_id, Detection.review_status == "accepted", Detection.accepted_location.is_not(None))
    if mission_id:
        q = q.where(Detection.mission_id == mission_id)
    feats = [{"type": "Feature", "geometry": json.loads(g),
              "properties": {"id": str(i), "label": lab, "confidence": c, "inferred_at": t.isoformat(), "drone_id": str(dr) if dr else None}}
             for i, lab, c, t, dr, g in db.execute(q).all()]
    return {"type": "FeatureCollection", "features": feats}


def heatmap(db: Session, org_id: uuid.UUID, mission_id: uuid.UUID | None = None, cell_m: float = 20.0) -> dict:
    """Accepted detections counted per grid cell (Web-Mercator metres, snapped
    with ST_SnapToGrid). One detection row counts once."""
    cell_m = max(5.0, min(500.0, cell_m))
    sql = text("""
        SELECT ST_X(c) AS x, ST_Y(c) AS y, n FROM (
          SELECT ST_SnapToGrid(ST_Transform(accepted_location::geometry, 3857), :cell) AS c, count(DISTINCT id) AS n
          FROM detections
          WHERE organization_id = :org AND review_status = 'accepted' AND accepted_location IS NOT NULL
            AND (CAST(:mission AS uuid) IS NULL OR mission_id = CAST(:mission AS uuid))
          GROUP BY 1) g""")
    rows = db.execute(sql, {"cell": cell_m, "org": str(org_id), "mission": str(mission_id) if mission_id else None}).all()
    cells = []
    to_ll = text("SELECT ST_Y(p), ST_X(p) FROM (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(:x, :y), 3857), 4326) AS p) q")
    for x, y, n in rows:
        sw = db.execute(to_ll, {"x": x - cell_m / 2, "y": y - cell_m / 2}).one()
        ne = db.execute(to_ll, {"x": x + cell_m / 2, "y": y + cell_m / 2}).one()
        cells.append({"count": int(n), "south": sw[0], "west": sw[1], "north": ne[0], "east": ne[1]})
    return {"cell_m": cell_m, "max": max((c["count"] for c in cells), default=0), "cells": cells,
            "total": sum(c["count"] for c in cells)}


def thumbnail(db: Session, org_id: uuid.UUID, detection_id: uuid.UUID) -> tuple[bytes, str]:
    d = db.get(Detection, detection_id)
    if d is None or d.organization_id != org_id:
        raise DetectionError("DETECTION_NOT_FOUND", "Detection not found", 404)
    if d.thumbnail is None:
        raise DetectionError("NO_THUMBNAIL", "This detection has no thumbnail", 404)
    return d.thumbnail, d.thumbnail_type or "application/octet-stream"


# ------------------------------------------------------------------ fixtures

FIXTURE_LABELS = ("person", "vehicle", "red_marker", "debris")


def fixture_thumbnail(n: int, label: str) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (96, 96), ((40 * n) % 200 + 30, 60, 90))
    dr = ImageDraw.Draw(img)
    dr.rectangle([18, 22, 78, 74], outline=(255, 255, 0), width=3)
    dr.rectangle([0, 0, 95, 13], fill=(0, 0, 0))
    dr.text((3, 1), "TEST FIXTURE", fill=(255, 255, 0))
    dr.text((22, 78), label[:12], fill=(255, 255, 255))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def fixture_events(drone: Drone, count: int, anchor: tuple[float, float], seq0: int, with_location: str = "valid") -> list[dict]:
    """Deterministic fixture events. Location: valid (offsets from anchor),
    missing, or invalid. Values depend only on the sequence number."""
    out = []
    for i in range(count):
        n = seq0 + i
        label = FIXTURE_LABELS[n % len(FIXTURE_LABELS)]
        ev = {"event_id": f"fixture-{uuid.uuid4().hex}", "drone_id": str(drone.id), "provider": "test_fixture", "label": label,
              "confidence": round(0.55 + 0.4 * ((n * 37) % 100) / 100, 3), "bbox": {"x": 18, "y": 22, "w": 60, "h": 52},
              "inferred_at": fleetsvc.now_utc().isoformat(), "thumbnail_png_b64": base64.b64encode(fixture_thumbnail(n, label)).decode(),
              "source": "test_fixture"}
        if with_location == "valid":
            k = 111_320.0
            ev["latitude"] = anchor[0] + ((n % 5) - 2) * 12 / k
            ev["longitude"] = anchor[1] + ((n % 3) - 1) * 12 / (k * math.cos(math.radians(anchor[0])))
        elif with_location == "invalid":
            ev["latitude"], ev["longitude"] = 123.0, 72.6
        else:
            ev["location_error"] = "fixture without GPS"
        out.append(ev)
    return out


class DetectionService:
    """Bus listener + WebSocket fan-out."""

    def __init__(self, session_factory: sessionmaker, connections: Any):
        self.session_factory = session_factory
        self.connections = connections
        self.stats = {"ingested": 0, "duplicates": 0, "dropped": 0}
        self.fixture_seq = 0

    async def on_bus_message(self, msg: Any) -> None:
        if msg.kind != "detection":
            return
        await self.handle(dict(msg.payload))

    async def handle(self, payload: dict) -> dict | None:
        def work():
            with self.session_factory() as db:
                row, created, err = ingest(db, payload)
                if row is None:
                    return None, created, err
                name = db.get(Drone, row.drone_id).name if row.drone_id else None
                return (view(row, name), str(row.organization_id)), created, err

        result, created, err = await asyncio.to_thread(work)
        if result is None:
            self.stats["dropped"] += 1
            logger.warning("detection dropped: %s", err)
            return None
        data, org = result
        if not created:
            self.stats["duplicates"] += 1
            return data
        self.stats["ingested"] += 1
        data["broadcast_at"] = fleetsvc.now_utc().isoformat()
        if self.connections is not None:
            await self.connections.broadcast_json({"type": "detection", "data": data}, organization_id=org)
        return data
