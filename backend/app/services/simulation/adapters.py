"""Flight-controller adapter: the one command interface the orchestrator uses.

    MissionOrchestrator
          |
    FlightControllerAdapter (arm, takeoff, hold, goto, set_mode, rtl, land,
          |                  upload_mission, start_mission, abort)
          +-- SimulationAdapter -> simhost -> PX4 SITL (MAVLink)
          +-- HardwareAdapter   -> real flight controller (locked)

The orchestrator issues abstract commands and never needs to know which
backend is behind the adapter -- but the adapter does, and enforces it:

* SimulationAdapter only accepts simulation missions and simulated drones,
  so a simulation command can never reach real hardware.
* HardwareAdapter refuses everything unless the mission is live, the drone
  is real, and an explicit operator authorization with passed safety checks
  is supplied. No hardware transport is configured in this sprint, so even
  an authorized call reports HARDWARE_UNAVAILABLE rather than pretending.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.drone import Drone
from app.models.mission import Mission
from app.services.simulation.simhost import SimhostClient


class AdapterError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HardwareAuthorization:
    operator_id: str
    safety_checks_passed: bool
    reason: str


class FlightControllerAdapter(ABC):
    backend: str = "abstract"

    @abstractmethod
    def arm(self) -> dict: ...

    @abstractmethod
    def takeoff(self, altitude_m: float) -> dict: ...

    @abstractmethod
    def hold(self) -> dict: ...

    @abstractmethod
    def goto(self, latitude: float, longitude: float, altitude_m: float) -> dict: ...

    @abstractmethod
    def set_mode(self, mode: str) -> dict: ...

    @abstractmethod
    def return_to_launch(self) -> dict: ...

    @abstractmethod
    def land(self) -> dict: ...

    @abstractmethod
    def upload_mission(self, items: list[dict]) -> dict: ...

    @abstractmethod
    def start_mission(self) -> dict: ...

    @abstractmethod
    def abort(self) -> dict: ...


class SimulationAdapter(FlightControllerAdapter):
    backend = "simulation"

    def __init__(self, client: SimhostClient, session_id: str, mission: Mission, drone: Drone):
        if mission.execution_mode != "simulation":
            raise AdapterError("NOT_SIMULATION", "Simulation commands require a simulation mission")
        if not drone.simulated:
            raise AdapterError("REAL_DRONE", f"{drone.name} is a real drone; simulation commands are refused")
        self.client, self.session_id, self.uin = client, session_id, drone.uin

    def _cmd(self, command: str, **params) -> dict:
        return self.client.command(self.session_id, self.uin, command, params)

    def arm(self) -> dict:
        return self._cmd("arm")

    def takeoff(self, altitude_m: float) -> dict:
        return self._cmd("takeoff", altitude_m=altitude_m)

    def hold(self) -> dict:
        return self._cmd("hold")

    def goto(self, latitude: float, longitude: float, altitude_m: float) -> dict:
        return self._cmd("goto", latitude=latitude, longitude=longitude, altitude_m=altitude_m)

    def set_mode(self, mode: str) -> dict:
        return self._cmd("mode", mode=mode)

    def return_to_launch(self) -> dict:
        return self._cmd("rtl")

    def land(self) -> dict:
        return self._cmd("land")

    def upload_mission(self, items: list[dict]) -> dict:
        return self.client.upload_missions(self.session_id, {self.uin: items})

    def start_mission(self) -> dict:
        # simhost arms once PX4's preflight checks pass, then enters AUTO.MISSION.
        return self._cmd("mission_start")

    def abort(self) -> dict:
        return self._cmd("rtl")


class HardwareAdapter(FlightControllerAdapter):
    backend = "hardware"

    def __init__(self, mission: Mission, drone: Drone, authorization: HardwareAuthorization | None = None):
        if mission.execution_mode != "live":
            raise AdapterError("SIMULATION_MISSION", "Simulation missions can never be routed to real hardware")
        if drone.simulated:
            raise AdapterError("SIMULATED_DRONE", f"{drone.name} is a simulation drone")
        self.mission, self.drone, self.authorization = mission, drone, authorization

    def _refuse(self, command: str) -> dict:
        if self.authorization is None:
            raise AdapterError("NOT_AUTHORIZED", f"Real-hardware '{command}' requires explicit operator authorization")
        if not self.authorization.safety_checks_passed:
            raise AdapterError("SAFETY_CHECKS", f"Real-hardware '{command}' blocked: safety checks not passed")
        raise AdapterError("HARDWARE_UNAVAILABLE", "No real flight-controller transport is configured")

    def arm(self) -> dict:
        return self._refuse("arm")

    def takeoff(self, altitude_m: float) -> dict:
        return self._refuse("takeoff")

    def hold(self) -> dict:
        return self._refuse("hold")

    def goto(self, latitude: float, longitude: float, altitude_m: float) -> dict:
        return self._refuse("goto")

    def set_mode(self, mode: str) -> dict:
        return self._refuse("set_mode")

    def return_to_launch(self) -> dict:
        return self._refuse("rtl")

    def land(self) -> dict:
        return self._refuse("land")

    def upload_mission(self, items: list[dict]) -> dict:
        return self._refuse("upload_mission")

    def start_mission(self) -> dict:
        return self._refuse("start_mission")

    def abort(self) -> dict:
        return self._refuse("abort")


def adapter_for(mission: Mission, drone: Drone, client: SimhostClient | None = None, session_id: str | None = None,
                authorization: HardwareAuthorization | None = None) -> FlightControllerAdapter:
    if mission.execution_mode == "simulation":
        if client is None or session_id is None:
            raise AdapterError("NO_SESSION", "Simulation commands need an active simulation session")
        return SimulationAdapter(client, session_id, mission, drone)
    return HardwareAdapter(mission, drone, authorization)
