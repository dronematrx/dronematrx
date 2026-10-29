"""HTTP client for the hardware bridge (hwbridge/, real ArduPilot aircraft).

The bridge runs on the operations laptop next to the telemetry radios and
listens on 127.0.0.1 only. Identities look like `serial:COM5/1`
(endpoint / MAVLink system id); the bridge's id for that controller is
`COM5-1` (see hwbridge.links.link_name).
"""
from __future__ import annotations

import re
import threading
from typing import Any

import httpx

from app.core.config import get_settings

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


class HwBridgeClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout_s: float = 12.0):
        s = get_settings()
        self.base_url = (base_url or s.HWBRIDGE_URL).rstrip("/")
        self.token = s.HWBRIDGE_TOKEN if token is None else token
        self.timeout_s = timeout_s
        self._local = threading.local()

    def _http(self) -> httpx.Client:
        c = getattr(self._local, "client", None)
        if c is None:
            c = httpx.Client(base_url=self.base_url, timeout=self.timeout_s, headers={"X-Bridge-Token": self.token} if self.token else {})
            self._local.client = c
        return c

    def _call(self, method: str, path: str, json: Any = None, timeout_s: float | None = None) -> Any:
        try:
            r = self._http().request(method, path, json=json, timeout=timeout_s or self.timeout_s)
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
