"""MAVLink links: serial telemetry radios (COMn), UDP or TCP endpoints.

One link can carry several aircraft (radios on one network, or a hub that
multiplexes them): every ArduPilot autopilot that sends a HEARTBEAT on the
link becomes a Vehicle, keyed by its MAVLink system id. Every aircraft must
therefore have a unique SYSID_THISMAV.

The link sends a 1 Hz ground-station heartbeat so ArduPilot's GCS failsafe
(FS_GCS_ENABLE) triggers if this laptop or the radio link is lost.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable

from hwbridge.ardupilot import MAV_AUTOPILOT_ARDUPILOTMEGA, MAV_TYPE_GCS, Limits, Vehicle

logger = logging.getLogger("hwbridge.links")

ENDPOINT_RE = re.compile(r"^(serial:(COM\d{1,3}|/dev/tty[A-Za-z0-9]{1,20})|udp:[A-Za-z0-9.\-]{1,63}:\d{2,5}|tcp:[A-Za-z0-9.\-]{1,63}:\d{2,5})$")
GCS_SYSID, GCS_COMPID = 255, 190


class _LockedMav:
    """pymavlink senders are not thread-safe: serialize every *_send call."""

    def __init__(self, mav, lock: threading.Lock):
        self._mav, self._lock = mav, lock

    def __getattr__(self, name):
        attr = getattr(self._mav, name)
        if not callable(attr) or not name.endswith("_send"):
            return attr

        def call(*a, **kw):
            with self._lock:
                return attr(*a, **kw)
        return call


def link_name(endpoint: str) -> str:
    kind, rest = endpoint.split(":", 1)
    if kind == "serial":
        return rest.rsplit("/", 1)[-1]
    return f"{kind}{rest.rsplit(':', 1)[-1]}"


def connection_string(endpoint: str) -> str:
    kind, rest = endpoint.split(":", 1)
    if kind == "serial":
        return rest
    if kind == "udp":
        return f"udpin:{rest}"
    return f"tcp:{rest}"


class Link:
    def __init__(self, endpoint: str, baud: int, limits: Limits, on_event: Callable[[Vehicle, str, dict], None]):
        if not ENDPOINT_RE.match(endpoint):
            raise ValueError(f"Invalid endpoint {endpoint!r}: use serial:COM5, udp:0.0.0.0:14550 or tcp:127.0.0.1:5760")
        self.endpoint = endpoint
        self.name = link_name(endpoint)
        self.baud = baud
        self.limits = limits
        self.on_event = on_event
        self.vehicles: dict[int, Vehicle] = {}
        self.ignored: dict[int, str] = {}
        self.conn = None
        self.mav = None
        self.error: str | None = None
        self.rx_count = 0
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for target, tag in ((self._rx_loop, "rx"), (self._heartbeat_loop, "hb")):
            t = threading.Thread(target=target, name=f"link-{self.name}-{tag}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass

    def _open(self) -> bool:
        from pymavlink import mavutil

        try:
            self.conn = mavutil.mavlink_connection(connection_string(self.endpoint), baud=self.baud, source_system=GCS_SYSID,
                                                   source_component=GCS_COMPID, autoreconnect=True)
            self.mav = _LockedMav(self.conn.mav, self._send_lock)
            for v in self.vehicles.values():
                v.mav = self.mav
            self.error = None
            logger.info("link %s open", self.endpoint)
            return True
        except Exception as exc:  # port busy (Mission Planner open?), unplugged radio
            self.error = f"{type(exc).__name__}: {exc}"
            self.conn = self.mav = None
            return False

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            if self.conn is None and not self._open():
                self._stop.wait(2.0)
                continue
            try:
                msg = self.conn.recv_match(blocking=True, timeout=0.5)
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                logger.warning("link %s read failed: %s; reopening", self.endpoint, exc)
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = self.mav = None
                self._stop.wait(1.0)
                continue
            if msg is None or msg.get_type() == "BAD_DATA":
                continue
            self.rx_count += 1
            self._route(msg)

    def _route(self, msg) -> None:
        sysid, compid = msg.get_srcSystem(), msg.get_srcComponent()
        if sysid == GCS_SYSID:
            return
        v = self.vehicles.get(sysid)
        if v is None:
            if msg.get_type() != "HEARTBEAT" or msg.type == MAV_TYPE_GCS or compid != 1:
                return
            if msg.autopilot != MAV_AUTOPILOT_ARDUPILOTMEGA:
                self.ignored[sysid] = f"autopilot {msg.autopilot} (only ArduPilot is supported)"
                return
            v = Vehicle(self.name, self.endpoint, sysid, compid, self.mav, self.limits, lambda kind, data, s=sysid: self._event(s, kind, data))
            self.vehicles[sysid] = v
            logger.info("link %s: ArduPilot system %d found", self.endpoint, sysid)
        if compid != v.compid and msg.get_type() not in ("RADIO_STATUS", "STATUSTEXT"):
            return  # gimbals, cameras, companion computers
        v.handle(msg)
        # (Re)request message rates when position is missing or every 60 s.
        now = time.time()
        if now - v.streams_requested > (5 if v.state.lat is None else 60):
            v.request_streams()

    def _event(self, sysid: int, kind: str, data: dict) -> None:
        v = self.vehicles.get(sysid)
        if v is not None:
            self.on_event(v, kind, data)

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            if self.mav is not None:
                try:
                    self.mav.heartbeat_send(MAV_TYPE_GCS, 8, 0, 0, 4)  # MAV_AUTOPILOT_INVALID, MAV_STATE_ACTIVE
                except Exception:
                    pass
            self._stop.wait(1.0)

    def status(self) -> dict:
        return {"endpoint": self.endpoint, "open": self.conn is not None, "error": self.error, "rx_messages": self.rx_count,
                "vehicles": sorted(self.vehicles), "ignored": self.ignored}
