"""Pluggable coordination strategy interface.

    CoordinationAlgorithm
        +-- CBBAEngine            (Sprint 3, this package)
        +-- ClusteredCBBA         (future)
        +-- LocalAuction          (future)
        +-- AdaptiveHybrid        (future research algorithm)

The mission planning service only ever talks to this interface and looks
algorithms up by name via `get_algorithm`, so a new strategy is one class
plus one `register_algorithm` call.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable

from app.services.coordination.models import Agent, AllocationProblem, AllocationResult


class CoordinationAlgorithm(ABC):
    name: str = "abstract"

    @abstractmethod
    def allocate(self, problem: AllocationProblem) -> AllocationResult:
        """Allocate every task in `problem` from scratch."""

    @abstractmethod
    def reallocate(
        self,
        problem: AllocationProblem,
        previous: AllocationResult,
        removed_agent_ids: Iterable[str] = (),
        added_agents: Iterable[Agent] = (),
    ) -> AllocationResult:
        """Re-plan after the agent set changed, reusing `previous` where valid.

        `problem` must already describe the *new* agent set (removed agents
        dropped, added agents present); the extra arguments say which agents
        changed so implementations can warm-start everyone else.
        """


_REGISTRY: dict[str, Callable[[], CoordinationAlgorithm]] = {}


def register_algorithm(name: str, factory: Callable[[], CoordinationAlgorithm]) -> None:
    _REGISTRY[name] = factory


def get_algorithm(name: str) -> CoordinationAlgorithm:
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise ValueError(f"Unknown coordination algorithm '{name}'. Available: {sorted(_REGISTRY)}") from None


def available_algorithms() -> list[str]:
    return sorted(_REGISTRY)
