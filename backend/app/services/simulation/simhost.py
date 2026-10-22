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

    def _call(self, method: str, path: str, json: Any = None, timeout_s: float | None = None) -> Any:
        try:
            resp = httpx.request(method, f"{self.base_url}{path}", json=json, timeout=timeout_s or self.timeout_s)
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

    def processes(self) -> dict:
        return self._call("GET", "/processes", timeout_s=5)
