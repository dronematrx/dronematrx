"""Sprint 6 simhost units: detector, geolocation, barcode, thumbnails and the
bench publish routing (an unbound controller publishes nothing)."""
import math
import struct
import zlib
from types import SimpleNamespace

import numpy as np

from simhost.detector import Detector, find_red_target, pixel_to_ground, png, thumbnail
from simhost.session import SessionSpec, SimSession
from simhost import video
from simhost.video import SitlCameraSource, decode_barcode, encode_barcode


def red_square(w=256, h=192, x=150, y=40, size=24, bg=(90, 120, 80)):
    f = np.zeros((h, w, 3), dtype=np.uint8)
    f[:] = bg
    f[y:y + size, x:x + size] = (210, 60, 46)  # the marker colour as rendered (0.82, 0.23, 0.18)
    return f


def test_red_target_found_with_bbox_and_confidence():
    hit = find_red_target(red_square())
    assert hit["bbox"] == {"x": 150, "y": 40, "w": 24, "h": 24}
    assert hit["centroid"] == (161.5, 51.5)
    assert 0.8 < hit["confidence"] <= 1.0


def test_no_target_on_terrain_or_tiny_blob():
    assert find_red_target(red_square(size=0)) is None
    assert find_red_target(red_square(size=4)) is None  # 16 px < MIN_PIXELS
    grey = np.full((192, 256, 3), 200, dtype=np.uint8)
    assert find_red_target(grey) is None


def test_pixel_to_ground_geometry():
    w, h, alt = 256, 192, 20.0
    # Image centre is straight below the drone.
    assert pixel_to_ground((w / 2, h / 2), w, h, (5.0, 7.0, 0), 0.3, alt) == (5.0, 7.0)
    f = (w / 2) / math.tan(1.2 / 2)
    # Facing east (yaw 0): image top = east, image right = south.
    e, n = pixel_to_ground((w / 2, h / 2 - f / 4), w, h, (0, 0, 0), 0.0, alt)
    assert abs(e - 5.0) < 1e-9 and abs(n) < 1e-9
    e, n = pixel_to_ground((w / 2 + f / 4, h / 2), w, h, (0, 0, 0), 0.0, alt)
    assert abs(e) < 1e-9 and abs(n + 5.0) < 1e-9
    # Facing north (yaw 90 deg): image top = north, image right = east.
    e, n = pixel_to_ground((w / 2, h / 2 - f / 4), w, h, (0, 0, 0), math.pi / 2, alt)
    assert abs(e) < 1e-9 and abs(n - 5.0) < 1e-9


def test_detector_emits_geolocated_event_once_per_marker():
    events = []
    det = Detector(drone_pose=lambda: (10.0, 20.0, 25.0), drone_yaw=lambda: 0.0, ground_z=0.0,
                   to_geodetic=lambda e, n, u: (23.0 + n / 111320, 72.0 + e / 102000, u), emit=events.append, every_n=1)
    frame = red_square(x=116, y=84, size=24)  # centred
    det.on_frame(frame, 1000.0)
    det.on_frame(frame, 1001.0)  # same marker, cooldown: no second event
    assert len(events) == 1
    ev = events[0]
    assert ev["provider"] == "sim_colour_detector" and ev["label"] == "red_marker"
    assert abs(ev["enu"]["east"] - 10.0) < 0.5 and abs(ev["enu"]["north"] - 20.0) < 0.5
    assert ev["camera_height_m"] == 25.0 and ev["thumbnail_png_b64"]


def test_detector_too_low_reports_missing_location():
    events = []
    det = Detector(drone_pose=lambda: (0.0, 0.0, 0.3), drone_yaw=lambda: 0.0, ground_z=0.0,
                   to_geodetic=lambda e, n, u: (0, 0, 0), emit=events.append, every_n=1)
    det.on_frame(red_square(), 1.0)
    assert "latitude" not in events[0] and "too low" in events[0]["location_error"]


def test_png_and_thumbnail_are_valid_png():
    data = thumbnail(red_square(), {"x": 150, "y": 40, "w": 24, "h": 24})
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    w, h = struct.unpack(">II", data[16:24])
    assert (w, h) == (96, 96)
    idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
    assert len(zlib.decompress(idat)) == 96 * (1 + 96 * 3)
    assert png(np.zeros((2, 3, 3), dtype=np.uint8)).endswith(b"IEND\xaeB`\x82")


def test_barcode_round_trip_and_half_scale():
    f = np.zeros((270, 480, 3), dtype=np.uint8)
    encode_barcode(f, 1792985052530, 1234)
    assert decode_barcode(f) == (1792985052530, 1234)
    half = f[::2, ::2]  # the lo rendition is half size
    assert decode_barcode(half, scale=0.5) == (1792985052530, 1234)


class FakeLink(SimpleNamespace):
    pass


def bench_session():
    out = []
    spec = SessionSpec(session_id="bench-aaaaaa", world_sdf="/x.sdf", world_name="w", origin={"latitude": 0, "longitude": 0}, drones=[], bench=True)
    s = SimSession.__new__(SimSession)
    SimSession.__init__(s, spec, manager=None, workdir="/tmp/b", publish=lambda u, k, p: out.append((u, k, p)))
    link = FakeLink(publish_enabled=False, pub_uin="FC-aaaaaa-01", drone_id="", name="FC-aaaaaa-01", index=0, port=14540, sysid=1, closed=False,
                    state=SimpleNamespace(firmware="PX4 v1.15.4", last_heartbeat=0, mode="HOLD", armed=False))
    s.links = {"FC-aaaaaa-01": link}
    s.index_of = {"FC-aaaaaa-01": 0}
    return s, out


def test_unbound_bench_controller_publishes_nothing():
    s, out = bench_session()
    s.publish("FC-aaaaaa-01", "telemetry", {"uin": "FC-aaaaaa-01", "drone_id": ""})
    s.publish("FC-aaaaaa-01", "detection", {"event_id": "x"})
    assert out == []


def test_bind_routes_and_unbind_silences():
    s, out = bench_session()
    info = s.bind("FC-aaaaaa-01", "drone-uuid", "SIM-01-UIN", "SIM-01")
    assert info["bound"] and info["drone_id"] == "drone-uuid" and info["system_id"] == 1 and info["firmware"] == "PX4 v1.15.4"
    s.publish("FC-aaaaaa-01", "telemetry", {"uin": "FC-aaaaaa-01", "drone_id": "", "latitude": 1})
    assert out == [("SIM-01-UIN", "telemetry", {"uin": "SIM-01-UIN", "drone_id": "drone-uuid", "name": "SIM-01", "latitude": 1})]
    s.unbind("FC-aaaaaa-01")
    s.publish("FC-aaaaaa-01", "telemetry", {"uin": "FC-aaaaaa-01"})
    assert len(out) == 1 and not s.fc_info("FC-aaaaaa-01")["bound"]


def test_stopped_camera_never_recreates_its_encoder(monkeypatch):
    """Gazebo keeps delivering frames after stop(): no new ffmpeg may be spawned."""
    created = []

    class FakeEncoder:
        def __init__(self, *args, **kwargs):
            self.closed = False
            created.append(self)

        def write(self, rgb):
            return True

        def close(self):
            self.closed = True

    monkeypatch.setattr(video, "_Encoder", FakeEncoder)
    cam = SitlCameraSource(SimpleNamespace(world_name="w"), "m", "FC-x-01", "/tmp")
    msg = SimpleNamespace(width=4, height=2, data=bytes(4 * 2 * 3))
    cam._on_image(msg)
    assert len(created) == 1 and cam.frames == 1
    cam.stop()
    assert created[0].closed
    cam._on_image(msg)
    assert len(created) == 1 and cam.frames == 1

