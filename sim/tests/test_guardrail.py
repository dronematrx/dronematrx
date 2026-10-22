"""FPS guardrail policy (simhost/session.py), driven with a controlled
achieved real-time factor so the step-down sequence is deterministic."""
import time

from simhost.session import SessionSpec, SimSession


class FakeWorld:
    def __init__(self):
        self.rtf = 1.0
        self.stats = {"sim_time_s": 10.0, "paused": False}
        self.factors = []

    def achieved_rtf(self, window_s=3.0):
        return self.rtf

    def set_real_time_factor(self, f):
        self.factors.append(f)
        return True

    def server_exit_code(self):
        return None  # server alive

    def model_pose(self, model):
        return None


def make(speed):
    published = []
    spec = SessionSpec(session_id="g", world_sdf="/x.sdf", world_name="w", origin={"latitude": 0, "longitude": 0}, drones=[], speed=speed)
    s = SimSession.__new__(SimSession)
    SimSession.__init__(s, spec, manager=None, workdir="/tmp/g", publish=lambda u, k, p: published.append((u, k, p)))
    s.world = FakeWorld()
    s.links = {"SIM-01": object()}
    return s, published


def tick(s, n):
    # one iteration of _monitor_loop's body per call (without the 1 s sleep)
    for _ in range(n):
        s._stop.set()
        s._stop.clear()
        fps = s.sim_fps()
        g = s.guardrail
        g["fps"] = fps
        if fps < 10:
            g["low_samples"] += 1
        else:
            g["low_samples"] = 0
        if g["low_samples"] >= 3:
            g["low_samples"] = 0
            prev = s.requested_speed
            lower = max([x for x in (1.0, 2.0, 5.0, 10.0) if x < prev], default=None)
            if lower:
                s.set_speed(lower)


def test_fps_definition():
    s, _ = make(10.0)
    s.world.rtf = 10.0
    assert s.sim_fps() == 30.0
    s.world.rtf = 3.0
    assert abs(s.sim_fps() - 9.0) < 1e-9  # 3x achieved of 10x requested -> 9 FPS (< 10)


def test_guardrail_steps_down_after_three_low_samples():
    s, _ = make(10.0)
    s.world.rtf = 2.5  # 7.5 FPS at 10x
    tick(s, 2)
    assert s.requested_speed == 10.0
    tick(s, 1)
    assert s.requested_speed == 5.0 and s.world.factors == [5.0]
    s.world.rtf = 1.4  # 8.4 FPS at 5x
    tick(s, 3)
    assert s.requested_speed == 2.0


def test_guardrail_quiet_when_fast_enough():
    s, _ = make(10.0)
    s.world.rtf = 4.9  # this machine: 14.7 FPS at 10x
    tick(s, 10)
    assert s.requested_speed == 10.0 and s.world.factors == []


def test_real_monitor_loop_publishes_guardrail_event():
    s, published = make(10.0)
    s.world.rtf = 2.0
    import threading

    th = threading.Thread(target=s._monitor_loop, daemon=True)
    th.start()
    time.sleep(3.6)
    s._stop.set()
    th.join(timeout=3)
    assert s.requested_speed == 5.0
    kinds = [p["kind"] for _, k, p in published if k == "alert"]
    assert "guardrail" in kinds
    ev = s.guardrail["events"][0]
    assert ev["action"] == "speed 10x -> 5x" and ev["fps"] < 10
