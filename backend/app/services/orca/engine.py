"""Stateful ORCA engine: one call to `step` = one 100 ms control cycle.

Per cycle, for every drone:
    neighbour detection (horizontal radius + same altitude band)
      -> relative position / velocity
      -> truncated velocity obstacle -> ORCA half-plane per neighbour
      -> 2-D linear program -> safe velocity
and, fleet-wide, time-to-collision prediction along *planned* velocities so
operators get an alert before the conflict (and ORCA) kicks in.

The engine also turns what happens into timeline events:
conflict_predicted -> constraint_generated -> avoidance_applied -> conflict_cleared.
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from datetime import datetime, timezone

from app.core.logging import log_event
from app.services.orca.deconfliction import time_to_collision
from app.services.orca.models import (
    AgentDecision,
    AgentState,
    InvalidAgentStateError,
    OrcaEvent,
    OrcaParams,
    PairPrediction,
    StepResult,
)
from app.services.orca.solver import orca_line, solve

logger = logging.getLogger("dronematrx.orca")

AVOIDANCE_EPSILON_MPS = 0.05


def _finite(*values: float) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


class OrcaEngine:
    def __init__(self, params: OrcaParams | None = None, event_capacity: int = 1000):
        self.params = params or OrcaParams()
        self.params.validate()
        self.events: deque[OrcaEvent] = deque(maxlen=event_capacity)
        self._seq = 0
        self._active_pairs: dict[tuple[str, str], dict] = {}
        self._avoiding: set[str] = set()

    # ------------------------------------------------------------ helpers

    def radius_of(self, agent: AgentState) -> float:
        """Avoidance radius = physical radius + safety margin."""
        physical = agent.radius if agent.radius is not None else self.params.agent_radius_m
        return physical + self.params.safety_margin_m

    def physical_radius_of(self, agent: AgentState) -> float:
        return agent.radius if agent.radius is not None else self.params.agent_radius_m

    def max_speed_of(self, agent: AgentState) -> float:
        return agent.max_speed if agent.max_speed is not None else self.params.max_speed_mps

    def same_band(self, a: AgentState, b: AgentState) -> bool:
        return abs(a.altitude - b.altitude) < self.params.altitude_separation_m

    def validate(self, agents: list[AgentState]) -> None:
        seen: set[str] = set()
        for agent in agents:
            if not agent.id or agent.id in seen:
                raise InvalidAgentStateError(f"Duplicate or empty drone id '{agent.id}'")
            seen.add(agent.id)
            if not _finite(*agent.position, *agent.velocity, *agent.preferred_velocity, agent.altitude):
                raise InvalidAgentStateError(f"Drone {agent.id} has non-finite position/velocity/altitude")
            if agent.radius is not None and not agent.radius > 0:
                raise InvalidAgentStateError(f"Drone {agent.id} radius must be > 0")
            if agent.max_speed is not None and not agent.max_speed > 0:
                raise InvalidAgentStateError(f"Drone {agent.id} max_speed must be > 0")

    def _emit(self, kind: str, sim_time: float, drones: list[str], out: list[OrcaEvent], **details) -> None:
        self._seq += 1
        event = OrcaEvent(
            seq=self._seq,
            kind=kind,
            sim_time_s=sim_time,
            wall_time=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            drones=drones,
            details=details,
        )
        self.events.append(event)
        out.append(event)
        log_event(logger, f"orca.{kind}", logging.INFO if kind != "constraint_generated" else logging.DEBUG, drones="|".join(drones), t=round(sim_time, 2), **{k: v for k, v in details.items() if not isinstance(v, (list, dict))})

    def _responsibility(self, me: AgentState, other: AgentState) -> float:
        if other.comm_degraded:
            return 1.0  # it may never see us: avoid it entirely ourselves
        mine, theirs = 1.0 + max(me.priority, 0), 1.0 + max(other.priority, 0)
        return theirs / (mine + theirs)  # higher-priority drones yield less; shares sum to 1

    # --------------------------------------------------------------- core

    def predict(self, agents: list[AgentState]) -> list[PairPrediction]:
        """Pairs whose planned (preferred-velocity) tracks collide within the prediction horizon."""
        predictions: list[PairPrediction] = []
        horizon = self.params.prediction_horizon_s
        for i, a in enumerate(agents):
            for b in agents[i + 1 :]:
                if not self.same_band(a, b):
                    continue
                combined = self.radius_of(a) + self.radius_of(b)
                ttc = time_to_collision(a.position, a.preferred_velocity, b.position, b.preferred_velocity, combined)
                if ttc is None or ttc > horizon:
                    continue
                # A planned track ends at its goal: a "collision" after both
                # legs are over is an artefact of extending the ray.
                legs = [x.plan_horizon_s for x in (a, b) if x.plan_horizon_s is not None]
                if len(legs) == 2 and ttc > max(legs):
                    continue
                current = time_to_collision(a.position, a.velocity, b.position, b.velocity, combined)
                pa = (a.position[0] + a.preferred_velocity[0] * ttc, a.position[1] + a.preferred_velocity[1] * ttc)
                pb = (b.position[0] + b.preferred_velocity[0] * ttc, b.position[1] + b.preferred_velocity[1] * ttc)
                distance = math.hypot(b.position[0] - a.position[0], b.position[1] - a.position[1])
                predictions.append(
                    PairPrediction(
                        a=a.id,
                        b=b.id,
                        time_to_collision_s=ttc,
                        distance_m=distance,
                        point=((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2),
                        current_ttc_s=current,
                    )
                )
        return predictions

    def compute(self, agent: AgentState, agents: list[AgentState]) -> AgentDecision:
        """Safe velocity for one agent given everybody else's current state."""
        p = self.params
        max_speed = self.max_speed_of(agent)
        if agent.status in ("landed", "offline"):
            zero = (0.0, 0.0)
            return AgentDecision(agent.id, agent.preferred_velocity, zero, (-agent.preferred_velocity[0], -agent.preferred_velocity[1]), [], [], False)

        candidates: list[tuple[float, AgentState]] = []
        for other in agents:
            if other.id == agent.id or not self.same_band(agent, other):
                continue
            dist = math.hypot(other.position[0] - agent.position[0], other.position[1] - agent.position[1])
            if dist <= p.neighbor_radius_m:
                candidates.append((dist, other))
        candidates.sort(key=lambda c: (c[0], c[1].id))
        neighbors = [other for _, other in candidates[: p.max_neighbors]]

        lines = []
        for other in neighbors:
            combined = self.radius_of(agent) + self.radius_of(other)
            if other.comm_degraded:
                speed = math.hypot(*other.velocity)
                combined += min(p.max_uncertainty_m, speed * p.degraded_uncertainty_s)
            other_velocity = (0.0, 0.0) if other.status in ("landed", "offline") else other.velocity
            lines.append(
                orca_line(
                    agent.position,
                    agent.velocity,
                    other.position,
                    other_velocity,
                    combined,
                    p.time_horizon_s,
                    p.timestep_s,
                    responsibility=self._responsibility(agent, other) if other.status not in ("landed", "offline") else 1.0,
                    tie_break=(1.0, 0.0) if agent.id < other.id else (-1.0, 0.0),
                    neighbor_id=other.id,
                )
            )
        preferred = agent.preferred_velocity
        safe, infeasible = solve(lines, preferred, max_speed)
        return AgentDecision(
            id=agent.id,
            preferred_velocity=preferred,
            safe_velocity=safe,
            avoidance_vector=(safe[0] - preferred[0], safe[1] - preferred[1]),
            neighbors=[n.id for n in neighbors],
            constraints=lines,
            collision_predicted=False,
            infeasible=infeasible,
        )

    def step(self, agents: list[AgentState], sim_time: float = 0.0) -> StepResult:
        """One synchronous ORCA cycle over a consistent snapshot of the fleet."""
        started = time.perf_counter()
        self.validate(agents)
        ordered = sorted(agents, key=lambda a: a.id)
        predictions = self.predict(ordered)
        predicted_ids = {x for pr in predictions for x in (pr.a, pr.b)}
        decisions = {a.id: self.compute(a, ordered) for a in ordered}
        for agent_id in predicted_ids:
            decisions[agent_id].collision_predicted = True

        events: list[OrcaEvent] = []
        by_id = {a.id: a for a in ordered}
        current_pairs = {pr.key(): pr for pr in predictions}
        for key, pr in current_pairs.items():
            if key not in self._active_pairs:
                self._active_pairs[key] = {"since": sim_time, "min_sep": pr.distance_m, "constraint": False}
                self._emit(
                    "conflict_predicted", sim_time, list(key), events,
                    time_to_collision_s=round(pr.time_to_collision_s, 2), point=[round(pr.point[0], 2), round(pr.point[1], 2)],
                )
            state = self._active_pairs[key]
            state["min_sep"] = min(state["min_sep"], pr.distance_m)
        for key, state in list(self._active_pairs.items()):
            a, b = by_id.get(key[0]), by_id.get(key[1])
            if a is not None and b is not None:
                state["min_sep"] = min(state["min_sep"], math.hypot(b.position[0] - a.position[0], b.position[1] - a.position[1]))
                if not state["constraint"] and (key[1] in decisions[key[0]].neighbors or key[0] in decisions[key[1]].neighbors):
                    state["constraint"] = True
                    self._emit("constraint_generated", sim_time, list(key), events)
            if key not in current_pairs:
                del self._active_pairs[key]
                self._emit(
                    "conflict_cleared", sim_time, list(key), events,
                    duration_s=round(sim_time - state["since"], 2), min_separation_m=round(state["min_sep"], 2),
                )

        for agent_id, decision in decisions.items():
            deviating = math.hypot(*decision.avoidance_vector) > AVOIDANCE_EPSILON_MPS
            if deviating and agent_id not in self._avoiding:
                self._avoiding.add(agent_id)
                self._emit(
                    "avoidance_applied", sim_time, [agent_id], events,
                    vector=[round(decision.avoidance_vector[0], 2), round(decision.avoidance_vector[1], 2)],
                    neighbors=decision.neighbors,
                )
            elif not deviating and agent_id in self._avoiding:
                self._avoiding.discard(agent_id)
                self._emit("avoidance_released", sim_time, [agent_id], events)

        compute_ms = (time.perf_counter() - started) * 1000
        return StepResult(sim_time_s=sim_time, decisions=decisions, predictions=predictions, events=events, compute_ms=compute_ms)

    def recent_events(self, since_seq: int = 0, limit: int = 200) -> list[OrcaEvent]:
        return [e for e in self.events if e.seq > since_seq][-limit:]
