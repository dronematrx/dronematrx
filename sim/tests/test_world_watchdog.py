"""World watchdog (simhost/session.py): Gazebo server death fails the session,
runaway models are removed, and a crashed PX4 gets its motors cut."""
import time

from simhost.session import RUNAWAY_LIMIT_M, SessionSpec, SimSession


class FakeWorld:
    def __init__(self):
        self.rc = None
        self.poses = {"x500_0": (10.0, 5.0, 30.0), "x500_1": (-40.0, 2.0, 28.0)}
        self.removed = []
        self.motors_stopped = []
        self.log_path = "/workspace/s/run/gz_server.log"
        self.stats = {"sim_time_s": 42.0}

    def server_exit_code(self):
        return self.rc

    def model_pose(self, model):
        return self.poses.get(model)

    def remove_entity(self, name):
        self.removed.append(name)
        self.poses.pop(name, None)
        return True

    def stop_motors(self, model, motors=4, repeat_s=1.0):
        self.motors_stopped.append(model)


class FakeLink:
    closed = False
    drone_id = "d"

    def close(self):
        self.closed = True


def make():
    published = []
    spec = SessionSpec(session_id="w", world_sdf="/x.sdf", world_name="w", origin={"latitude": 0, "longitude": 0}, drones=[])
    s = SimSession.__new__(SimSession)
    SimSession.__init__(s, spec, manager=None, workdir="/tmp/w", publish=lambda u, k, p: published.append((u, k, p)))
    s.world = FakeWorld()
    s.links = {"SIM-01": FakeLink(), "SIM-02": FakeLink()}
    s.index_of = {"SIM-01": 0, "SIM-02": 1}
    s.state = "running"
    return s, published


def test_healthy_world_untouched():
    s, published = make()
    s._check_world()
    assert s.state == "running" and s.world.removed == [] and published == []


def test_gazebo_server_death_fails_session_once():
    s, published = make()
    s.world.rc = -6
    s._check_world()
    s._check_world()
    assert s.state == "failed" and "code -6" in s.error
    kinds = [p["kind"] for _, k, p in published]
    assert kinds == ["simulator_failed"]


def test_runaway_model_is_removed():
    s, published = make()
    s.world.poses["x500_1"] = (RUNAWAY_LIMIT_M * 3e12, 1e15, 5e15)  # measured runaway: x = 5.9e16 m
    s._check_world()
    assert s.world.removed == ["x500_1"] and s.state == "running"
    (uin, kind, payload), = published
    assert uin == "SIM-02" and payload["kind"] == "model_removed"


def test_unexpected_px4_exit_cuts_motors():
    s, published = make()
    s.process_exited("SIM-01", -9, expected=False)
    deadline = time.time() + 2
    while not s.world.motors_stopped and time.time() < deadline:
        time.sleep(0.02)
    assert s.world.motors_stopped == ["x500_0"] and s.links["SIM-01"].closed


def test_expected_exit_leaves_motors_alone():
    s, _ = make()
    s.process_exited("SIM-01", 0, expected=True)
    time.sleep(0.2)
    assert s.world.motors_stopped == []
