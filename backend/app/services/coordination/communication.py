"""Communication model used during the consensus phase.

CBBA is decentralised: each agent only reconciles its beliefs with the
agents it can currently talk to. On the edge orchestrator we simulate that
exchange; the topology is pluggable so research runs can model degraded
links (range-limited mesh, partitions) without changing the algorithm.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.services.coordination.models import Agent
from app.services.coordination.utility import distance


class CommunicationModel(ABC):
    @abstractmethod
    def neighbors(self, agents: tuple[Agent, ...]) -> list[list[int]]:
        """Adjacency list: neighbors(agents)[i] = indices agent i hears from."""

    def diameter(self, agents: tuple[Agent, ...]) -> int:
        """Longest shortest-path hop count (BFS). Disconnected graphs return len(agents)."""
        adjacency = self.neighbors(agents)
        n = len(agents)
        worst = 0
        for start in range(n):
            seen = {start: 0}
            frontier = [start]
            while frontier:
                nxt = []
                for node in frontier:
                    for other in adjacency[node]:
                        if other not in seen:
                            seen[other] = seen[node] + 1
                            nxt.append(other)
                frontier = nxt
            if len(seen) < n:
                return n
            worst = max(worst, max(seen.values()))
        return worst


class FullyConnected(CommunicationModel):
    """Every agent hears every other agent each round (edge server relay)."""

    def neighbors(self, agents: tuple[Agent, ...]) -> list[list[int]]:
        n = len(agents)
        return [[k for k in range(n) if k != i] for i in range(n)]


class RangeLimited(CommunicationModel):
    """Agents only talk directly to agents within `range_m` of their home position."""

    def __init__(self, range_m: float):
        self.range_m = range_m

    def neighbors(self, agents: tuple[Agent, ...]) -> list[list[int]]:
        return [
            [k for k, other in enumerate(agents) if k != i and distance(agent.position, other.position) <= self.range_m]
            for i, agent in enumerate(agents)
        ]
