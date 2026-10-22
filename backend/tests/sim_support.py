"""Helpers for Sprint 5 simulation tests.

`FakeSimhost` stands in for the simulation host HTTP API in backend unit /
integration tests. It records every call and returns simhost-shaped
responses; it never claims to be Gazebo or PX4. Tests that need the real
simulator are marked `gazebo` and live in test_sim_gazebo_integration.py.
"""
from __future__ import annotations

import itertools

from tests.planning_support import BASE_LAT, BASE_LON, square

SIM_AOI = square(BASE_LAT, BASE_LON + 0.006, 0.0018)  # ~370 m square east of HQ


class FakeSimhost:
    def __init__(self, max_instances: int = 8, nats: bool = True):
        self.max_instances = max_instances
        self.nats = nats
        self.calls: list[tuple] = []
        self._pid = itertools.count(4000)

    def health(self):
        self.calls.append(("health",))
        return {"capacity": {"max_instances": self.max_instances, "running_instances": 0},
                "versions": {"gazebo": "gz-sim 8.9.0", "px4": "v1.15.4"}, "nats": {"connected": self.nats}}

    def create_session(self, body):
        self.calls.append(("create_session", body))
        return {"instances": [{"uin": d["uin"], "pid": next(self._pid), "api_port": 14540 + i, "gz_model_name": f"x500_{i}", "state": "running"}
                              for i, d in enumerate(body["drones"])]}

    def status(self, session_id):
        return {"stats": {"sim_time_s": 0.0}, "guardrail": {}, "requested_speed": 1.0}

    def upload_missions(self, session_id, missions, start=False):
        self.calls.append(("upload", session_id, missions))
        self.gens = getattr(self, "gens", {})
        for u in missions:
            self.gens[u] = self.gens.get(u, 0) + 1
        return {u: {"items": len(i), "gen": self.gens[u]} for u, i in missions.items()}

    def command(self, session_id, uin, command, params=None):
        self.calls.append(("command", uin, command, params or {}))
        return {"uin": uin, "command": command, "result": 0}

    def pause(self, session_id):
        self.calls.append(("pause",))
        return {"paused": True, "sim_time_s": 12.0}

    def resume(self, session_id):
        self.calls.append(("resume",))
        return {"paused": False, "sim_time_s": 12.0}

    def speed(self, session_id, speed):
        self.calls.append(("speed", speed))
        return {"speed": speed}

    def failure(self, session_id, ftype, uin, params):
        self.calls.append(("failure", ftype, uin, params))
        if ftype == "link":
            return {"link": {"latency_ms": params.get("latency_ms", 0), "loss_pct": params.get("loss_pct", 0), "down": params.get("down", False)}}
        if ftype == "obstacle":
            return {"obstacle": {"name": params["name"], "exists_in_gazebo": True, "gazebo_latlon": [params["latitude"], params["longitude"]]}}
        return {"type": ftype}

    def stop(self, session_id):
        self.calls.append(("stop", session_id))
        return {"sitl_stopped": 0, "leftover_processes": []}

    def processes(self):
        return {"px4": []}


def plan_sim_mission(client, headers, *, template="survey", drones=3, flight=None, constraints=None, aoi=SIM_AOI) -> str:
    resp = client.post("/missions", headers=headers, json={"name": f"Sim {template}", "mission_type": "SURVEY", "execution_mode": "simulation"})
    assert resp.status_code == 201, resp.text
    mid = resp.json()["id"]

    def step(n, data):
        r = client.put(f"/missions/{mid}/wizard/steps/{n}", headers=headers, json={"data": data})
        assert r.status_code == 200, r.text
        return r.json()

    step(1, {"template": template})
    step(2, {"source": "drawn", "geometry": aoi})
    wiz = client.get(f"/missions/{mid}/wizard", headers=headers).json()
    step(3, {**wiz["config"]["flight"], "altitude_m": 40, "speed_mps": 8, **(flight or {})})
    r = client.post(f"/simulations/missions/{mid}/fleet", headers=headers, json={"drone_count": drones})
    assert r.status_code == 200, r.text
    step(5, {"min_battery_pct": 30, "max_tasks_per_drone": 40, **(constraints or {})})
    r = client.post(f"/missions/{mid}/cbba/allocate", headers=headers, json={})
    assert r.status_code == 200, r.text
    return mid
