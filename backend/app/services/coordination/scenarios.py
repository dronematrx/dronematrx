"""Deterministic reference scenarios shared by the test suite and the
benchmark script (`scripts/benchmark_cbba.py`). No randomness: the same
call always yields the same agents and tasks."""
from __future__ import annotations

from dataclasses import replace

from shapely.geometry import Polygon

from app.services.coordination.models import Agent, AllocationProblem, Task
from app.services.coordination.task_generation import CoverageSpec, generate_coverage_tasks

# Irregular pentagon AOI, ~0.6 km^2, in local metres.
BENCHMARK_AOI = Polygon([(-450, -300), (380, -340), (520, 120), (60, 380), (-430, 220)])

# (name, home x, home y, battery %, speed m/s, endurance min, capabilities)
_FLEET = [
    ("DMX-001", -700, -500, 92, 12, 32, ("camera",)),
    ("DMX-002", -650, 450, 85, 10, 28, ("camera", "thermal")),
    ("DMX-003", 700, -450, 78, 14, 30, ("multispectral", "camera")),
    ("DMX-004", 750, 400, 66, 11, 25, ("camera",)),
    ("DMX-005", 0, -650, 95, 13, 35, ("thermal",)),
    ("DMX-006", -800, 0, 58, 9, 22, ("sprayer", "camera")),
    ("DMX-007", 0, 650, 88, 12, 30, ("camera", "multispectral")),
    ("DMX-008", 820, -20, 73, 10, 27, ("thermal", "camera")),
    ("DMX-009", 300, -700, 90, 12, 30, ("camera", "thermal")),  # reserve unit for "add drone" tests
]


def benchmark_agents(count: int = 8, reserve_pct: float = 20.0, max_tasks: int = 12) -> list[Agent]:
    agents = []
    for name, x, y, battery, speed, endurance_min, caps in _FLEET[:count]:
        agents.append(
            Agent(
                id=name,
                name=name,
                position=(float(x), float(y)),
                battery_pct=float(battery),
                speed_mps=float(speed),
                capabilities=frozenset(caps),
                drain_pct_per_s=100.0 / (endurance_min * 60),
                reserve_pct=reserve_pct,
                max_tasks=max_tasks,
            )
        )
    return agents


def benchmark_tasks(count: int) -> list[Task]:
    """`count` coverage segments over BENCHMARK_AOI with a fixed mix of
    sensor requirements and priorities (heterogeneous capabilities)."""
    generated = generate_coverage_tasks(
        BENCHMARK_AOI,
        CoverageSpec(altitude_m=60, overlap_pct=40, sensor="camera", target_task_count=count),
    )
    tasks = []
    for n, task in enumerate(generated.tasks):
        if n % 5 == 4:
            capability = "thermal"
        elif n % 7 == 6:
            capability = "multispectral"
        else:
            capability = "camera"
        priority = 3 if n % 6 == 0 else 1
        tasks.append(replace(task, required_capability=capability, priority=priority))
    return tasks


def benchmark_problem(drones: int = 8, tasks: int = 30) -> AllocationProblem:
    return AllocationProblem(agents=tuple(benchmark_agents(drones)), tasks=tuple(benchmark_tasks(tasks)))
