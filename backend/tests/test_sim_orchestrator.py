"""Sprint 5: orchestrator behaviour on simulator events.

The orchestrator is real; the simulator side is FakeSimhost plus
simhost-shaped NATS messages fed through `on_bus_message` -- the same hook
the FleetRuntime `ui` consumer calls in production. Real Gazebo/PX4 runs are
in test_sim_gazebo_integration.py."""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import Drone, Mission
from app.models.simulation import SimulationEvent, SimulationReport, SimulationSession
from app.services.simulation import orchestrator as orch_mod
from app.services.simulation.orchestrator import SimulationOrchestrator
from tests.planning_support import headers_for
from tests.sim_support import FakeSimhost, plan_sim_mission


@dataclass
class Msg:
    uin: str
    kind: str
    payload: dict


class FakeConnections:
    def __init__(self):
        self.sent: list[dict] = []

    async def broadcast_json(self, payload, organization_id=None):
        self.sent.append(payload)


@dataclass
class FakeRuntime:
    nats_ok: bool = True
    message_listeners: list = field(default_factory=list)


@pytest.fixture()
def env(client, admin_user, db, monkeypatch, tmp_path):
    fake = FakeSimhost()
    monkeypatch.setattr(orch_mod, "SimhostClient", lambda *a, **k: fake)
    monkeypatch.setattr(orch_mod.settings, "SIM_WORKSPACE_DIR", str(tmp_path / "ws"))
    h = headers_for(client, admin_user)

    def make(drones=2, template="survey", flight=None):
        # Small fleets may not cover every lane on one battery: accept that at
        # validation (the report then shows the unassigned tasks).
        mid = plan_sim_mission(client, h, drones=drones, template=template, flight=flight, constraints={"accept_unassigned": True})
        sid = client.post("/simulations", headers=h, json={"mission_id": mid, "dem_source": "flat"}).json()["id"]
        assert client.post(f"/simulations/{sid}/world/generate", headers=h).status_code == 200
        sess = db.get(SimulationSession, uuid.UUID(sid))
        sess.progress = {"preflight": {"passed": True, "checks": []}}
        # Telemetry from the spawned SITL fleet (as the FleetRuntime would persist it).
        for inst in sess.instances:
            d = db.get(Drone, inst.drone_id)
            d.status, d.gps_status, d.last_telemetry_at = "online", "3D_FIX", datetime.now(timezone.utc)
        db.commit()
        orch = SimulationOrchestrator(sessionmaker(bind=db.get_bind()), FakeConnections(), FakeRuntime(), client=fake)
        return orch, uuid.UUID(sid), uuid.UUID(mid)

    return make, fake, admin_user, db


def tel(run, uin, **kw):
    dr = run.drones[uin]
    base = {"session_id": str(run.session_id), "sim_time_s": run.sim_time_s + 1.0, "latitude": dr.start[1], "longitude": dr.start[0],
            "altitude": 40.0, "battery": 90.0, "flight_mode": "MISSION", "armed": True, "landed_state": 2, "reached_max": -1,
            "mission_current": 2, "truth_lat": dr.start[1], "truth_lon": dr.start[0], "truth_z": 40.0}
    return Msg(uin, "telemetry", {**base, **kw})


def exit_seqs(dr):
    return sorted(s for s, v in dr.seq_map.items() if v.get("role") == "exit")


async def _drain(orch, sid, timeout=10.0):
    t0 = time.time()
    while sid in orch.runs and time.time() - t0 < timeout:
        await asyncio.sleep(0.05)
    for _ in range(20):
        await asyncio.sleep(0.01)


def events(db, sid):
    db.expire_all()
    return [e for e in db.execute(select(SimulationEvent).where(SimulationEvent.session_id == sid).order_by(SimulationEvent.created_at)).scalars()]


def test_launch_uploads_cbba_routes_and_activates_mission(env):
    make, fake, user, db = env
    orch, sid, mid = make(drones=2)

    async def go():
        orch._loop = asyncio.get_running_loop()
        await asyncio.to_thread(orch._launch_sync, sid, user.id)

    asyncio.run(go())
    db.expire_all()
    assert db.get(Mission, mid).status == "active"
    sess = db.get(SimulationSession, sid)
    assert sess.state == "running" and sess.spawned_drones == sess.requested_drones == 2
    create = next(c for c in fake.calls if c[0] == "create_session")[1]
    assert [d["uin"] for d in create["drones"]] == [i.uin for i in sess.instances]
    assert all(d["pose"].count(",") == 5 for d in create["drones"])
    uploads = [c for c in fake.calls if c[0] == "upload"]
    assert uploads and all(len(items) > 3 for u in uploads for items in u[2].values())
    starts = [c for c in fake.calls if c[0] == "command" and c[2] == "mission_start"]
    assert len(starts) == sum(1 for i in sess.instances if i.bundle)
    assert {e.event_type for e in events(db, sid)} >= {"sitl_ready", "mission_uploaded", "running"}


def test_task_completion_crash_reallocation_and_report(env):
    make, fake, user, db = env
    orch, sid, mid = make(drones=2)

    async def go():
        orch._loop = asyncio.get_running_loop()
        await asyncio.to_thread(orch._launch_sync, sid, user.id)
        run = orch.runs[sid]
        a, b = sorted(run.drones)
        for u in (a, b):
            await orch.on_bus_message(tel(run, u))
        # Planned != completed: nothing is completed until PX4 reports it.
        assert orch.progress(run)["tasks_completed"] == 0
        first_exit = exit_seqs(run.drones[a])[0]
        await orch.on_bus_message(tel(run, a, reached_max=first_exit))
        assert orch.progress(run)["tasks_completed"] == 1
        b_tasks = [k for k, _ in run.drones[b].path]
        assert b_tasks
        fake.calls.clear()
        # SITL crash of drone b.
        await orch.on_bus_message(Msg(b, "alert", {"type": "sim", "kind": "process_exited", "session_id": str(sid), "returncode": -9, "expected": False}))
        assert run.drones[b].failed
        # b's unfinished tasks were re-allocated by CBBA: each either moved to a
        # or (battery/endurance infeasible for a) is now a failed task.
        a_tasks = {k for k, _ in run.drones[a].path}
        for k in b_tasks:
            assert k in a_tasks or run.tasks[k]["status"] == "failed", k
        assert any(k in a_tasks for k in b_tasks)
        assert any(c[0] == "upload" and a in c[2] for c in fake.calls)
        assert [c[2] for c in fake.calls if c[0] == "command" and c[1] == a][:1] == ["hold"]
        # a completes everything and lands.
        last = exit_seqs(run.drones[a])[-1]
        await orch.on_bus_message(tel(run, a, reached_max=last))
        await orch.on_bus_message(tel(run, a, reached_max=last, armed=False, landed_state=1, battery=61.0, flight_mode="RTL"))
        await _drain(orch, sid)

    asyncio.run(go())
    db.expire_all()
    sess = db.get(SimulationSession, sid)
    assert sess.state == "completed"
    assert db.get(Mission, mid).status == "completed"
    types = [e.event_type for e in events(db, sid)]
    for t in ("task_completed", "process_crashed", "drone_unavailable", "tasks_reallocated", "drone_mission_complete", "mission_completed", "report_generated"):
        assert t in types, (t, types)
    realloc = next(e for e in events(db, sid) if e.event_type == "tasks_reallocated")
    assert realloc.mission_outcome == "replanned" and realloc.details["moved"]
    report = db.execute(select(SimulationReport).where(SimulationReport.session_id == sid)).scalar_one()
    d = report.data
    ts = d["task_summary"]
    assert ts["completed"] + ts["failed"] == ts["assigned"] and ts["completed"] > ts["failed"]
    assert ts["failed"] == len(realloc.details["unassigned"])
    assert d["failure_summary"]["drone_failures"] == 1 and d["failure_summary"]["reallocation_events"] == 1
    assert d["mission_summary"]["drones_requested"] == d["mission_summary"]["drones_spawned"] == 2
    assert report.generation_ms < 30_000
    assert any(c[0] == "stop" for c in fake.calls), "simulator processes must be cleaned up"


def test_all_drones_failed_aborts(env):
    make, fake, user, db = env
    orch, sid, mid = make(drones=1)

    async def go():
        orch._loop = asyncio.get_running_loop()
        await asyncio.to_thread(orch._launch_sync, sid, user.id)
        run = orch.runs[sid]
        (a,) = run.drones
        await orch.on_bus_message(tel(run, a))
        await orch.on_bus_message(Msg(a, "alert", {"kind": "process_exited", "session_id": str(sid), "returncode": -9, "expected": False}))
        await _drain(orch, sid)

    asyncio.run(go())
    db.expire_all()
    assert db.get(SimulationSession, sid).state == "aborted"
    assert db.get(Mission, mid).status == "aborted"
    report = db.execute(select(SimulationReport).where(SimulationReport.session_id == sid)).scalar_one()
    assert report.data["task_summary"]["failed"] > 0


def test_low_battery_commands_rtl_and_reallocates(env):
    make, fake, user, db = env
    orch, sid, mid = make(drones=2)

    async def go():
        orch._loop = asyncio.get_running_loop()
        await asyncio.to_thread(orch._launch_sync, sid, user.id)
        run = orch.runs[sid]
        a, b = sorted(run.drones)
        for u in (a, b):
            await orch.on_bus_message(tel(run, u))
        fake.calls.clear()
        await orch.on_bus_message(tel(run, b, battery=20.0))  # reserve is 25 %
        assert ("command", b, "rtl", {}) in fake.calls
        assert run.drones[b].returning and not run.drones[b].path

    asyncio.run(go())
    types = [e.event_type for e in events(db, sid)]
    assert "battery_low" in types and "tasks_reallocated" in types


def test_comm_loss_and_restore(env, monkeypatch):
    make, fake, user, db = env
    orch, sid, mid = make(drones=1)
    monkeypatch.setattr(orch_mod.settings, "SIM_COMM_LOSS_S", 0.2)

    async def go():
        orch._loop = asyncio.get_running_loop()
        await asyncio.to_thread(orch._launch_sync, sid, user.id)
        run = orch.runs[sid]
        (a,) = run.drones
        await orch.on_bus_message(tel(run, a))
        await asyncio.sleep(0.3)
        await orch._tick(run)
        assert run.drones[a].comm_lost
        await orch.on_bus_message(tel(run, a))
        assert not run.drones[a].comm_lost

    asyncio.run(go())
    types = [e.event_type for e in events(db, sid)]
    assert types.index("comm_lost") < types.index("comm_restored")


def test_failure_controls_and_collision(env):
    make, fake, user, db = env
    orch, sid, mid = make(drones=1)

    async def go():
        orch._loop = asyncio.get_running_loop()
        await asyncio.to_thread(orch._launch_sync, sid, user.id)
        run = orch.runs[sid]
        (a,) = run.drones
        dr = run.drones[a]
        await orch.on_bus_message(tel(run, a))
        await orch.pause(sid, True)
        await orch.pause(sid, False)
        await orch.set_speed(sid, 5.0)
        await orch.inject_failure(sid, "link", dr.drone_id, {"latency_ms": 400, "loss_pct": 30})
        await orch.inject_failure(sid, "gps_drift", dr.drone_id, {"enabled": True})
        await orch.inject_failure(sid, "obstacle", None, {"latitude": dr.start[1], "longitude": dr.start[0]})
        # Gazebo truth inside the obstacle footprint, below its top -> collision event.
        await orch.on_bus_message(tel(run, a, truth_z=10.0))
        assert run.drones[a].collided_with
        with pytest.raises(orch_mod.SimulationError):
            await orch.set_speed(sid, 3.0)

    asyncio.run(go())
    assert [c for c in fake.calls if c[0] in ("pause", "resume", "speed")] == [("pause",), ("resume",), ("speed", 5.0)]
    types = [e.event_type for e in events(db, sid)]
    for t in ("paused", "resumed", "speed_changed", "failure_injected", "comm_degraded", "gps_drift", "obstacle_spawned", "collision"):
        assert t in types, t


def test_report_comparison_flags_incompatible(env, client):
    make, fake, user, db = env
    h = headers_for(client, user)
    reports = []
    for drones, outcome in ((2, "stopped"), (1, "aborted")):
        orch, sid, mid = make(drones=drones)

        async def go(orch=orch, sid=sid, outcome=outcome):
            orch._loop = asyncio.get_running_loop()
            await asyncio.to_thread(orch._launch_sync, sid, user.id)
            await orch.finish(sid, outcome, "test")

        asyncio.run(go())
        reports.append(sid)
    r = client.post(f"/simulations/{reports[0]}/report/compare", headers=h, json={"baseline_session_id": str(reports[1])})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["compatible"] is False and any("fleet size" in w for w in body["warnings"])
    assert any("Different outcome (stopped vs aborted)" in w for w in body["warnings"])
    assert set(body["diff"]) >= {"coverage_pct", "battery_used_total_pct", "simulated_duration_s", "tasks_completed", "tasks_failed", "drone_failures"}


def test_stale_progress_from_previous_mission_is_ignored(env):
    """After a re-plan upload, frames still carrying the old mission's
    progress must not complete tasks of the new mission."""
    make, fake, user, db = env
    orch, sid, mid = make(drones=2)

    async def go():
        orch._loop = asyncio.get_running_loop()
        await asyncio.to_thread(orch._launch_sync, sid, user.id)
        run = orch.runs[sid]
        a, b = sorted(run.drones)
        for u in (a, b):
            await orch.on_bus_message(tel(run, u, mission_gen=1))
        await orch.on_bus_message(Msg(b, "alert", {"kind": "process_exited", "session_id": str(sid), "returncode": -9, "expected": False}))
        assert run.drones[a].mission_gen == 2
        before = orch.progress(run)["tasks_completed"]
        # Old-mission frame with a high reached index: must be ignored.
        await orch.on_bus_message(tel(run, a, mission_gen=1, reached_max=40))
        await orch.on_bus_message(Msg(a, "alert", {"kind": "waypoint_reached", "seq": 40, "mission_gen": 1, "session_id": str(sid)}))
        assert orch.progress(run)["tasks_completed"] == before
        # The current mission's progress is applied.
        first_exit = exit_seqs(run.drones[a])[0]
        await orch.on_bus_message(tel(run, a, mission_gen=2, reached_max=first_exit))
        assert orch.progress(run)["tasks_completed"] == before + 1

    asyncio.run(go())


def test_resume_after_pause_is_not_a_comm_loss(env, monkeypatch):
    make, fake, user, db = env
    orch, sid, mid = make(drones=1)
    monkeypatch.setattr(orch_mod.settings, "SIM_COMM_LOSS_S", 0.3)

    async def go():
        orch._loop = asyncio.get_running_loop()
        await asyncio.to_thread(orch._launch_sync, sid, user.id)
        run = orch.runs[sid]
        (a,) = run.drones
        await orch.on_bus_message(tel(run, a))
        await orch.pause(sid, True)
        await asyncio.sleep(0.5)  # paused: no telemetry, no comm-loss checks
        await orch._tick(run)
        await orch.pause(sid, False)
        await orch._tick(run)  # immediately after resume
        assert not run.drones[a].comm_lost

    asyncio.run(go())
    assert "comm_lost" not in [e.event_type for e in events(db, sid)]


def test_simulator_failure_ends_the_run(env, monkeypatch):
    """Gazebo server death is reported by simhost as a failed session: the run
    ends as failed with the simulator's error, instead of waiting forever."""
    make, fake, user, db = env
    orch, sid, mid = make(drones=1)
    error = "Gazebo server exited (code -6); see /workspace/x/run/gz_server.log"

    async def go():
        orch._loop = asyncio.get_running_loop()
        await asyncio.to_thread(orch._launch_sync, sid, user.id)
        run = orch.runs[sid]
        monkeypatch.setattr(fake, "status", lambda session_id: {"state": "failed", "error": error, "stats": {"sim_time_s": 12.0}, "guardrail": {}})
        await orch._tick(run)
        await orch._tick(run)  # a second tick must not record it twice
        await _drain(orch, sid)

    asyncio.run(go())
    db.expire_all()
    assert db.get(SimulationSession, sid).state == "failed"
    assert db.get(Mission, mid).status == "aborted"
    failed = [e for e in events(db, sid) if e.event_type == "simulator_failed"]
    assert len(failed) == 1 and failed[0].response == error
