"""Sprint 6 detection provider on SITL camera frames.

A classical colour-segmentation detector (no trained model): it finds the
red ground markers placed in the bench world in the nadir camera image of a
simulated drone, and geolocates them from the drone's Gazebo pose and the
camera model. It is labelled `sim_colour_detector` everywhere, so it is
never mistaken for an AI model running on real imagery.

Geolocation (camera x500_dm_cam: nadir, image top = drone forward,
image right = drone right, horizontal FOV `hfov`):

    f = (W / 2) / tan(hfov / 2)                       focal length in pixels
    fwd = (H/2 - v) / f * h,   right = (u - W/2) / f * h     metres on flat ground
    east  = x + fwd * cos(yaw) + right * sin(yaw)
    north = y + fwd * sin(yaw) - right * cos(yaw)    (yaw: ENU, CCW from east)

h is the camera height above the ground. It assumes a level camera: hover
tilt of a few degrees moves the estimate by h * tan(tilt), reported as the
location uncertainty.
"""
from __future__ import annotations

import io
import math
import struct
import time
import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

HFOV = 1.2
LABEL = "red_marker"
PROVIDER = "sim_colour_detector"
MIN_PIXELS = 30
COOLDOWN_S = 120.0  # the same marker is re-reported by the same drone at most every 2 min
SAME_TARGET_M = 6.0


@dataclass
class Detector:
    drone_pose: Callable[[], tuple[float, float, float] | None]  # ENU x, y, z of the drone
    drone_yaw: Callable[[], float | None]
    ground_z: float  # terrain height (ENU z) under the bench, flat world
    to_geodetic: Callable[[float, float, float], tuple[float, float, float]]
    emit: Callable[[dict], None]
    every_n: int = 3  # run on every 3rd frame (~3 Hz at 10 Hz)
    _n: int = 0
    _recent: list = field(default_factory=list)  # (wall, east, north)

    def on_frame(self, rgb: np.ndarray, wall: float) -> None:
        self._n += 1
        if self._n % self.every_n:
            return
        found = find_red_target(rgb)
        if found is None:
            return
        pose, yaw = self.drone_pose(), self.drone_yaw()
        event = {"event_id": f"simdet-{uuid.uuid4().hex}", "provider": PROVIDER, "label": LABEL,
                 "confidence": found["confidence"], "bbox": found["bbox"], "inferred_at": wall,
                 "thumbnail_png_b64": _b64(thumbnail(rgb, found["bbox"])), "thumbnail_type": "image/png"}
        if pose is None or yaw is None:
            event["location_error"] = "drone pose unavailable"
        else:
            h = pose[2] - self.ground_z
            if h < 2.0:
                event["location_error"] = f"camera {h:.1f} m above ground: too low to geolocate"
            else:
                east, north = pixel_to_ground(found["centroid"], rgb.shape[1], rgb.shape[0], pose, yaw, h)
                if self._seen_recently(wall, east, north):
                    return
                lat, lon, _ = self.to_geodetic(east, north, self.ground_z)
                event.update(latitude=lat, longitude=lon, location_uncertainty_m=round(h * math.tan(math.radians(5)), 2),
                             enu={"east": round(east, 2), "north": round(north, 2)}, camera_height_m=round(h, 2))
        self.emit(event)

    def _seen_recently(self, wall: float, east: float, north: float) -> bool:
        self._recent = [r for r in self._recent if wall - r[0] < COOLDOWN_S]
        if any(math.hypot(east - e, north - n) < SAME_TARGET_M for _, e, n in self._recent):
            return True
        self._recent.append((wall, east, north))
        return False


def find_red_target(rgb: np.ndarray) -> dict | None:
    """Largest saturated-red region. Confidence: how cleanly red its pixels
    are (mean red dominance) times how compact the blob is (fill of its bbox)."""
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    mask = (r > 120) & (r - g > 70) & (r - b > 70)
    count = int(mask.sum())
    if count < MIN_PIXELS:
        return None
    ys, xs = np.nonzero(mask)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    fill = count / float((x1 - x0 + 1) * (y1 - y0 + 1))
    dominance = float(((r - np.maximum(g, b))[mask]).mean()) / 255.0
    confidence = round(max(0.0, min(1.0, 0.5 * fill + 0.5 * min(1.0, dominance * 1.6))), 3)
    return {"bbox": {"x": x0, "y": y0, "w": x1 - x0 + 1, "h": y1 - y0 + 1}, "centroid": (float(xs.mean()), float(ys.mean())),
            "pixels": count, "confidence": confidence}


def pixel_to_ground(centroid: tuple[float, float], width: int, height: int, pose: tuple[float, float, float], yaw: float, h: float,
                    hfov: float = HFOV) -> tuple[float, float]:
    u, v = centroid
    f = (width / 2.0) / math.tan(hfov / 2.0)
    fwd = (height / 2.0 - v) / f * h
    right = (u - width / 2.0) / f * h
    east = pose[0] + fwd * math.cos(yaw) + right * math.sin(yaw)
    north = pose[1] + fwd * math.sin(yaw) - right * math.cos(yaw)
    return east, north


def thumbnail(rgb: np.ndarray, bbox: dict, size: int = 96) -> bytes:
    """Square crop around the bbox (with margin), nearest-neighbour resized, as PNG."""
    cx, cy = bbox["x"] + bbox["w"] / 2, bbox["y"] + bbox["h"] / 2
    half = max(bbox["w"], bbox["h"]) * 0.9 + 8
    hgt, wid = rgb.shape[:2]
    x0, x1 = int(max(0, cx - half)), int(min(wid, cx + half))
    y0, y1 = int(max(0, cy - half)), int(min(hgt, cy + half))
    crop = rgb[y0:y1, x0:x1]
    yi = (np.arange(size) * crop.shape[0] / size).astype(int)
    xi = (np.arange(size) * crop.shape[1] / size).astype(int)
    return png(crop[yi][:, xi])


def png(rgb: np.ndarray) -> bytes:
    h, w = rgb.shape[:2]
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    out = io.BytesIO()
    out.write(b"\x89PNG\r\n\x1a\n")
    out.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
    out.write(chunk(b"IDAT", zlib.compress(raw, 6)))
    out.write(chunk(b"IEND", b""))
    return out.getvalue()


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode()


def now() -> float:
    return time.time()
