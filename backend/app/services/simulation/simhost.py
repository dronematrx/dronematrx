"""HTTP client for the simulation host (sim/simhost, Docker service `simhost`)."""
from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings


class SimhostError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class SimhostClient:
    def __init__(self, base_url: str | None = None, timeout_s: float = 10.0):
        self.base_url = (base_url or get_settings().SIMHOST_URL).rstrip("/")
        self.timeout_s = timeout_s

    _pools: dict[str, httpx.Client] = {}  # one keep-alive pool per simhost (thread-safe)

    def _http(self) -> httpx.Client:
        pool = SimhostClient._pools.get(self.base_url)
        if pool is None:
            pool = SimhostClient._pools[self.base_url] = httpx.Client(base_url=self.base_url)
        return pool

    def _call(self, method: str, path: str, json: Any = None, timeout_s: float | None = None) -> Any:
        try:
            resp = self._http().request(method, path, json=json, timeout=timeout_s or self.timeout_s)
        except httpx.HTTPError as exc:
            raise SimhostError(f"Simulation host unreachable: {type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = resp.text[:300]
            raise SimhostError(str(detail), resp.status_code)
        return resp.json()

    def health(self) -> dict:
        return self._call("GET", "/health", timeout_s=5)

    def create_session(self, body: dict) -> dict:
        # Blocks until every PX4 instance has a GPS fix and home position.
        return self._call("POST", "/sessions", body, timeout_s=420)

    def status(self, session_id: str) -> dict:
        return self._call("GET", f"/sessions/{session_id}", timeout_s=5)

    def upload_missions(self, session_id: str, missions: dict[str, list[dict]], start: bool = False) -> dict:
        return self._call("POST", f"/sessions/{session_id}/missions", {"missions": missions, "start": start}, timeout_s=300)

    def command(self, session_id: str, uin: str, command: str, params: dict | None = None) -> dict:
        return self._call("POST", f"/sessions/{session_id}/command", {"uin": uin, "command": command, "params": params or {}}, timeout_s=200)

    def pause(self, session_id: str) -> dict:
        return self._call("POST", f"/sessions/{session_id}/pause")

    def resume(self, session_id: str) -> dict:
        return self._call("POST", f"/sessions/{session_id}/resume")

    def speed(self, session_id: str, speed: float) -> dict:
        return self._call("POST", f"/sessions/{session_id}/speed", {"speed": speed})

    def failure(self, session_id: str, ftype: str, uin: str | None, params: dict) -> dict:
        return self._call("POST", f"/sessions/{session_id}/failures", {"type": ftype, "uin": uin, "params": params}, timeout_s=30)

    def stop(self, session_id: str) -> dict:
        return self._call("POST", f"/sessions/{session_id}/stop", timeout_s=60)

    # Sprint 6: SITL bench flight controllers and video sources.
    def flight_controllers(self, session_id: str) -> dict:
        return self._call("GET", f"/sessions/{session_id}/fc", timeout_s=5)

    def bind_fc(self, session_id: str, fc_id: str, drone_id: str, uin: str, name: str) -> dict:
        return self._call("POST", f"/sessions/{session_id}/fc/{fc_id}/bind", {"drone_id": drone_id, "uin": uin, "name": name}, timeout_s=5)

    def unbind_fc(self, session_id: str, fc_id: str) -> dict:
        return self._call("POST", f"/sessions/{session_id}/fc/{fc_id}/unbind", timeout_s=5)

    def prearm(self, session_id: str, fc_id: str) -> dict:
        return self._call("GET", f"/sessions/{session_id}/fc/{fc_id}/prearm", timeout_s=15)

    def video_sources(self) -> dict:
        return self._call("GET", "/video/sources", timeout_s=5)

    def start_test_sources(self, count: int) -> dict:
        return self._call("POST", "/video/test-sources", {"count": count}, timeout_s=20)

    def control_test_source(self, number: int, action: str) -> dict:
        return self._call("POST", f"/video/test-sources/{number}/{action}", timeout_s=10)

    def clock(self) -> dict:
        return self._call("GET", "/clock", timeout_s=3)

    def sessions(self) -> dict:
        return self.health().get("sessions", {})

    def processes(self) -> dict:
        return self._call("GET", "/processes", timeout_s=5)
