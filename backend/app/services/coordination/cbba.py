"""Consensus-Based Bundle Algorithm (Choi, Brunet & How, IEEE T-RO 2009).

Each agent i keeps:
    bundle  b_i  tasks in the order it added them
    path    p_i  the same tasks in execution order
    y_i          its belief of the winning bid for every task
    z_i          its belief of the winning agent for every task
    s_i          timestamp of the latest information it holds from each agent
    utilities    the (warped) bid it last computed for every task

and the algorithm alternates two phases until no agent's beliefs change:

  1. Bundle construction -- greedily add the task with the highest marginal
     bid that beats the currently known winning bid (`build_bundles`).
  2. Consensus -- exchange (y, z, s) with neighbours and apply the CBBA
     update / reset / leave decision table (`consensus`), then release every
     task in the bundle after the first one this agent was outbid on
     (`update_bundles`), because their marginal scores depended on it.

Bids are "warped" (Johnson et al. 2012): an agent never bids more for a
later bundle entry than for an earlier one. That restores the diminishing
marginal gain property that path-insertion scores lack, which is what CBBA's
convergence guarantee relies on.

Everything is deterministic: agents and tasks are processed in input order,
ties are broken by lower agent index then lower task index, and there is no
randomness anywhere.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.core.logging import log_event
from app.services.coordination.base import CoordinationAlgorithm, register_algorithm
from app.services.coordination.communication import CommunicationModel, FullyConnected
from app.services.coordination.models import (
    Agent,
    AgentPlan,
    AllocationProblem,
    AllocationResult,
    InvalidProblemError,
    PathStep,
    Task,
)
from app.services.coordination.utility import (
    DefaultUtility,
    UtilityModel,
    distance,
    oriented_endpoints,
    path_totals,
)

logger = logging.getLogger("dronematrx.cbba")

NONE = -1  # "no winner" marker in z vectors
EPS = 1e-9


@dataclass
class AgentState:
    index: int
    agent: Agent
    bundle: list[int] = field(default_factory=list)
    path: list[PathStep] = field(default_factory=list)
    y: list[float] = field(default_factory=list)
    z: list[int] = field(default_factory=list)
    s: list[float] = field(default_factory=list)
    utilities: list[float] = field(default_factory=list)
    own_bids: dict[int, float] = field(default_factory=dict)


def _beats(bid_a: float, winner_a: int, bid_b: float, winner_b: int) -> bool:
    """Does (bid_a by winner_a) beat (bid_b by winner_b)? Ties -> lower agent index."""
    if winner_b == NONE:
        return winner_a != NONE
    if bid_a > bid_b + EPS:
        return True
    return abs(bid_a - bid_b) <= EPS and winner_a != NONE and winner_a < winner_b


class CBBAEngine(CoordinationAlgorithm):
    name = "cbba"

    def __init__(self, utility: UtilityModel | None = None, communication: CommunicationModel | None = None):
        self.utility = utility or DefaultUtility()
        self.communication = communication or FullyConnected()

    # ------------------------------------------------------------------ API

    def allocate(self, problem: AllocationProblem) -> AllocationResult:
        started = time.perf_counter()
        self._validate(problem)
        states = self.initialize(problem)
        return self.run(problem, states, started)

    def reallocate(
        self,
        problem: AllocationProblem,
        previous: AllocationResult,
        removed_agent_ids: Iterable[str] = (),
        added_agents: Iterable[Agent] = (),
    ) -> AllocationResult:
        """Warm start: surviving agents keep their previous bundles (re-checked
        against their current constraints), removed agents' tasks start
        unassigned, new agents start empty. CBBA then only has to resolve
        what actually changed."""
        started = time.perf_counter()
        self._validate(problem)
        removed = set(removed_agent_ids)
        added = {agent.id for agent in added_agents}
        present = {agent.id for agent in problem.agents}
        if removed & present:
            raise InvalidProblemError("Removed agents must not be part of the new problem")

        states = self.initialize(problem)
        tasks = self._tasks_by_id(problem)
        index_of = {task.id: j for j, task in enumerate(problem.tasks)}
        claims: list[tuple[int, int, float]] = []  # (task_idx, agent_idx, bid)

        for state in states:
            previous_plan = previous.agent_plans.get(state.agent.id)
            if previous_plan is None or state.agent.id in added:
                continue
            bid_of = {step.task_id: step.bid for step in previous_plan.path}
            order_of = {step.task_id: n for n, step in enumerate(previous_plan.path)}
            orientation_of = {step.task_id: step.orientation for step in previous_plan.path}
            last_bid = float("inf")
            for task_id in previous_plan.bundle:
                j = index_of.get(task_id)
                if j is None or not self.utility.is_compatible(state.agent, tasks[task_id]):
                    break
                kept = [tasks[problem.tasks[k].id] for k in state.bundle] + [tasks[task_id]]
                trial_path = sorted(
                    [PathStep(task_id=t.id, orientation=orientation_of[t.id]) for t in kept],
                    key=lambda step: order_of[step.task_id],
                )
                totals = path_totals(state.agent, trial_path, tasks)
                if not self.utility.is_feasible(state.agent, totals, len(trial_path)):
                    break  # bundle is a prefix: once infeasible, drop the rest
                bid = min(bid_of.get(task_id, 0.0), last_bid)
                last_bid = bid
                state.bundle.append(j)
                state.own_bids[j] = bid
                state.path = trial_path
                claims.append((j, state.index, bid))

        # Every surviving agent already agreed on the previous allocation.
        for state in states:
            for j, agent_idx, bid in claims:
                state.y[j] = bid
                state.z[j] = agent_idx
        for state in states:
            for step in state.path:
                step.bid = state.own_bids[self._index(problem, step.task_id)]

        result = self.run(problem, states, started)
        moved = sum(
            1
            for task_id, agent_id in result.assignments.items()
            if previous.assignments.get(task_id) not in (None, agent_id)
        )
        result.history.append(
            {
                "event": "reallocation",
                "removed_agents": sorted(removed),
                "added_agents": sorted(added),
                "reused_claims": len(claims),
                "tasks_moved": moved,
            }
        )
        return result

    # ------------------------------------------------------- algorithm steps

    def initialize(self, problem: AllocationProblem) -> list[AgentState]:
        n_tasks = len(problem.tasks)
        n_agents = len(problem.agents)
        return [
            AgentState(
                index=i,
                agent=agent,
                y=[0.0] * n_tasks,
                z=[NONE] * n_tasks,
                s=[0.0] * n_agents,
                utilities=[0.0] * n_tasks,
            )
            for i, agent in enumerate(problem.agents)
        ]

    def calculate_bids(self, state: AgentState, problem: AllocationProblem, tasks: dict[str, Task]) -> dict[int, tuple[float, int, int]]:
        """Warped marginal bids for every task not yet in the agent's bundle.

        Returns {task_idx: (bid, path_position, orientation)} for tasks the
        agent can do at all; records every bid in `state.utilities`.
        """
        context = self.utility.prepare(state.agent, state.path, tasks)
        in_bundle = set(state.bundle)
        cap = state.own_bids[state.bundle[-1]] if state.bundle else float("inf")
        bids: dict[int, tuple[float, int, int]] = {}
        for j, task in enumerate(problem.tasks):
            if j in in_bundle:
                continue
            bid = self.utility.calculate_bid(state.agent, task, state.path, tasks, context)
            if bid is None:
                state.utilities[j] = 0.0
                continue
            value = min(bid.value, cap)
            state.utilities[j] = value
            bids[j] = (value, bid.position, bid.orientation)
        return bids

    def build_bundles(self, states: list[AgentState], problem: AllocationProblem, tasks: dict[str, Task]) -> None:
        """Phase 1: each agent greedily grows its bundle."""
        for state in states:
            while len(state.bundle) < state.agent.max_tasks:
                bids = self.calculate_bids(state, problem, tasks)
                best: tuple[int, float, int, int] | None = None
                for j, (value, position, orientation) in bids.items():
                    # h_ij: would this bid win against what the agent currently believes?
                    if not _beats(value, state.index, state.y[j], state.z[j]):
                        continue
                    if best is None or value > best[1] + EPS:
                        best = (j, value, position, orientation)
                if best is None:
                    break
                j, value, position, orientation = best
                state.bundle.append(j)
                state.path.insert(position, PathStep(task_id=problem.tasks[j].id, orientation=orientation, bid=value))
                state.own_bids[j] = value
                state.y[j] = value
                state.z[j] = state.index

    def consensus(self, states: list[AgentState], neighbors: list[list[int]], round_time: float) -> int:
        """Phase 2: exchange beliefs with neighbours and resolve conflicts.

        Returns the number of messages exchanged this round.
        """
        for state in states:
            state.s[state.index] = round_time
        snapshot = [(list(st.y), list(st.z), list(st.s)) for st in states]
        messages = 0
        for state in states:
            for k in neighbors[state.index]:
                y_k, z_k, s_k = snapshot[k]
                self.resolve_conflicts(state, k, y_k, z_k, s_k)
                for m, stamp in enumerate(s_k):
                    if stamp > state.s[m]:
                        state.s[m] = stamp
                state.s[k] = round_time
                messages += 1
        return messages

    def resolve_conflicts(self, state: AgentState, k: int, y_k: list[float], z_k: list[int], s_k: list[float]) -> None:
        """CBBA decision table (Choi et al. 2009, Table 1) for receiver i, sender k."""
        i = state.index
        s_i = state.s
        for j in range(len(state.y)):
            zk, zi = z_k[j], state.z[j]
            yk, yi = y_k[j], state.y[j]
            action = "leave"
            if zk == k:
                if zi == i:
                    action = "update" if _beats(yk, zk, yi, zi) else "leave"
                elif zi == k or zi == NONE:
                    action = "update"
                else:
                    m = zi
                    action = "update" if (s_k[m] > s_i[m] or _beats(yk, zk, yi, zi)) else "leave"
            elif zk == i:
                if zi == k:
                    action = "reset"
                elif zi not in (i, NONE):
                    m = zi
                    action = "reset" if s_k[m] > s_i[m] else "leave"
            elif zk != NONE:
                m = zk
                if zi == i:
                    action = "update" if (s_k[m] > s_i[m] and _beats(yk, zk, yi, zi)) else "leave"
                elif zi == k:
                    action = "update" if s_k[m] > s_i[m] else "reset"
                elif zi == m or zi == NONE:
                    action = "update" if s_k[m] > s_i[m] else "leave"
                else:
                    n = zi
                    if s_k[m] > s_i[m] and s_k[n] > s_i[n]:
                        action = "update"
                    elif s_k[m] > s_i[m] and _beats(yk, zk, yi, zi):
                        action = "update"
                    elif s_k[n] > s_i[n] and s_i[m] > s_k[m]:
                        action = "reset"
            else:  # sender believes nobody has it
                if zi == k:
                    action = "update"
                elif zi not in (i, NONE):
                    m = zi
                    action = "update" if s_k[m] > s_i[m] else "leave"

            if action == "update":
                state.y[j], state.z[j] = yk, zk
            elif action == "reset":
                state.y[j], state.z[j] = 0.0, NONE

    def update_bundles(self, states: list[AgentState]) -> None:
        """Release everything after the first task this agent lost."""
        for state in states:
            cut = next((n for n, j in enumerate(state.bundle) if state.z[j] != state.index), None)
            if cut is None:
                continue
            for j in state.bundle[cut + 1 :]:
                if state.z[j] == state.index:
                    state.y[j], state.z[j] = 0.0, NONE
            released = set(state.bundle[cut:])
            for j in released:
                state.own_bids.pop(j, None)
            state.bundle = state.bundle[:cut]
            kept_ids = {self._task_ids[j] for j in state.bundle}
            state.path = [step for step in state.path if step.task_id in kept_ids]

    @staticmethod
    def check_convergence(states: list[AgentState], previous_signature: tuple | None) -> tuple[bool, bool, tuple]:
        """Returns (unchanged_since_last_iteration, all_agents_agree, signature)."""
        signature = tuple((tuple(st.bundle), tuple(st.z), tuple(round(v, 9) for v in st.y)) for st in states)
        agree = all(st.z == states[0].z for st in states)
        return signature == previous_signature, agree, signature

    def run(self, problem: AllocationProblem, states: list[AgentState], started: float) -> AllocationResult:
        tasks = self._tasks_by_id(problem)
        self._task_ids = [task.id for task in problem.tasks]
        params = problem.params
        log_event(logger, "cbba.started", agents=len(problem.agents), tasks=len(problem.tasks))

        history: list[dict] = []
        neighbors = self.communication.neighbors(problem.agents)
        diameter = self.communication.diameter(problem.agents) if problem.agents else 0
        converged = timed_out = False
        iterations = messages = 0
        signature: tuple | None = None
        stalled = 0

        if not problem.agents or not problem.tasks:
            converged = True
        while not converged and iterations < params.max_iterations:
            iterations += 1
            self.build_bundles(states, problem, tasks)
            messages += self.consensus(states, neighbors, float(iterations))
            self.update_bundles(states)

            unchanged, agree, signature_now = self.check_convergence(states, signature)
            signature = signature_now
            assigned = sum(1 for z in states[0].z if z != NONE)
            history.append({"iteration": iterations, "assigned": assigned, "agree": agree, "unchanged": unchanged})
            log_event(logger, "cbba.iteration", logging.DEBUG, iteration=iterations, assigned=assigned, agree=agree)

            if unchanged and agree:
                converged = True
                break
            stalled = stalled + 1 if unchanged else 0
            if stalled > diameter + 1:
                break  # beliefs frozen without agreement (e.g. partitioned network)
            if time.perf_counter() - started > params.time_limit_s:
                timed_out = True
                break

        result = self._finalize(problem, states, tasks, converged, timed_out, iterations, messages, history, started)
        log_event(
            logger,
            "cbba.converged" if converged else "cbba.failed",
            logging.INFO if converged else logging.WARNING,
            iterations=iterations,
            assigned=result.assigned_count,
            unassigned=len(result.unassigned),
            timed_out=timed_out,
            elapsed_ms=round(result.convergence_time_s * 1000, 2),
        )
        return result

    # --------------------------------------------------------------- helpers

    def _finalize(
        self,
        problem: AllocationProblem,
        states: list[AgentState],
        tasks: dict[str, Task],
        converged: bool,
        timed_out: bool,
        iterations: int,
        messages: int,
        history: list[dict],
        started: float,
    ) -> AllocationResult:
        # A task is assigned to the agent holding it in its own bundle. If the
        # run stopped before agreement two agents may both hold it: keep the
        # higher bid (lower index on ties) so the output never double-books.
        owner: dict[int, tuple[float, int]] = {}
        for state in states:
            for j in state.bundle:
                claim = (state.own_bids[j], state.index)
                if j not in owner or _beats(claim[0], claim[1], owner[j][0], owner[j][1]):
                    owner[j] = claim

        assignments: dict[str, str] = {}
        winning_bids: dict[str, float] = {}
        plans: dict[str, AgentPlan] = {}
        for state in states:
            agent = state.agent
            path = [step for step in state.path if owner.get(self._index(problem, step.task_id), (0, NONE))[1] == state.index]
            bundle = [problem.tasks[j].id for j in state.bundle if owner.get(j, (0, NONE))[1] == state.index]
            plan = self._evaluate_path(agent, path, bundle, tasks)
            plans[agent.id] = plan
            for step in path:
                assignments[step.task_id] = agent.id
                winning_bids[step.task_id] = step.bid

        unassigned = {task.id: self._unassigned_reason(task, problem.agents, tasks) for task in problem.tasks if task.id not in assignments}

        elapsed = time.perf_counter() - started
        return AllocationResult(
            algorithm=self.name,
            converged=converged,
            timed_out=timed_out,
            iterations=iterations,
            consensus_rounds=iterations,
            messages_exchanged=messages,
            convergence_time_s=elapsed,
            assignments=assignments,
            winning_bids=winning_bids,
            agent_plans=plans,
            unassigned=unassigned,
            total_distance_m=sum(plan.distance_m for plan in plans.values()),
            makespan_s=max((plan.duration_s for plan in plans.values()), default=0.0),
            total_energy_pct=sum(plan.energy_pct for plan in plans.values()),
            history=history,
        )

    def _evaluate_path(self, agent: Agent, path: list[PathStep], bundle: list[str], tasks: dict[str, Task]) -> AgentPlan:
        position = agent.position
        travelled = 0.0
        elapsed = 0.0
        waypoints = [agent.position]
        steps: list[PathStep] = []
        for step in path:
            task = tasks[step.task_id]
            entry, exit_ = oriented_endpoints(task, step.orientation)
            leg = distance(position, entry)
            travelled += leg
            elapsed += leg / agent.speed_mps
            eta = elapsed  # arrival at the start of the task
            travelled += task.service_distance_m
            elapsed += task.service_distance_m / agent.speed_mps + task.hover_time_s
            steps.append(
                PathStep(
                    task_id=step.task_id,
                    orientation=step.orientation,
                    eta_s=eta,
                    bid=step.bid,
                    energy_pct=elapsed * agent.drain_pct_per_s,
                )
            )
            waypoints.append(entry)
            if exit_ != entry:
                waypoints.append(exit_)
            position = exit_
        if path:
            home_leg = distance(position, agent.position)
            travelled += home_leg
            elapsed += home_leg / agent.speed_mps
            waypoints.append(agent.position)
        energy = elapsed * agent.drain_pct_per_s
        return AgentPlan(
            agent_id=agent.id,
            path=steps,
            bundle=bundle,
            distance_m=travelled if path else 0.0,
            duration_s=elapsed if path else 0.0,
            energy_pct=energy if path else 0.0,
            battery_start_pct=agent.battery_pct,
            battery_end_pct=agent.battery_pct - (energy if path else 0.0),
            waypoints=waypoints if path else [],
        )

    def _unassigned_reason(self, task: Task, agents: tuple[Agent, ...], tasks: dict[str, Task]) -> str:
        capable = [agent for agent in agents if self.utility.is_compatible(agent, task)]
        if not agents:
            return "no_drones_available"
        if not capable:
            return f"no_drone_with_{task.required_capability}" if task.required_capability else "no_compatible_drone"
        for agent in capable:
            solo = path_totals(agent, [PathStep(task_id=task.id, orientation=0)], tasks)
            if self.utility.is_feasible(agent, solo, 1):
                return "insufficient_capacity"
        return "exceeds_battery_range_or_flight_time"

    def _validate(self, problem: AllocationProblem) -> None:
        agent_ids = [agent.id for agent in problem.agents]
        task_ids = [task.id for task in problem.tasks]
        if len(set(agent_ids)) != len(agent_ids):
            raise InvalidProblemError("Duplicate agent ids")
        if len(set(task_ids)) != len(task_ids):
            raise InvalidProblemError("Duplicate task ids")
        for agent in problem.agents:
            if agent.speed_mps <= 0:
                raise InvalidProblemError(f"Agent {agent.id} has non-positive speed")
            if not 0 <= agent.battery_pct <= 100:
                raise InvalidProblemError(f"Agent {agent.id} battery out of range")
            if agent.drain_pct_per_s < 0 or agent.max_tasks < 0:
                raise InvalidProblemError(f"Agent {agent.id} has invalid constraints")
        for task in problem.tasks:
            if task.hover_time_s < 0 or task.reward < 0:
                raise InvalidProblemError(f"Task {task.id} has negative hover time or reward")
            for x, y in task.endpoints:
                if x != x or y != y:  # NaN
                    raise InvalidProblemError(f"Task {task.id} has invalid coordinates")
        self._index_map = {task.id: j for j, task in enumerate(problem.tasks)}

    def _index(self, problem: AllocationProblem, task_id: str) -> int:
        return self._index_map[task_id]

    @staticmethod
    def _tasks_by_id(problem: AllocationProblem) -> dict[str, Task]:
        return {task.id: task for task in problem.tasks}


register_algorithm("cbba", CBBAEngine)
