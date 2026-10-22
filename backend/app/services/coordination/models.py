"""Algorithm-facing data model for multi-drone task coordination.

Everything in this package works in a local metric frame (x = metres east,
y = metres north of a projection origin) and knows nothing about the
database, HTTP, or the UI. The mission planning service is responsible for
translating ORM rows into these types and back. Keeping this boundary
strict is what lets a different coordination strategy (clustered CBBA,
auctions, future adaptive/hybrid schemes) be dropped in behind the same
`CoordinationAlgorithm` interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Point = tuple[float, float]


@dataclass(frozen=True)
class Agent:
    """A drone as seen by the allocator."""

    id: str
    name: str
    position: Point  # launch/home position in the local metric frame
    battery_pct: float
    speed_mps: float
    capabilities: frozenset[str]
    # Battery percentage consumed per second of flight (derived from endurance).
    drain_pct_per_s: float
    # Battery percentage that must remain on landing back at home.
    reserve_pct: float = 20.0
    max_tasks: int = 12
    max_range_m: float | None = None
    max_flight_time_s: float | None = None


@dataclass(frozen=True)
class Task:
    """A unit of work. Coverage tasks are short lane segments flown from one
    endpoint to the other; point tasks (inspection/hover) have identical
    endpoints and a non-zero hover time."""

    id: str
    endpoints: tuple[Point, Point]
    priority: int = 1  # 1 (routine) .. 5 (critical)
    required_capability: str | None = None
    hover_time_s: float = 0.0
    reward: float = 1.0  # relative weight; the utility model scales it
    task_type: str = "coverage"
    # Flown path length when the task is not a straight line between its
    # endpoints (e.g. a search-and-rescue cell swept with a creeping line).
    path_length_m: float | None = None

    @property
    def service_distance_m(self) -> float:
        if self.path_length_m is not None:
            return self.path_length_m
        (x0, y0), (x1, y1) = self.endpoints
        return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5

    @property
    def location(self) -> Point:
        (x0, y0), (x1, y1) = self.endpoints
        return ((x0 + x1) / 2, (y0 + y1) / 2)


@dataclass(frozen=True)
class AllocationParams:
    max_iterations: int = 200
    # Wall-clock budget; exceeding it returns a non-converged result rather
    # than blocking the API (reported as a timeout).
    time_limit_s: float = 5.0
    bid_epsilon: float = 1e-9


@dataclass(frozen=True)
class AllocationProblem:
    agents: tuple[Agent, ...]
    tasks: tuple[Task, ...]
    params: AllocationParams = field(default_factory=AllocationParams)


@dataclass
class PathStep:
    task_id: str
    # 0 = fly endpoints[0] -> endpoints[1]; 1 = reversed.
    orientation: int
    eta_s: float = 0.0
    bid: float = 0.0
    energy_pct: float = 0.0  # cumulative battery used on arrival-complete of this task


@dataclass
class AgentPlan:
    agent_id: str
    path: list[PathStep]
    bundle: list[str]  # tasks in the order they were added (CBBA bundle)
    distance_m: float = 0.0
    duration_s: float = 0.0
    energy_pct: float = 0.0
    battery_start_pct: float = 0.0
    battery_end_pct: float = 0.0
    waypoints: list[Point] = field(default_factory=list)


@dataclass
class AllocationResult:
    algorithm: str
    converged: bool
    timed_out: bool
    iterations: int
    consensus_rounds: int
    messages_exchanged: int
    convergence_time_s: float
    assignments: dict[str, str]  # task_id -> agent_id
    winning_bids: dict[str, float]  # task_id -> winning bid
    agent_plans: dict[str, AgentPlan]
    unassigned: dict[str, str]  # task_id -> reason
    total_distance_m: float = 0.0
    makespan_s: float = 0.0
    total_energy_pct: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def assigned_count(self) -> int:
        return len(self.assignments)

    def bundle_sizes(self) -> dict[str, int]:
        return {agent_id: len(plan.path) for agent_id, plan in self.agent_plans.items()}

    def average_bid(self) -> float:
        if not self.winning_bids:
            return 0.0
        return sum(self.winning_bids.values()) / len(self.winning_bids)


class InvalidProblemError(ValueError):
    """Raised for malformed allocation input (duplicate ids, bad numbers)."""
