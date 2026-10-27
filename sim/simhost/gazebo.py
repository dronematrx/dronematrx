"""Gazebo Harmonic control for one session world.

Everything here goes through gz-transport (python3-gz-transport13), i.e.
the same services/topics the Gazebo GUI uses:

    /world/<w>/control        gz.msgs.WorldControl   pause / resume / step
    /world/<w>/set_physics    gz.msgs.Physics        real_time_factor (speed)
    /world/<w>/create         gz.msgs.EntityFactory  spawn obstacles
    /world/<w>/wrench         gz.msgs.EntityWrench   wind gust force (ApplyLinkWrench system)
    /world/<w>/stats          gz.msgs.WorldStatistics  sim time, real-time factor, paused
    /world/<w>/dynamic_pose/info  gz.msgs.Pose_V       ground-truth model poses

PX4 runs in lockstep with the Gazebo clock, so pausing or changing the
real-time factor here really pauses / speeds up the flight controllers too.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger("simhost.gazebo")
MODELS_DIR = Path(__file__).resolve().parent / "models"


class GazeboError(Exception):
    pass


def gz_version() -> str:
    try:
        out = subprocess.run(["gz", "sim", "--versions"], capture_output=True, text=True, timeout=20)
        return f"gz-sim {out.stdout.strip().splitlines()[-1]}" if out.stdout.strip() else "unknown"
    except Exception:  # pragma: no cover
        return "unknown"


class GazeboWorld:
    def __init__(self, sdf_path: Path, world_name: str, log_path: Path, headless_rendering: bool = True):
        self.sdf_path = Path(sdf_path)
        self.world_name = world_name
        self.log_path = Path(log_path)
        self.headless_rendering = headless_rendering
        self.process: subprocess.Popen | None = None
        self.gui: subprocess.Popen | None = None
        self._node = None
        self.stats: dict = {}
        self._stats_hist: deque = deque(maxlen=60)  # (wall, sim_time, iterations)
        self.poses: dict[str, tuple[float, float, float, float]] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------- lifecycle

    def start(self, timeout_s: float = 60.0) -> None:
        if not self.sdf_path.exists():
            raise GazeboError(f"World file not found: {self.sdf_path}")
        cmd = ["gz", "sim", "-s", "-r", "-v", "2", str(self.sdf_path)]
        if self.headless_rendering:
            cmd.insert(3, "--headless-rendering")
        log = open(self.log_path, "wb")
        env = os.environ.copy()
        # Drone MatrX models (x500_dm_cam) resolve before PX4's own models.
        env["GZ_SIM_RESOURCE_PATH"] = f"{MODELS_DIR}:{env.get('GZ_SIM_RESOURCE_PATH', '')}"
        self.process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, env=env)
        self._connect()
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise GazeboError(f"gz sim exited with code {self.process.returncode}; see {self.log_path}")
            if self.stats.get("sim_time_s") is not None:
                return
            time.sleep(0.2)
        raise GazeboError("Gazebo world did not start publishing statistics in time")

    def _connect(self) -> None:
        from gz.msgs10.pose_v_pb2 import Pose_V
        from gz.msgs10.world_stats_pb2 import WorldStatistics
        from gz.transport13 import Node

        self._node = Node()
        self._node.subscribe(WorldStatistics, f"/world/{self.world_name}/stats", self._on_stats)
        self._node.subscribe(Pose_V, f"/world/{self.world_name}/dynamic_pose/info", self._on_poses)

    def _on_stats(self, msg) -> None:
        sim_t = msg.sim_time.sec + msg.sim_time.nsec * 1e-9
        real_t = msg.real_time.sec + msg.real_time.nsec * 1e-9
        now = time.time()
        with self._lock:
            self._stats_hist.append((now, sim_t, msg.iterations))
            self.stats = {
                "sim_time_s": sim_t,
                "real_time_s": real_t,
                "iterations": msg.iterations,
                "paused": msg.paused,
                "real_time_factor": msg.real_time_factor,
                "wall": now,
            }

    def _on_poses(self, msg) -> None:
        poses = {}
        for p in msg.pose:
            if p.name:
                poses[p.name] = (p.position.x, p.position.y, p.position.z, p.orientation.x, p.orientation.y, p.orientation.z, p.orientation.w)
        with self._lock:
            self.poses.update(poses)

    def achieved_rtf(self, window_s: float = 3.0) -> float | None:
        """Real-time factor actually achieved over the last `window_s` of wall time."""
        with self._lock:
            hist = list(self._stats_hist)
        if len(hist) < 2:
            return None
        end = hist[-1]
        start = next((h for h in hist if end[0] - h[0] <= window_s), hist[0])
        if end[0] - start[0] <= 0.2:
            return None
        return (end[1] - start[1]) / (end[0] - start[0])

    def stop(self) -> None:
        for proc in (self.gui, self.process):
            if proc and proc.poll() is None:
                try:
                    os.killpg(proc.pid, 15)
                    proc.wait(timeout=10)
                except Exception:
                    try:
                        os.killpg(proc.pid, 9)
                    except Exception:
                        pass

    # ------------------------------------------------------------ services

    def _request(self, service: str, req, req_type, rep_type, timeout_ms: int = 5000):
        if self._node is None:
            raise GazeboError("Gazebo transport not connected")
        # Retries: right after start-up (or while another world shuts down) the
        # first request can time out before the service is fully advertised.
        for attempt in range(4):
            ok, rep = self._node.request(service, req, req_type, rep_type, timeout_ms)
            if ok:
                return rep
            time.sleep(1.0 + attempt)
        raise GazeboError(f"Gazebo service {service} timed out")

    def pause(self, paused: bool) -> bool:
        from gz.msgs10.boolean_pb2 import Boolean
        from gz.msgs10.world_control_pb2 import WorldControl

        req = WorldControl()
        req.pause = paused
        rep = self._request(f"/world/{self.world_name}/control", req, WorldControl, Boolean)
        return bool(rep.data)

    def set_real_time_factor(self, factor: float) -> bool:
        from gz.msgs10.boolean_pb2 import Boolean
        from gz.msgs10.physics_pb2 import Physics

        req = Physics()
        req.real_time_factor = float(factor)
        req.max_step_size = 0.004
        rep = self._request(f"/world/{self.world_name}/set_physics", req, Physics, Boolean)
        return bool(rep.data)

    def create_entity(self, sdf: str, name: str) -> bool:
        from gz.msgs10.boolean_pb2 import Boolean
        from gz.msgs10.entity_factory_pb2 import EntityFactory

        req = EntityFactory()
        req.sdf = sdf
        req.name = name
        req.allow_renaming = False
        rep = self._request(f"/world/{self.world_name}/create", req, EntityFactory, Boolean)
        return bool(rep.data)

    def apply_force(self, model: str, fx: float, fy: float, fz: float = 0.0, persistent: bool = True) -> None:
        """Force on <model>::base_link in world frame (ApplyLinkWrench system).
        persistent=True keeps applying it every step until clear_forces()."""
        from gz.msgs10.entity_pb2 import Entity
        from gz.msgs10.entity_wrench_pb2 import EntityWrench

        msg = EntityWrench()
        msg.entity.name = f"{model}::base_link"
        msg.entity.type = Entity.LINK
        msg.wrench.force.x, msg.wrench.force.y, msg.wrench.force.z = fx, fy, fz
        topic = f"/world/{self.world_name}/wrench" + ("/persistent" if persistent else "")
        pub = self._node.advertise(topic, EntityWrench)
        time.sleep(0.15)  # let discovery connect the publisher
        pub.publish(msg)

    def clear_force(self, model: str) -> None:
        from gz.msgs10.entity_pb2 import Entity

        msg = Entity()
        msg.name = f"{model}::base_link"
        msg.type = Entity.LINK
        pub = self._node.advertise(f"/world/{self.world_name}/wrench/clear", Entity)
        time.sleep(0.15)
        pub.publish(msg)

    def stop_motors(self, model: str, motors: int = 4, repeat_s: float = 1.0) -> None:
        """Zero the motor-speed reference of <model>. The MulticopterMotorModel
        keeps the last command it received, so a drone whose PX4 process died
        would otherwise keep its last thrust and fly off without bound (seen:
        ODE aborting on an out-of-range AABB). Published for `repeat_s` so
        discovery cannot drop it."""
        from gz.msgs10.actuators_pb2 import Actuators

        msg = Actuators()
        msg.velocity.extend([0.0] * motors)
        pub = self._node.advertise(f"/{model}/command/motor_speed", Actuators)
        deadline = time.time() + repeat_s
        while time.time() < deadline:
            pub.publish(msg)
            time.sleep(0.1)

    def remove_entity(self, name: str) -> bool:
        from gz.msgs10.boolean_pb2 import Boolean
        from gz.msgs10.entity_pb2 import Entity

        req = Entity()
        req.name = name
        req.type = Entity.MODEL
        rep = self._request(f"/world/{self.world_name}/remove", req, Entity, Boolean)
        with self._lock:
            self.poses.pop(name, None)
        return bool(rep.data)

    def server_exit_code(self) -> int | None:
        """None while the gz sim server runs; its return code once it has exited."""
        return self.process.poll() if self.process else None

    def subscribe(self, topic: str, msg_type, callback) -> None:
        if self._node is None:
            raise GazeboError("Gazebo transport not connected")
        if not self._node.subscribe(msg_type, topic, callback):
            raise GazeboError(f"Could not subscribe to {topic}")

    def model_yaw(self, model: str) -> float | None:
        """ENU yaw (rad, counter-clockwise from east) from the model quaternion."""
        import math

        with self._lock:
            p = self.poses.get(model)
        if not p or len(p) < 7:
            return None
        qx, qy, qz, qw = p[3:7]
        return math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))

    def model_pose(self, model: str) -> tuple[float, float, float] | None:
        with self._lock:
            p = self.poses.get(model)
        return (p[0], p[1], p[2]) if p else None

    def entity_exists(self, name: str, timeout_s: float = 5.0) -> bool:
        """Check the scene graph (scene/info) for a named model."""
        from gz.msgs10.empty_pb2 import Empty
        from gz.msgs10.scene_pb2 import Scene

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                scene = self._request(f"/world/{self.world_name}/scene/info", Empty(), Empty, Scene, 3000)
                if any(m.name == name for m in scene.model):
                    return True
            except GazeboError:
                pass
            time.sleep(0.3)
        return False

    def entity_pose(self, name: str) -> tuple[float, float, float] | None:
        from gz.msgs10.empty_pb2 import Empty
        from gz.msgs10.scene_pb2 import Scene

        scene = self._request(f"/world/{self.world_name}/scene/info", Empty(), Empty, Scene, 3000)
        for m in scene.model:
            if m.name == name:
                return (m.pose.position.x, m.pose.position.y, m.pose.position.z)
        return None

    # --------------------------------------------------------------- GUI

    def start_gui(self, display: str = ":99", width: int = 1600, height: int = 1000) -> None:
        """Gazebo GUI on a virtual X display (evidence screenshots/recordings)."""
        env = {**os.environ, "DISPLAY": display, "LIBGL_ALWAYS_SOFTWARE": "1", "QT_X11_NO_MITSHM": "1"}
        log = open(self.log_path.with_name("gz_gui.log"), "wb")
        self.gui = subprocess.Popen(["gz", "sim", "-g", "-v", "1"], env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
