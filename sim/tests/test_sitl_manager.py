"""SITL process manager + link emulator tests (run inside the simhost container).

These use a stand-in executable (python sleeping / exiting) through the
manager's `launcher` hook, so lifecycle logic is tested deterministically.
Real PX4 processes are exercised by backend/tests/test_sim_gazebo_integration.py.
"""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from simhost.bridge import LinkEmulator
from simhost.sitl_manager import API_PORT_BASE, SitlError, SitlProcessManager, running_px4_processes


def fake_launcher(behaviour: dict):
    """Launch python instead of PX4; behaviour[index] = 'run' | 'exit:<code>'."""

    def launch(cmd, env, cwd, log_path):
        idx = int(cmd[cmd.index("-i") + 1])
        mode = behaviour.get(idx, "run")
        code = "import time; time.sleep(600)" if mode == "run" else f"import sys, time; time.sleep(0.3); sys.exit({mode.split(':')[1]})"
        # argv contains 'px4' so the orphan scanner recognises it like a real SITL process.
        return subprocess.Popen([sys.executable, "-c", code, "px4-fake"], cwd=cwd, env=env, start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return launch


@pytest.fixture()
def events():
    return []


@pytest.fixture()
def mgr(tmp_path, events):
    m = SitlProcessManager(Path("/opt/PX4-Autopilot/build/px4_sitl_default"), max_instances=4,
                           on_exit=lambda inst, rc, expected: events.append((inst.index, rc, expected)),
                           launcher=fake_launcher({}), startup_timeout_s=60)
    yield m
    m.shutdown()


def wait_for(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_start_one_and_many_with_unique_ports(mgr, tmp_path):
    idx = mgr.allocate_indices("s1", 3)
    assert len(set(idx)) == 3
    insts = [mgr.start("s1", i, f"SIM-{i}", "0,0,0,0,0,0", "w", tmp_path) for i in idx]
    ports = {i.api_port for i in insts}
    assert ports == {API_PORT_BASE + i for i in idx}
    assert all(i.pid for i in insts) and len({i.pid for i in insts}) == 3
    health = mgr.health("s1")
    assert [h["state"] for h in health] == ["starting"] * 3
    mgr.mark_running("s1", idx[0])
    assert mgr.health("s1")[0]["state"] == "running"


def test_capacity_limit(mgr, tmp_path):
    for i in mgr.allocate_indices("s1", 4):
        mgr.start("s1", i, f"SIM-{i}", "0,0,0,0,0,0", "w", tmp_path)
    with pytest.raises(SitlError, match="limit is 4"):
        mgr.allocate_indices("s1", 1)


def test_port_collision_is_detected(mgr, tmp_path):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", API_PORT_BASE + 0))
    try:
        assert 0 not in mgr.allocate_indices("s1", 2), "a bound port must be skipped"
        with pytest.raises(SitlError, match="port collision"):
            mgr.start("s1", 0, "SIM-0", "0,0,0,0,0,0", "w", tmp_path)
    finally:
        sock.close()


def test_index_in_use_by_other_session(mgr, tmp_path):
    mgr.start("s1", 2, "SIM-2", "0,0,0,0,0,0", "w", tmp_path)
    with pytest.raises(SitlError, match="in use"):
        mgr.start("s2", 2, "SIM-2b", "0,0,0,0,0,0", "w", tmp_path)


def test_unexpected_exit_is_a_crash(tmp_path, events):
    m = SitlProcessManager(Path("/x"), 4, on_exit=lambda inst, rc, exp: events.append((inst.index, rc, exp)), launcher=fake_launcher({1: "exit:3"}))
    try:
        m.start("s1", 1, "SIM-1", "0,0,0,0,0,0", "w", tmp_path)
        assert wait_for(lambda: events)
        assert events == [(1, 3, False)]
        assert m.health("s1")[0]["state"] == "crashed"
    finally:
        m.shutdown()


def test_kill_reports_crash_not_expected_exit(mgr, tmp_path, events):
    mgr.start("s1", 0, "SIM-0", "0,0,0,0,0,0", "w", tmp_path)
    mgr.kill("s1", 0)
    assert wait_for(lambda: events)
    idx, rc, expected = events[0]
    assert idx == 0 and rc == -signal.SIGKILL and expected is False
    # A crashed instance is not silently respawned.
    time.sleep(0.6)
    assert mgr.health("s1")[0]["state"] == "crashed" and len(events) == 1


def test_controlled_stop_and_session_cleanup(mgr, tmp_path, events):
    for i in (0, 1):
        mgr.start("s1", i, f"SIM-{i}", "0,0,0,0,0,0", "w", tmp_path)
    pids = [h["pid"] for h in mgr.health("s1")]
    assert mgr.stop_session("s1") == 2
    assert wait_for(lambda: len(events) == 2)
    assert all(expected for _, _, expected in events)
    for pid in pids:
        assert not Path(f"/proc/{pid}").exists() or "zombie" in Path(f"/proc/{pid}/status").read_text().lower()
    mgr.forget_session("s1")
    assert mgr.health("s1") == []


def test_repeated_session_start_stop(mgr, tmp_path):
    for round_ in range(3):
        idx = mgr.allocate_indices(f"s{round_}", 2)
        for i in idx:
            mgr.start(f"s{round_}", i, f"SIM-{i}", "0,0,0,0,0,0", "w", tmp_path)
        assert mgr.stop_session(f"s{round_}") == 2
        mgr.forget_session(f"s{round_}")
    assert mgr.allocate_indices("final", 4) == [0, 1, 2, 3]


def test_startup_timeout(tmp_path, events):
    m = SitlProcessManager(Path("/x"), 4, on_exit=lambda inst, rc, exp: events.append((inst.index, rc, exp)), launcher=fake_launcher({}), startup_timeout_s=0.5)
    try:
        m.start("s1", 0, "SIM-0", "0,0,0,0,0,0", "w", tmp_path)
        assert wait_for(lambda: events, 3)
        assert events[0] == (0, -1, False) and m.health("s1")[0]["state"] == "crashed"
    finally:
        m.shutdown()


def test_orphan_sweep(tmp_path):
    env = {**os.environ, "DM_SIM_SESSION": "ghost"}
    orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)", "px4-fake"], env=env, start_new_session=True)
    try:
        assert any(p["pid"] == orphan.pid for p in _marked())
        m = SitlProcessManager(Path("/x"), 4)
        killed = m.sweep_orphans()
        assert orphan.pid in killed
        orphan.wait(timeout=5)
    finally:
        if orphan.poll() is None:
            orphan.kill()


def _marked():
    out = []
    for e in Path("/proc").glob("[0-9]*"):
        try:
            if b"DM_SIM_SESSION=" in (e / "environ").read_bytes() and b"px4" in (e / "cmdline").read_bytes():
                out.append({"pid": int(e.name)})
        except OSError:
            pass
    return out


def test_running_px4_listing_runs():
    assert isinstance(running_px4_processes(), list)


# ---------------------------------------------------------------- link


def test_link_emulator_is_deterministic():
    def pattern(seed):
        em = LinkEmulator(seed)
        em.configure(loss_pct=30)
        return [em.admit() for _ in range(200)]

    a, b = pattern(7), pattern(7)
    assert a == b and 0.6 < sum(a) / len(a) < 0.8
    assert pattern(8) != a


def test_link_down_drops_everything_and_quality():
    em = LinkEmulator(1)
    em.configure(down=True)
    assert not any(em.admit() for _ in range(50))
    assert em.config.quality_pct() == 0.0
    em.configure(down=False, latency_ms=500, loss_pct=10)
    assert 0 < em.config.quality_pct() < 100


def test_link_latency_delays_delivery():
    em = LinkEmulator(1)
    em.configure(latency_ms=300)
    got = []
    t0 = time.time()
    em.deliver(lambda: got.append(time.time() - t0))
    assert not got
    assert wait_for(lambda: got, 2)
    assert got[0] >= 0.28
