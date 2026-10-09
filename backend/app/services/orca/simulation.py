"""Closed-loop ORCA simulation used by tests, the benchmark and /orca/simulate.

Each 100 ms tick: preferred velocity toward goal -> OrcaEngine.step ->
integrate positions with the safe velocities. Multirotors are modelled as
holonomic (velocity can change within one tick), which is the ORCA model.

Metrics:
* separation: horizontal distance between every pair sharing an altitude band
* collision violation: separation < sum of PHYSICAL radii (actual contact)
* envelope breach:    separation < sum of avoidance radii (radius + safety margin) - 5 cm
* resolution latency: wall time of the full pipeline in the tick where a
  conflict is first predicted -- prediction -> ORCA LP -> safe velocity ->
  integrated (updated) trajectory -- plus the simulated delay until the
  first avoidance velocity is applied (one control period at most).
"""
from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass, field, replace
from typing import Any

from app.services.orca.engine import OrcaEngine
from app.services.orca.models import AgentState, OrcaParams, Vec

ENVELOPE_TOLERANCE_M = 0.05


@dataclass(frozen=True)
class DeadlockPolicy:
    """Guidance-layer fix for ORCA's known symmetric deadlocks.

    If a drone is far from its goal, ORCA-constrained, and nearly stopped for
    `detect_s`, its preferred velocity is rotated `detour_deg` to the RIGHT for
    `detour_s` (give-way-right convention, identical for every drone so the
    group circulates instead of mirroring). ORCA still guarantees safety.
    """

    speed_fraction: float = 0.15
    detect_s: float = 2.0
    detour_s: float = 3.0
    detour_deg: float = 60.0
    min_goal_distance_m: float = 10.0


@dataclass
class SimAgent:
    id: str
    position: Vec
    goal: Vec
    cruise_speed: float = 10.0
    altitude: float = 50.0
    radius: float | None = None
    max_speed: float | None = None
    priority: int = 0
    comm_degraded: bool = False
    velocity: Vec = (0.0, 0.0)
    status: str = "active"
    arrived: bool = False
    distance_flown: float = 0.0
    stuck_since: float | None = None
    detour_until: float = -1.0


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, math.ceil(pct / 100.0 * len(ordered)) - 1))
    return ordered[k]


@dataclass
class SimulationReport:
    steps: int
    sim_time_s: float
    agents: int
    min_separation_m: float
    collision_violations: int
    envelope_breaches: int
    predicted_conflicts: int
    avoidance_events: int
    infeasible_solves: int
    arrived: int
    step_ms: list[float] = field(default_factory=list)
    resolution_ms: list[float] = field(default_factory=list)
    resolution_sim_delay_s: list[float] = field(default_factory=list)
    trajectories: dict[str, list[list[float]]] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "sim_time_s": round(self.sim_time_s, 2),
            "agents": self.agents,
            "min_separation_m": round(self.min_separation_m, 3),
            "collision_violations": self.collision_violations,
            "envelope_breaches": self.envelope_breaches,
            "predicted_conflicts": self.predicted_conflicts,
            "avoidance_events": self.avoidance_events,
            "infeasible_solves": self.infeasible_solves,
            "arrived": self.arrived,
            "step_ms_avg": round(statistics.fmean(self.step_ms), 4) if self.step_ms else 0.0,
            "step_ms_p95": round(percentile(self.step_ms, 95), 4),
            "step_ms_max": round(max(self.step_ms, default=0.0), 4),
            "resolution_ms_avg": round(statistics.fmean(self.resolution_ms), 4) if self.resolution_ms else 0.0,
            "resolution_ms_p95": round(percentile(self.resolution_ms, 95), 4),
            "resolution_ms_max": round(max(self.resolution_ms, default=0.0), 4),
            "resolution_sim_delay_s_max": round(max(self.resolution_sim_delay_s, default=0.0), 3),
        }


class OrcaSimulation:
    def __init__(
        self,
        agents: list[SimAgent],
        params: OrcaParams | None = None,
        arrive_radius_m: float = 1.0,
        record_every: int = 0,
        deadlock: DeadlockPolicy | None = None,
    ):
        self.params = params or OrcaParams()
        self.deadlock = deadlock or DeadlockPolicy()
        self.deadlock_detours = 0
        self.engine = OrcaEngine(self.params)
        self.agents = sorted(agents, key=lambda a: a.id)
        self.arrive_radius_m = arrive_radius_m
        self.record_every = record_every
        self.sim_time = 0.0
        self.steps = 0
        self.report = SimulationReport(0, 0.0, len(agents), math.inf, 0, 0, 0, 0, 0, 0)
        self._index = {a.id: n for n, a in enumerate(self.agents)}
        self._pending_conflicts: dict[tuple[str, str], float] = {}

    def preferred_velocity(self, agent: SimAgent) -> Vec:
        if agent.arrived or agent.status in ("hold", "landed", "offline"):
            return (0.0, 0.0)
        dx, dy = agent.goal[0] - agent.position[0], agent.goal[1] - agent.position[1]
        dist = math.hypot(dx, dy)
        if dist <= self.arrive_radius_m:
            return (0.0, 0.0)
        speed = min(agent.cruise_speed, dist / self.params.timestep_s)
        # Deterministic sub-degree heading bias per agent: breaks the perfect
        # symmetry of e.g. antipodal swaps (standard practice with RVO2).
        bias = math.radians(0.5) * ((self._index[agent.id] % 3) - 1)
        if self.sim_time < agent.detour_until:
            bias -= math.radians(self.deadlock.detour_deg)  # clockwise = to the right
        ux, uy = dx / dist, dy / dist
        return (speed * (ux * math.cos(bias) - uy * math.sin(bias)), speed * (ux * math.sin(bias) + uy * math.cos(bias)))

    def plan_horizon(self, agent: SimAgent) -> float | None:
        if agent.arrived:
            return 0.0
        dist = math.hypot(agent.goal[0] - agent.position[0], agent.goal[1] - agent.position[1])
        return dist / max(agent.cruise_speed, 0.1)

    def _update_deadlock(self, agent: SimAgent, constrained: bool) -> None:
        policy = self.deadlock
        far = math.hypot(agent.goal[0] - agent.position[0], agent.goal[1] - agent.position[1]) > policy.min_goal_distance_m
        slow = math.hypot(*agent.velocity) < policy.speed_fraction * agent.cruise_speed
        if agent.arrived or not far or not slow or not constrained or self.sim_time < agent.detour_until:
            agent.stuck_since = None
            return
        if agent.stuck_since is None:
            agent.stuck_since = self.sim_time
        elif self.sim_time - agent.stuck_since >= policy.detect_s:
            agent.detour_until = self.sim_time + policy.detour_s
            agent.stuck_since = None
            self.deadlock_detours += 1

    def states(self) -> list[AgentState]:
        return [
            AgentState(
                id=a.id,
                position=a.position,
                velocity=a.velocity,
                preferred_velocity=self.preferred_velocity(a),
                altitude=a.altitude,
                radius=a.radius,
                max_speed=a.max_speed,
                status="landed" if a.arrived else a.status,
                priority=a.priority,
                comm_degraded=a.comm_degraded,
                plan_horizon_s=self.plan_horizon(a),
            )
            for a in self.agents
        ]

    def step(self) -> Any:
        dt = self.params.timestep_s
        tick_start = time.perf_counter()
        result = self.engine.step(self.states(), self.sim_time)
        for agent in self.agents:
            decision = result.decisions[agent.id]
            v = decision.safe_velocity if not agent.arrived else (0.0, 0.0)
            agent.velocity = v
            agent.position = (agent.position[0] + v[0] * dt, agent.position[1] + v[1] * dt)
            agent.distance_flown += math.hypot(*v) * dt
            if not agent.arrived and math.hypot(agent.goal[0] - agent.position[0], agent.goal[1] - agent.position[1]) <= self.arrive_radius_m:
                agent.arrived = True
                agent.velocity = (0.0, 0.0)
            if decision.infeasible:
                self.report.infeasible_solves += 1
            self._update_deadlock(agent, bool(decision.neighbors) and math.hypot(*decision.avoidance_vector) > 0.05)
        tick_ms = (time.perf_counter() - tick_start) * 1000  # predict + ORCA + integrate
        self.sim_time += dt
        self.steps += 1

        rep = self.report
        rep.step_ms.append(tick_ms)
        for event in result.events:
            if event.kind == "conflict_predicted":
                rep.predicted_conflicts += 1
                rep.resolution_ms.append(tick_ms)
                pair = tuple(event.drones)
                self._pending_conflicts[pair] = event.sim_time_s  # type: ignore[index]
            elif event.kind == "avoidance_applied":
                rep.avoidance_events += 1
        # Simulated delay from prediction to the first deviation from the plan.
        for pair, predicted_at in list(self._pending_conflicts.items()):
            if any(result.decisions[d].avoidance_vector != (0.0, 0.0) and math.hypot(*result.decisions[d].avoidance_vector) > 0.05 for d in pair):
                rep.resolution_sim_delay_s.append(self.sim_time - dt - predicted_at)
                del self._pending_conflicts[pair]
            elif self.sim_time - predicted_at > self.params.prediction_horizon_s:
                del self._pending_conflicts[pair]  # planned conflict never materialised into a constraint

        engine = self.engine
        for i, a in enumerate(self.agents):
            for b in self.agents[i + 1 :]:
                if abs(a.altitude - b.altitude) >= self.params.altitude_separation_m:
                    continue
                sep = math.hypot(b.position[0] - a.position[0], b.position[1] - a.position[1])
                rep.min_separation_m = min(rep.min_separation_m, sep)
                physical = engine.physical_radius_of(self._as_state(a)) + engine.physical_radius_of(self._as_state(b))
                envelope = engine.radius_of(self._as_state(a)) + engine.radius_of(self._as_state(b))
                if sep < physical:
                    rep.collision_violations += 1
                if sep < envelope - ENVELOPE_TOLERANCE_M:
                    rep.envelope_breaches += 1
        if self.record_every and self.steps % self.record_every == 0:
            for a in self.agents:
                rep.trajectories.setdefault(a.id, []).append([round(a.position[0], 2), round(a.position[1], 2)])
        return result

    @staticmethod
    def _as_state(agent: SimAgent) -> AgentState:
        return AgentState(id=agent.id, position=agent.position, velocity=(0.0, 0.0), preferred_velocity=(0.0, 0.0), radius=agent.radius)

    def run(self, max_time_s: float = 90.0) -> SimulationReport:
        max_steps = int(round(max_time_s / self.params.timestep_s))
        while self.steps < max_steps and not all(a.arrived for a in self.agents):
            self.step()
        rep = self.report
        rep.steps = self.steps
        rep.sim_time_s = self.sim_time
        rep.arrived = sum(1 for a in self.agents if a.arrived)
        if rep.min_separation_m == math.inf:
            rep.min_separation_m = float("nan")
        return rep


def clone_agents(agents: list[SimAgent]) -> list[SimAgent]:
    return [replace(a) for a in agents]
