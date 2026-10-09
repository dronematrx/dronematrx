"""ORCA data model. All positions/velocities are in a local Cartesian frame
(metres east = x, metres north = y); geographic conversion lives in
`frames.py` and nowhere else."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Vec = tuple[float, float]


class InvalidAgentStateError(ValueError):
    """Raised for malformed ORCA input (NaN, negative radius, duplicate ids...)."""


@dataclass(frozen=True)
class OrcaParams:
    """Tunable ORCA parameters (defaults documented in docs/sprint4)."""

    agent_radius_m: float = 1.0  # physical half-size of a multirotor incl. props
    safety_margin_m: float = 2.0  # added to the radius for avoidance (envelope)
    time_horizon_s: float = 5.0  # how far ahead other agents are avoided (tau)
    timestep_s: float = 0.1  # 10 Hz control loop
    max_speed_mps: float = 15.0  # default when an agent has none
    neighbor_radius_m: float = 150.0  # only agents this close constrain each other
    max_neighbors: int = 10
    altitude_separation_m: float = 8.0  # vertically farther apart => no horizontal conflict
    prediction_horizon_s: float = 10.0  # collision-prediction alert window
    degraded_uncertainty_s: float = 1.0  # stale-info inflation for comms-degraded neighbours
    max_uncertainty_m: float = 10.0

    def validate(self) -> None:
        positive = {
            "agent_radius_m": self.agent_radius_m,
            "time_horizon_s": self.time_horizon_s,
            "timestep_s": self.timestep_s,
            "max_speed_mps": self.max_speed_mps,
            "neighbor_radius_m": self.neighbor_radius_m,
            "altitude_separation_m": self.altitude_separation_m,
            "prediction_horizon_s": self.prediction_horizon_s,
        }
        for name, value in positive.items():
            if not value > 0:
                raise InvalidAgentStateError(f"{name} must be > 0")
        if self.safety_margin_m < 0 or self.max_neighbors < 1:
            raise InvalidAgentStateError("safety_margin_m must be >= 0 and max_neighbors >= 1")


# Lower rank = higher priority. Used for responsibility splitting and RTH.
PRIORITY_RANK = {"critical": 0, "emergency": 1, "rth": 2, "degraded": 3, "normal": 4}


@dataclass(frozen=True)
class AgentState:
    """Minimum drone state ORCA needs (derived from live Drone MatrX state)."""

    id: str
    position: Vec
    velocity: Vec
    preferred_velocity: Vec
    altitude: float = 50.0
    radius: float | None = None  # physical radius; None = params.agent_radius_m
    max_speed: float | None = None
    heading: float = 0.0
    status: str = "active"  # active | hold | rth | landed | offline
    priority: int = 0  # higher = more important (yields less)
    comm_degraded: bool = False  # this agent's broadcast info is stale/unreliable
    uin: str | None = None
    # Seconds until the current planned leg ends (goal/waypoint reached). The
    # planned track stops there, so collision prediction must not extend the
    # ray beyond it. None = unknown (unbounded).
    plan_horizon_s: float | None = None


@dataclass(frozen=True)
class OrcaLine:
    """Half-plane constraint: permitted velocities lie to the LEFT of
    `direction` through `point`."""

    point: Vec
    direction: Vec
    neighbor_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"point": list(self.point), "direction": list(self.direction), "neighbor_id": self.neighbor_id}


@dataclass(frozen=True)
class PairPrediction:
    a: str
    b: str
    time_to_collision_s: float  # along planned (preferred) velocities
    distance_m: float
    point: Vec  # predicted intersection (midpoint at TTC)
    current_ttc_s: float | None = None  # along the velocities actually flown

    def key(self) -> tuple[str, str]:
        return (self.a, self.b) if self.a < self.b else (self.b, self.a)


@dataclass
class AgentDecision:
    id: str
    preferred_velocity: Vec
    safe_velocity: Vec
    avoidance_vector: Vec  # safe - preferred
    neighbors: list[str]
    constraints: list[OrcaLine]
    collision_predicted: bool
    infeasible: bool = False  # LP needed the "least violating" fallback (linearProgram3)
    altitude_band: int | None = None


@dataclass
class OrcaEvent:
    seq: int
    kind: str  # conflict_predicted | constraint_generated | avoidance_applied | conflict_cleared | rth_* | band_*
    sim_time_s: float
    wall_time: str
    drones: list[str]
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "sim_time_s": round(self.sim_time_s, 3),
            "wall_time": self.wall_time,
            "drones": self.drones,
            "details": self.details,
        }


@dataclass
class StepResult:
    sim_time_s: float
    decisions: dict[str, AgentDecision]
    predictions: list[PairPrediction]
    events: list[OrcaEvent]
    compute_ms: float
