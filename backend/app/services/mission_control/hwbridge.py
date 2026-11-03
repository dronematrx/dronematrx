"""HTTP client for the hardware bridge (hwbridge/, real ArduPilot aircraft).

The bridge runs on the operations laptop next to the telemetry radios and
listens on 127.0.0.1 only. Identities look like `serial:COM5/1`
(endpoint / MAVLink system id); the bridge's id for that controller is
`COM5-1` (see hwbridge.links.link_name).

Token: HWBRIDGE_TOKEN when set, otherwise the token the bridge writes to
hwbridge/.bridge_token on first start (same laptop), read on every call so
the bridge can be started after the backend.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[4]
HW_IDENTITY_RE = re.compile(r"^(?P<endpoint>(serial:(COM\d{1,3}|/dev/tty[A-Za-z0-9]{1,20})|udp:[A-Za-z0-9.\-]{1,63}:\d{2,5}|"
                            r"tcp:[A-Za-z0-9.\-]{1,63}:\d{2,5}))/(?P<sysid>\d{1,3})$")


class HwBridgeError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status, self.code = status, code


def parse_identity(identity: str) -> dict | None:
    m = HW_IDENTITY_RE.match(identity or "")
    if not m or not 1 <= int(m.group("sysid")) <= 254:
        return None
    endpoint = m.group("endpoint")
    kind, rest = endpoint.split(":", 1)
    name = rest.rsplit("/", 1)[-1] if kind == "serial" else f"{kind}{rest.rsplit(':', 1)[-1]}"
    return {"endpoint": endpoint, "conn": "serial" if kind == "serial" else "udp", "system_id": int(m.group("sysid")),
            "fc_id": f"{name}-{int(m.group('sysid'))}"}


def token_file() -> Path:
    s = get_settings()
    return Path(s.HWBRIDGE_TOKEN_FILE) if s.HWBRIDGE_TOKEN_FILE else REPO_ROOT / "hwbridge" / ".bridge_token"


class HwBridgeClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout_s: float = 12.0):
        s = get_settings()
        self.base_url = (base_url or s.HWBRIDGE_URL).rstrip("/")
        self._fixed_token = s.HWBRIDGE_TOKEN if token is None else token
        self.timeout_s = timeout_s
        self._local = threading.local()

    @property
    def token(self) -> str:
        if self._fixed_token:
            return self._fixed_token
        try:
            return token_file().read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _http(self) -> httpx.Client:
        token = self.token
        c = getattr(self._local, "client", None)
        if c is None or getattr(self._local, "token", None) != token:
            c = httpx.Client(base_url=self.base_url, timeout=self.timeout_s, headers={"X-Bridge-Token": token} if token else {})
            self._local.client, self._local.token = c, token
        return c

    def _call(self, method: str, path: str, json: Any = None, timeout_s: float | None = None, params: dict | None = None) -> Any:
        try:
            r = self._http().request(method, path, json=json, params=params, timeout=timeout_s or self.timeout_s)
        except httpx.HTTPError as exc:
            raise HwBridgeError(f"Hardware bridge unreachable at {self.base_url}: {type(exc).__name__}") from exc
        if r.status_code >= 400:
            detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
            if isinstance(detail, dict):
                raise HwBridgeError(detail.get("message", str(detail)), r.status_code, detail.get("code"))
            raise HwBridgeError(str(detail)[:300], r.status_code)
        return r.json()

    def health(self) -> dict:
        return self._call("GET", "/health", timeout_s=3)

    def controllers(self) -> list[dict]:
        return self._call("GET", "/fc", timeout_s=4)["controllers"]

    def bind(self, fc_id: str, drone_id: str, uin: str, name: str) -> dict:
        return self._call("POST", f"/fc/{fc_id}/bind", {"drone_id": drone_id, "uin": uin, "name": name})

    def unbind(self, fc_id: str) -> dict:
        return self._call("POST", f"/fc/{fc_id}/unbind")

    def prearm(self, fc_id: str) -> dict:
        return self._call("GET", f"/fc/{fc_id}/prearm", timeout_s=25)

    def command(self, fc_id: str, command: str, params: dict | None = None) -> dict:
        return self._call("POST", f"/fc/{fc_id}/command", {"command": command, "params": params or {}}, timeout_s=20)

    def authorize(self, fc_id: str, until_epoch: float, by: str) -> dict:
        return self._call("POST", f"/fc/{fc_id}/authorize", {"until_epoch": until_epoch, "by": by})

    def revoke(self, fc_id: str) -> dict:
        return self._call("DELETE", f"/fc/{fc_id}/authorize")

    def release_override(self, fc_id: str, by: str) -> dict:
        return self._call("POST", f"/fc/{fc_id}/override/release", {"by": by})

    def upload_mission(self, fc_id: str, items: list[dict]) -> dict:
        return self._call("POST", f"/fc/{fc_id}/mission", {"items": items}, timeout_s=40)

    def video_sources(self) -> dict:
        return self._call("GET", "/video/sources", timeout_s=4)

    # ------------------------------------------------ guided setup (Drone Setup page)

    def ports(self) -> dict:
        return self._call("GET", "/ports", timeout_s=6)

    def add_link(self, endpoint: str, baud: int) -> dict:
        return self._call("POST", "/links", {"endpoint": endpoint, "baud": baud})

    def remove_link(self, name: str) -> dict:
        return self._call("DELETE", f"/links/{quote(name, safe='')}")

    def set_camera(self, sysid: int, url: str | None) -> dict:
        if url:
            return self._call("PUT", f"/cameras/{sysid}", {"url": url})
        return self._call("DELETE", f"/cameras/{sysid}")

    def setup(self, fc_id: str, refresh: bool) -> dict:
        return self._call("GET", f"/fc/{fc_id}/setup", params={"refresh": refresh}, timeout_s=20)

    def set_params(self, fc_id: str, values: dict[str, float]) -> dict:
        return self._call("POST", f"/fc/{fc_id}/params", {"values": values}, timeout_s=10 + 4 * len(values))

    def set_sysid(self, fc_id: str, system_id: int) -> dict:
        return self._call("POST", f"/fc/{fc_id}/sysid", {"system_id": system_id}, timeout_s=25)

    def reboot(self, fc_id: str) -> dict:
        return self._call("POST", f"/fc/{fc_id}/reboot", timeout_s=15)

    def calibration(self, fc_id: str, action: str) -> dict:
        return self._call("POST", f"/fc/{fc_id}/calibration/{action}", timeout_s=20)

    def motor_test(self, fc_id: str, motor: int, throttle_pct: float, seconds: float) -> dict:
        return self._call("POST", f"/fc/{fc_id}/motor-test",
                          {"motor": motor, "throttle_pct": throttle_pct, "seconds": seconds, "propellers_removed": True}, timeout_s=15)

    def radio_info(self, port: str) -> dict:
        return self._call("GET", f"/radio/{quote(port, safe='')}", timeout_s=25)

    def radio_netid(self, port: str, netid: int) -> dict:
        return self._call("POST", f"/radio/{quote(port, safe='')}/netid", {"netid": netid}, timeout_s=30)
