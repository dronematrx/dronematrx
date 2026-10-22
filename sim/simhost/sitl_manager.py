"""PX4 SITL process manager.

One PX4 process per simulated drone, all attached to a Gazebo server that
the session started (PX4_GZ_STANDALONE=1). The manager owns:

* port allocation -- PX4 instance i talks MAVLink to 127.0.0.1:(14540+i)
  (onboard/API link) and 14550 (GCS link, unused here). An index is only
  handed out if its API port is free, so two sessions (or a stale process)
  can never collide;
* process lifecycle -- start, explicit restart, stop (TERM -> KILL);
* health -- a monitor thread polls every process; an unexpected exit is a
  crash, reported once through `on_exit(instance, returncode, expected)`.
  A crashed instance is NOT respawned automatically: the failure history is
  the point (Sprint 5 crash-detection requirement);
* orphan prevention -- every process gets DM_SIM_SESSION=<id> in its
  environment and its own process group; `sweep_orphans` kills any PX4
  process carrying a session marker that the manager no longer tracks.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("simhost.sitl")

API_PORT_BASE = 14540
SITL_GCS_PORT_BASE = 18570
DEFAULT_AIRFRAME = 4001  # gz_x500 quadrotor


class SitlError(Exception):
    pass


@dataclass
class SitlInstance:
    session_id: str
    index: int
    uin: str
    model: str
    pose: str  # "x,y,z,roll,pitch,yaw" in the Gazebo world frame
    workdir: Path
    api_port: int
    process: subprocess.Popen | None = None
    state: str = "pending"  # pending|starting|running|exited|crashed|stopped
    started_at: float | None = None
    exited_at: float | None = None
    returncode: int | None = None
    expected_exit: bool = False
    log_path: Path | None = None
    env_overrides: dict = field(default_factory=dict)

    @property
    def gz_model_name(self) -> str:
        return f"{self.model.removeprefix('gz_')}_{self.index}"

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process else None

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "uin": self.uin,
            "pid": self.pid,
            "state": self.state,
            "api_port": self.api_port,
            "gz_model_name": self.gz_model_name,
            "pose": self.pose,
            "started_at": self.started_at,
            "exited_at": self.exited_at,
            "returncode": self.returncode,
        }


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


class SitlProcessManager:
    def __init__(
        self,
        px4_build: Path,
        max_instances: int,
        on_exit: Callable[[SitlInstance, int, bool], None] | None = None,
        launcher: Callable[[list[str], dict, Path, Path], subprocess.Popen] | None = None,
        startup_timeout_s: float = 60.0,
    ):
        self.px4_build = Path(px4_build)
        self.max_instances = max_instances
        self.on_exit = on_exit
        self._launch = launcher or self._default_launch
        self.startup_timeout_s = startup_timeout_s
        self.instances: dict[tuple[str, int], SitlInstance] = {}
        self._lock = threading.RLock()
        self._monitor: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ allocation

    def allocate_indices(self, session_id: str, count: int) -> list[int]:
        """Lowest free instance indices whose API port is not bound."""
        with self._lock:
            active = sum(1 for i in self.instances.values() if i.state in ("starting", "running"))
            if active + count > self.max_instances:
                raise SitlError(
                    f"Requested {count} SITL instances; {active} already running and the limit is {self.max_instances}"
                )
            used = {idx for (_, idx), inst in self.instances.items() if inst.state in ("pending", "starting", "running")}
            out: list[int] = []
            idx = 0
            while len(out) < count and idx < 255:
                if idx not in used and port_free(API_PORT_BASE + idx):
                    out.append(idx)
                idx += 1
            if len(out) < count:
                raise SitlError("No free MAVLink port range for the requested fleet")
            return out

    # -------------------------------------------------------------- process

    def _default_launch(self, cmd: list[str], env: dict, cwd: Path, log_path: Path) -> subprocess.Popen:
        log = open(log_path, "wb")
        return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)

    def start(
        self,
        session_id: str,
        index: int,
        uin: str,
        pose: str,
        world_name: str,
        workdir_root: Path,
        model: str = "gz_x500",
        extra_env: dict | None = None,
    ) -> SitlInstance:
        key = (session_id, index)
        with self._lock:
            if key in self.instances and self.instances[key].state in ("starting", "running"):
                raise SitlError(f"Instance {index} already running in session {session_id}")
            if any(i.index == index and i.state in ("starting", "running") for i in self.instances.values()):
                raise SitlError(f"Instance index {index} is in use by another session")
            if not port_free(API_PORT_BASE + index):
                raise SitlError(f"MAVLink port {API_PORT_BASE + index} is already bound (port collision)")
            workdir = Path(workdir_root) / f"px4_{index}"
            workdir.mkdir(parents=True, exist_ok=True)
            inst = SitlInstance(session_id, index, uin, model, pose, workdir, API_PORT_BASE + index, log_path=workdir / "px4.log")
            env = {
                **os.environ,
                "PX4_SYS_AUTOSTART": str(DEFAULT_AIRFRAME),
                "PX4_SIM_MODEL": model,
                "PX4_GZ_STANDALONE": "1",
                "PX4_GZ_WORLD": world_name,
                "PX4_GZ_MODEL_POSE": pose,
                "DM_SIM_SESSION": session_id,
                "DM_SIM_UIN": uin,
                **(extra_env or {}),
            }
            inst.env_overrides = {k: v for k, v in env.items() if k.startswith(("PX4_", "DM_"))}
            # stdbuf: line-buffered PX4 output, so px4.log is complete even if
            # the process is killed during start-up.
            cmd = ["stdbuf", "-oL", "-eL", str(self.px4_build / "bin" / "px4"), "-i", str(index), "-d", "-w", str(workdir), str(self.px4_build / "etc")]
            inst.process = self._launch(cmd, env, workdir, inst.log_path)
            inst.state = "starting"
            inst.started_at = time.time()
            self.instances[key] = inst
            logger.info("sitl.started session=%s index=%s uin=%s pid=%s port=%s", session_id, index, uin, inst.pid, inst.api_port)
            self._ensure_monitor()
            return inst

    def mark_running(self, session_id: str, index: int) -> None:
        with self._lock:
            inst = self.instances.get((session_id, index))
            if inst and inst.state == "starting":
                inst.state = "running"

    def stop(self, session_id: str, index: int, timeout_s: float = 8.0) -> None:
        with self._lock:
            inst = self.instances.get((session_id, index))
            if inst is None or inst.process is None:
                return
            inst.expected_exit = True
        self._terminate(inst, timeout_s)
        with self._lock:
            already = inst.state in ("exited", "crashed", "stopped")
            inst.state = "stopped"
            inst.returncode = inst.process.returncode if inst.process else None
            inst.exited_at = inst.exited_at or time.time()
        if not already and self.on_exit:
            # Controlled shutdown is reported too, flagged expected=True, so it
            # is never confused with a crash.
            self.on_exit(inst, inst.returncode if inst.returncode is not None else 0, True)

    def kill(self, session_id: str, index: int) -> SitlInstance:
        """Failure injection: SIGKILL without marking the exit as expected,
        so the monitor reports it exactly like a real crash."""
        with self._lock:
            inst = self.instances.get((session_id, index))
            if inst is None or inst.process is None or inst.process.poll() is not None:
                raise SitlError(f"Instance {index} is not running")
        try:
            os.killpg(inst.process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, AttributeError):
            inst.process.kill()
        return inst

    def restart(self, session_id: str, index: int, world_name: str, workdir_root: Path) -> SitlInstance:
        """Explicit operator restart: a new process; the old crash stays in history."""
        with self._lock:
            old = self.instances.get((session_id, index))
            if old is None:
                raise SitlError(f"Unknown instance {index}")
        if old.process and old.process.poll() is None:
            self.stop(session_id, index)
        del self.instances[(session_id, index)]
        return self.start(session_id, index, old.uin, old.pose, world_name, workdir_root, old.model)

    def stop_session(self, session_id: str) -> int:
        stopped = 0
        for (sid, idx), inst in list(self.instances.items()):
            if sid != session_id:
                continue
            if inst.process and inst.process.poll() is None:
                self.stop(sid, idx)
                stopped += 1
            else:
                inst.state = inst.state if inst.state in ("crashed", "exited") else "stopped"
        return stopped

    def forget_session(self, session_id: str) -> None:
        with self._lock:
            for key in [k for k in self.instances if k[0] == session_id]:
                del self.instances[key]

    def _terminate(self, inst: SitlInstance, timeout_s: float) -> None:
        proc = inst.process
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, AttributeError):
            proc.terminate()
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, AttributeError):
                proc.kill()
            proc.wait(timeout=5)

    # --------------------------------------------------------------- health

    def _ensure_monitor(self) -> None:
        if self._monitor and self._monitor.is_alive():
            return
        self._stop.clear()
        self._monitor = threading.Thread(target=self._monitor_loop, name="sitl-monitor", daemon=True)
        self._monitor.start()

    def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            time.sleep(0.25)

    def poll_once(self) -> list[SitlInstance]:
        """Detect exits; returns instances that exited since the last poll."""
        exited: list[SitlInstance] = []
        with self._lock:
            items = list(self.instances.values())
        for inst in items:
            if inst.process is None or inst.state in ("exited", "crashed", "stopped"):
                if inst.state == "starting" and inst.started_at and time.time() - inst.started_at > self.startup_timeout_s:
                    pass
                continue
            rc = inst.process.poll()
            if rc is None:
                if inst.state == "starting" and time.time() - (inst.started_at or 0) > self.startup_timeout_s:
                    inst.state = "crashed"
                    inst.returncode = None
                    logger.warning("sitl.startup_timeout index=%s", inst.index)
                    exited.append(inst)
                    if self.on_exit:
                        self.on_exit(inst, -1, False)
                continue
            inst.returncode = rc
            inst.exited_at = time.time()
            expected = inst.expected_exit
            inst.state = "exited" if expected else "crashed"
            exited.append(inst)
            logger.log(logging.INFO if expected else logging.WARNING, "sitl.exit index=%s rc=%s expected=%s", inst.index, rc, expected)
            if self.on_exit:
                try:
                    self.on_exit(inst, rc, expected)
                except Exception:  # pragma: no cover - never kill the monitor
                    logger.exception("on_exit handler failed")
        return exited

    def health(self, session_id: str) -> list[dict]:
        with self._lock:
            return [i.as_dict() for (sid, _), i in sorted(self.instances.items()) if sid == session_id]

    def shutdown(self) -> None:
        self._stop.set()
        for (sid, idx) in list(self.instances):
            self.stop(sid, idx)

    # -------------------------------------------------------------- orphans

    def sweep_orphans(self) -> list[int]:
        """Kill PX4 processes that carry a DM_SIM_SESSION marker but are not
        tracked by this manager (e.g. left over from a crashed simhost)."""
        tracked = {i.pid for i in self.instances.values() if i.process and i.process.poll() is None}
        killed: list[int] = []
        for pid in _px4_pids_with_marker():
            if pid in tracked:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except ProcessLookupError:
                pass
        if killed:
            logger.warning("sitl.orphans_killed pids=%s", killed)
        return killed


def _px4_pids_with_marker() -> list[int]:
    pids: list[int] = []
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            environ = (entry / "environ").read_bytes()
            cmdline = (entry / "cmdline").read_bytes()
        except (OSError, PermissionError):
            continue
        if b"DM_SIM_SESSION=" in environ and b"px4" in cmdline:
            pids.append(int(entry.name))
    return pids


def running_px4_processes() -> list[dict]:
    """For orphan inspection: every live PX4 process with its session marker."""
    out = []
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            environ = (entry / "environ").read_bytes()
        except (OSError, PermissionError):
            continue
        if "/bin/px4" in cmdline:
            marker = next((p.split(b"=", 1)[1].decode() for p in environ.split(b"\0") if p.startswith(b"DM_SIM_SESSION=")), None)
            out.append({"pid": int(entry.name), "session": marker, "cmdline": cmdline[:160]})
    return out
