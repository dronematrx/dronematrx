"""Sprint 6 video sources, published to the media relay (MediaMTX) over RTSP.

Two kinds, never mixed up:

* SITL camera: frames from the Gazebo camera on a bench x500 (x500_dm_cam),
  i.e. what the simulated drone sees. Path `sitl/<fc_id>/{hi,lo}`.
* Test pattern: a deterministic generated source for tests and development,
  labelled TEST PATTERN in the picture. Path `test/<NN>/{hi,lo}`. Every
  frame carries a barcode with its capture time (epoch ms) and frame
  counter, so the browser can measure glass-to-glass latency and check
  where a DVR seek landed.

Each source is published in two renditions: `hi` for normal links and `lo`
(lower resolution, frame rate and bitrate) that the client switches to when
measured link quality degrades.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable

import numpy as np

logger = logging.getLogger("simhost.video")

RTSP_BASE = os.environ.get("MEDIAMTX_RTSP", "rtsp://mediamtx:8554")
PUBLISH_USER = os.environ.get("MEDIAMTX_PUBLISH_USER", "dmx-publisher")
PUBLISH_PASS = os.environ.get("MEDIAMTX_PUBLISH_PASS", "dmx-publisher-dev")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Barcode layout (shared with frontend/src/lib/videoBarcode.ts):
# BARCODE_BITS cells in one row at the top-left. Cell i is white for bit 1.
# Bits 0..47: capture time in epoch ms. Bits 48..63: frame counter mod 65536.
BARCODE_BITS = 64
CELL_W, CELL_H, BAR_X, BAR_Y = 6, 16, 8, 8


def rtsp_url(path: str) -> str:
    scheme, rest = RTSP_BASE.split("://", 1)
    return f"{scheme}://{PUBLISH_USER}:{PUBLISH_PASS}@{rest}/{path}"


def encode_barcode(frame: np.ndarray, epoch_ms: int, counter: int, scale: int = 1) -> None:
    value = (epoch_ms & ((1 << 48) - 1)) | ((counter & 0xFFFF) << 48)
    cw, ch, x0, y0 = CELL_W * scale, CELL_H * scale, BAR_X * scale, BAR_Y * scale
    frame[y0 - 2 * scale:y0 + ch + 2 * scale, x0 - 2 * scale:x0 + BARCODE_BITS * cw + 2 * scale] = 0
    for i in range(BARCODE_BITS):
        if (value >> i) & 1:
            frame[y0:y0 + ch, x0 + i * cw:x0 + (i + 1) * cw] = 255


def decode_barcode(frame: np.ndarray, scale: float = 1.0) -> tuple[int, int]:
    """Inverse of encode_barcode (used by tests; the browser has its own copy)."""
    value = 0
    for i in range(BARCODE_BITS):
        x = int((BAR_X + i * CELL_W + CELL_W / 2) * scale)
        y = int((BAR_Y + CELL_H / 2) * scale)
        if frame[y, x].mean() > 128:
            value |= 1 << i
    return value & ((1 << 48) - 1), value >> 48


class _Encoder:
    """One ffmpeg process: raw RGB frames in, hi + lo H.264 renditions out over RTSP."""

    def __init__(self, path: str, width: int, height: int, fps: int, hi_kbps: int, lo_kbps: int, label: str | None, log_dir: str):
        self.path = path
        self.width, self.height, self.fps = width, height, fps
        lo_w, lo_h = (width // 2) // 2 * 2, (height // 2) // 2 * 2
        lo_fps = max(1, fps // 2)
        draw = ""
        if label:
            # Label burnt into the picture: a test pattern can never pass for a camera.
            draw = (f",drawtext=fontfile={FONT}:text='{label}':x=w-tw-10:y=h-th-10:fontsize={max(12, height // 18)}"
                    ":fontcolor=yellow:box=1:boxcolor=black@0.6")
        x264 = ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-profile:v", "baseline", "-bf", "0", "-pix_fmt", "yuv420p"]
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
            "-use_wallclock_as_timestamps", "1", "-i", "pipe:0",
            "-filter_complex", f"[0:v]split=2[a][b];[a]null{draw}[hi];[b]scale={lo_w}:{lo_h},fps={lo_fps}{draw}[lo]",
            "-map", "[hi]", *x264, "-g", str(fps), "-b:v", f"{hi_kbps}k", "-maxrate", f"{hi_kbps}k", "-bufsize", f"{hi_kbps // 2}k",
            "-vsync", "0", "-f", "rtsp", "-rtsp_transport", "tcp", rtsp_url(f"{path}/hi"),
            "-map", "[lo]", *x264, "-g", str(lo_fps), "-b:v", f"{lo_kbps}k", "-maxrate", f"{lo_kbps}k", "-bufsize", f"{lo_kbps // 2}k",
            "-vsync", "0", "-f", "rtsp", "-rtsp_transport", "tcp", rtsp_url(f"{path}/lo"),
        ]
        os.makedirs(log_dir, exist_ok=True)
        self._log = open(os.path.join(log_dir, f"ffmpeg_{path.replace('/', '_')}.log"), "wb")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=self._log, stderr=subprocess.STDOUT, start_new_session=True)

    def write(self, rgb: np.ndarray) -> bool:
        if self.proc.poll() is not None:
            return False
        try:
            self.proc.stdin.write(rgb.tobytes())
            return True
        except (BrokenPipeError, ValueError):
            return False

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self._log.close()


class TestPatternSource:
    """Deterministic generated source TEST-NN. Pattern and colours depend only
    on NN and the frame counter; the barcode carries the capture time."""

    def __init__(self, number: int, log_dir: str, width: int = 480, height: int = 270, fps: int = 15):
        self.number = number
        self.path = f"test/{number:02d}"
        self.width, self.height, self.fps = width, height, fps
        self.log_dir = log_dir
        self.frames = 0
        self.restarts = 0
        self.started_at: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._encoder: _Encoder | None = None
        hue = (number * 47) % 360
        self._bg = self._background(hue)

    def _background(self, hue: int) -> np.ndarray:
        h, w = self.height, self.width
        yy, xx = np.mgrid[0:h, 0:w]
        rgb = np.zeros((h, w, 3), dtype=np.uint8)
        c = np.array(_hsv(hue, 0.55, 0.55), dtype=np.float32)
        shade = 0.6 + 0.4 * ((xx // 30 + yy // 30) % 2)
        rgb[:] = (c[None, None, :] * shade[..., None]).astype(np.uint8)
        return rgb

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        if self._encoder:  # left by a thread that died: close it before replacing it
            self._encoder.close()
        self._stop.clear()
        self._encoder = _Encoder(self.path, self.width, self.height, self.fps, 600, 150, f"TEST PATTERN {self.number:02d} - NOT A DRONE CAMERA", self.log_dir)
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._run, name=f"testsrc-{self.number}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self._encoder:
            self._encoder.close()
            self._encoder = None

    def _run(self) -> None:
        period = 1.0 / self.fps
        nxt = time.perf_counter()
        while not self._stop.is_set():
            frame = self._bg.copy()
            # Moving marker: shows the stream is live, not a still.
            x = int((self.frames * 6) % (self.width - 40))
            frame[self.height // 2 - 20:self.height // 2 + 20, x:x + 40] = (255, 255, 255)
            encode_barcode(frame, int(time.time() * 1000), self.frames)
            if not self._encoder or not self._encoder.write(frame):
                # Relay restarted or the RTSP session dropped: re-publish after 2 s.
                logger.warning("test source %s encoder exited; restarting", self.path)
                if self._encoder:
                    self._encoder.close()
                if self._stop.wait(2.0):
                    return
                self._encoder = _Encoder(self.path, self.width, self.height, self.fps, 600, 150,
                                         f"TEST PATTERN {self.number:02d} - NOT A DRONE CAMERA", self.log_dir)
                self.restarts += 1
                nxt = time.perf_counter()
                continue
            self.frames += 1
            nxt += period
            delay = nxt - time.perf_counter()
            if delay > 0:
                self._stop.wait(delay)
            else:
                nxt = time.perf_counter()

    def status(self) -> dict:
        return {"path": self.path, "kind": "test_pattern", "number": self.number, "running": self.running, "frames": self.frames, "restarts": self.restarts,
                "width": self.width, "height": self.height, "fps": self.fps, "started_at": self.started_at}


class SitlCameraSource:
    """Frames of a bench drone's Gazebo camera, re-published as video."""

    def __init__(self, world, model: str, fc_id: str, log_dir: str, on_frame: Callable[[np.ndarray, float], None] | None = None):
        self.world = world
        self.model = model
        self.fc_id = fc_id
        self.path = f"sitl/{fc_id}"
        self.log_dir = log_dir
        self.on_frame = on_frame
        self.topic = f"/world/{world.world_name}/model/{model}/link/dm_cam_link/sensor/dm_cam/image"
        self.frames = 0
        self.last_frame_wall: float | None = None
        self.width = self.height = None
        self._encoder: _Encoder | None = None
        self._retry_at = 0.0
        self.restarts = 0
        self._stopped = False
        self._lock = threading.Lock()
        self.latest: np.ndarray | None = None

    def start(self) -> None:
        from gz.msgs10.image_pb2 import Image

        self.world.subscribe(self.topic, Image, self._on_image)

    def _on_image(self, msg) -> None:
        now = time.time()
        rgb = np.frombuffer(msg.data, dtype=np.uint8)
        if rgb.size != msg.width * msg.height * 3:
            return
        rgb = rgb.reshape(msg.height, msg.width, 3)
        with self._lock:
            if self._stopped:
                # Gazebo keeps delivering frames until the world is gone: never
                # re-create an encoder after stop() (it would leak an ffmpeg).
                return
            if self._encoder is None:
                if now < self._retry_at:
                    return
                self.width, self.height = msg.width, msg.height
                self._encoder = _Encoder(self.path, msg.width, msg.height, 10, 400, 100, None, self.log_dir)
            ok = self._encoder.write(rgb)
            if not ok:
                # Relay restarted / RTSP dropped: drop this encoder, re-publish in 2 s.
                self._encoder.close()
                self._encoder = None
                self._retry_at = now + 2.0
                self.restarts += 1
        if not ok:
            return
        self.frames += 1
        self.last_frame_wall = now
        self.latest = rgb
        if self.on_frame:
            try:
                self.on_frame(rgb, now)
            except Exception:  # never let the detector stall the video
                logger.exception("frame callback failed")

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._encoder:
                self._encoder.close()
                self._encoder = None

    def status(self) -> dict:
        age = time.time() - self.last_frame_wall if self.last_frame_wall else None
        return {"path": self.path, "kind": "sitl_camera", "fc_id": self.fc_id, "model": self.model, "frames": self.frames, "restarts": self.restarts,
                "width": self.width, "height": self.height, "last_frame_age_s": round(age, 2) if age is not None else None,
                "running": age is not None and age < 3.0}


def _hsv(h: float, s: float, v: float) -> tuple[float, float, float]:
    import colorsys

    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, v)
    return r * 255, g * 255, b * 255
