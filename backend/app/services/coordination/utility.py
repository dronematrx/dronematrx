"""Bid / utility calculation -- deliberately isolated from the consensus logic.

CBBA only needs one question answered: "if agent i added task j to its
current path, what is its marginal score, and where in the path would it
go?". That is `UtilityModel.calculate_bid`. Swap the model (or subclass
`DefaultUtility`) to experiment with different objectives without touching
`cbba.py`.

Default formulation (per agent path):

    score(path) = sum_j (task_reward_j + priority_bonus_j) * lambda ** arrival_j
                  - travel_cost   (w_distance * metres flown, incl. return home)
                  - energy_cost   (w_energy * battery% used / battery fraction available)

The time discount lambda ** arrival is the time penalty: a task finished
later is worth less, which is what spreads work across the swarm (shorter
mission duration) instead of letting the nearest drone sweep the whole AOI.
Dividing energy by the available battery fraction makes low-SOC drones bid
less. Rewards are scaled to dominate costs, so a task is only ever left
unassigned by a real constraint, not because it is "unprofitable".
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.services.coordination.models import Agent, PathStep, Point, Task


def distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def oriented_endpoints(task: Task, orientation: int) -> tuple[Point, Point]:
    first, second = task.endpoints
    return (first, second) if orientation == 0 else (second, first)


def _orientations(task: Task) -> tuple[int, ...]:
    return (0,) if task.endpoints[0] == task.endpoints[1] else (0, 1)


@dataclass(frozen=True)
class PathTotals:
    distance_m: float
    hover_s: float

    def duration_s(self, agent: Agent) -> float:
        return self.distance_m / agent.speed_mps + self.hover_s

    def energy_pct(self, agent: Agent) -> float:
        return self.duration_s(agent) * agent.drain_pct_per_s


@dataclass(frozen=True)
class Bid:
    value: float
    position: int
    orientation: int


def path_totals(agent: Agent, path: list[PathStep], tasks: dict[str, Task]) -> PathTotals:
    """Home -> each task (flown along its oriented segment) -> home."""
    position = agent.position
    dist = 0.0
    hover = 0.0
    for step in path:
        task = tasks[step.task_id]
        entry, exit_ = oriented_endpoints(task, step.orientation)
        dist += distance(position, entry) + task.service_distance_m
        hover += task.hover_time_s
        position = exit_
    dist += distance(position, agent.position)
    return PathTotals(distance_m=dist, hover_s=hover)


def arrival_times(agent: Agent, path: list[PathStep], tasks: dict[str, Task]) -> list[float]:
    """Time (s from launch) the agent reaches the start of each task."""
    position = agent.position
    elapsed = 0.0
    arrivals = []
    for step in path:
        task = tasks[step.task_id]
        entry, exit_ = oriented_endpoints(task, step.orientation)
        elapsed += distance(position, entry) / agent.speed_mps
        arrivals.append(elapsed)
        elapsed += task.service_distance_m / agent.speed_mps + task.hover_time_s
        position = exit_
    return arrivals


class UtilityModel(ABC):
    """Interface every bid model implements.

    Subclasses must provide `task_value` and `path_score`; the generic
    `calculate_bid` then works by re-scoring every candidate insertion, which
    is simple and correct. Fast models override `prepare`/`calculate_bid`.
    """

    @abstractmethod
    def task_value(self, task: Task) -> float:
        """Reward for completing the task, including any priority bonus."""

    @abstractmethod
    def path_score(self, agent: Agent, path: list[PathStep], tasks: dict[str, Task]) -> float:
        """Total score of executing `path` (launch and return home included)."""

    def is_compatible(self, agent: Agent, task: Task) -> bool:
        return task.required_capability is None or task.required_capability in agent.capabilities

    def is_feasible(self, agent: Agent, totals: PathTotals, n_tasks: int) -> bool:
        if n_tasks > agent.max_tasks:
            return False
        if agent.max_range_m is not None and totals.distance_m > agent.max_range_m + 1e-6:
            return False
        if agent.max_flight_time_s is not None and totals.duration_s(agent) > agent.max_flight_time_s + 1e-6:
            return False
        return agent.battery_pct - totals.energy_pct(agent) >= agent.reserve_pct - 1e-9

    def prepare(self, agent: Agent, path: list[PathStep], tasks: dict[str, Task]) -> Any:
        """Per-path precomputation reused across every task's bid in one bundle step."""
        return self.path_score(agent, path, tasks)

    def calculate_bid(
        self,
        agent: Agent,
        task: Task,
        path: list[PathStep],
        tasks: dict[str, Task],
        context: Any = None,
    ) -> Bid | None:
        """Best marginal score for inserting `task` anywhere in `path`, or None
        if the agent cannot do it (capability, capacity, battery, range,
        flight time) or it adds no value."""
        if not self.is_compatible(agent, task) or len(path) + 1 > agent.max_tasks:
            return None
        base = context if context is not None else self.path_score(agent, path, tasks)
        best: Bid | None = None
        for position in range(len(path) + 1):
            for orientation in _orientations(task):
                candidate = list(path)
                candidate.insert(position, PathStep(task_id=task.id, orientation=orientation))
                if not self.is_feasible(agent, path_totals(agent, candidate, tasks), len(candidate)):
                    continue
                marginal = self.path_score(agent, candidate, tasks) - base
                if best is None or marginal > best.value + 1e-9:
                    best = Bid(value=marginal, position=position, orientation=orientation)
        if best is None or best.value <= 0:
            return None
        return best


@dataclass(frozen=True)
class _PathContext:
    exits: list[Point]  # exits[k] = where the agent is before path[k] (exits[0] = home)
    entries: list[Point]  # entries[k] = entry point of path[k] (entries[-1] = home)
    ready_s: list[float]  # time the agent leaves exits[k]
    suffix_value: list[float]  # sum of discounted rewards of path[k:]
    totals: PathTotals


@dataclass(frozen=True)
class DefaultUtility(UtilityModel):
    reward_scale: float = 1000.0
    priority_bonus: float = 250.0
    w_distance: float = 0.02
    w_energy: float = 1.0
    # Reward half-life: a task reached 15 min after launch is worth half.
    discount_half_life_s: float = 900.0

    @property
    def _lambda(self) -> float:
        return 0.5 ** (1.0 / self.discount_half_life_s)

    def task_value(self, task: Task) -> float:
        return self.reward_scale * task.reward + self.priority_bonus * (max(1, task.priority) - 1)

    def _costs(self, agent: Agent, totals: PathTotals) -> float:
        battery_fraction = max(agent.battery_pct, 1.0) / 100.0
        return self.w_distance * totals.distance_m + self.w_energy * totals.energy_pct(agent) / battery_fraction

    def path_score(self, agent: Agent, path: list[PathStep], tasks: dict[str, Task]) -> float:
        lam = self._lambda
        rewards = sum(
            self.task_value(tasks[step.task_id]) * lam**arrival
            for step, arrival in zip(path, arrival_times(agent, path, tasks))
        )
        return rewards - self._costs(agent, path_totals(agent, path, tasks))

    def prepare(self, agent: Agent, path: list[PathStep], tasks: dict[str, Task]) -> _PathContext:
        lam = self._lambda
        exits: list[Point] = [agent.position]
        entries: list[Point] = []
        ready: list[float] = [0.0]
        values: list[float] = []
        elapsed = 0.0
        position = agent.position
        for step in path:
            task = tasks[step.task_id]
            entry, exit_ = oriented_endpoints(task, step.orientation)
            elapsed += distance(position, entry) / agent.speed_mps
            values.append(self.task_value(task) * lam**elapsed)
            elapsed += task.service_distance_m / agent.speed_mps + task.hover_time_s
            entries.append(entry)
            exits.append(exit_)
            ready.append(elapsed)
            position = exit_
        entries.append(agent.position)
        suffix = [0.0] * (len(path) + 1)
        for k in range(len(path) - 1, -1, -1):
            suffix[k] = suffix[k + 1] + values[k]
        return _PathContext(exits, entries, ready, suffix, path_totals(agent, path, tasks))

    def calculate_bid(
        self,
        agent: Agent,
        task: Task,
        path: list[PathStep],
        tasks: dict[str, Task],
        context: Any = None,
    ) -> Bid | None:
        """O(len(path)) insertion: inserting a task delays every later task by
        the same dt, so their discounted value just scales by lambda ** dt.

        Hot path (called for every task x slot x bundle step), so it is
        written with plain floats: every constraint (battery, range, flight
        time) is monotone in the added distance, so it reduces to one
        `max_extra` bound computed once per call, and the cost difference is
        linear in the added distance. Results equal the generic
        re-scoring in UtilityModel.calculate_bid (tested).
        """
        if not self.is_compatible(agent, task) or len(path) + 1 > agent.max_tasks:
            return None
        ctx: _PathContext = context if isinstance(context, _PathContext) else self.prepare(agent, path, tasks)

        speed = agent.speed_mps
        drain = agent.drain_pct_per_s
        hover = task.hover_time_s
        base_d = ctx.totals.distance_m
        base_h = ctx.totals.hover_s

        # Largest total distance any feasible candidate path may have.
        limit = math.inf
        if agent.max_range_m is not None:
            limit = agent.max_range_m + 1e-6
        if agent.max_flight_time_s is not None:
            limit = min(limit, (agent.max_flight_time_s + 1e-6 - base_h - hover) * speed)
        if drain > 0:
            limit = min(limit, ((agent.battery_pct - agent.reserve_pct + 1e-9) / drain - base_h - hover) * speed)
        max_extra = limit - base_d
        if max_extra < 0:
            return None

        battery_fraction = max(agent.battery_pct, 1.0) / 100.0
        cost_per_m = self.w_distance + self.w_energy * drain / (speed * battery_fraction)
        hover_cost = self.w_energy * drain * hover / battery_fraction
        log_lam = math.log(self._lambda)
        value = self.task_value(task)
        service = task.service_distance_m
        orientations = ((task.endpoints[0], task.endpoints[1], 0),) if task.endpoints[0] == task.endpoints[1] else (
            (task.endpoints[0], task.endpoints[1], 0),
            (task.endpoints[1], task.endpoints[0], 1),
        )
        exits, entries, ready, suffix = ctx.exits, ctx.entries, ctx.ready_s, ctx.suffix_value
        hypot, exp = math.hypot, math.exp

        best_value = -math.inf
        best_position = best_orientation = -1
        for position in range(len(path) + 1):
            px, py = exits[position]
            nx, ny = entries[position]
            base_leg = hypot(nx - px, ny - py)
            ready_s = ready[position]
            later = suffix[position]
            for (ix, iy), (ox, oy), orientation in orientations:
                leg_in = hypot(ix - px, iy - py)
                delta_d = leg_in + service + hypot(nx - ox, ny - oy) - base_leg
                if delta_d > max_extra:
                    continue
                marginal = (
                    value * exp(log_lam * (ready_s + leg_in / speed))
                    + later * (exp(log_lam * (delta_d / speed + hover)) - 1.0)
                    - (cost_per_m * delta_d + hover_cost)
                )
                if marginal > best_value + 1e-9:
                    best_value, best_position, best_orientation = marginal, position, orientation
        if best_position < 0 or best_value <= 0:
            return None
        return Bid(value=best_value, position=best_position, orientation=best_orientation)

