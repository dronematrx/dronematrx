"""Test groups B + C -- drone add/remove reallocation and the <2 s
performance acceptance criteria, measured on the real engine."""
import time

import pytest

from app.services.coordination import AllocationProblem, CBBAEngine
from app.services.coordination.scenarios import benchmark_agents, benchmark_tasks
from tests.cbba_helpers import assert_valid_allocation

ACCEPTANCE_SECONDS = 2.0


def _initial(n_tasks: int = 30):
    agents = benchmark_agents(8)
    tasks = benchmark_tasks(n_tasks)
    prob = AllocationProblem(agents=tuple(agents), tasks=tuple(tasks))
    return agents, tasks, prob, CBBAEngine().allocate(prob)


def test_remove_drone_reassigns_its_tasks():
    agents, tasks, _, before = _initial(30)
    removed = "DMX-004"
    orphaned = [s.task_id for s in before.agent_plans[removed].path]
    assert orphaned, "scenario should give the removed drone some work"

    survivors = [a for a in agents if a.id != removed]
    prob = AllocationProblem(agents=tuple(survivors), tasks=tuple(tasks))
    t0 = time.perf_counter()
    after = CBBAEngine().reallocate(prob, before, removed_agent_ids=[removed])
    elapsed = time.perf_counter() - t0

    assert elapsed < ACCEPTANCE_SECONDS
    assert after.converged
    assert removed not in after.agent_plans
    assert removed not in after.assignments.values()
    for task_id in orphaned:
        assert task_id in after.assignments or task_id in after.unassigned
    assert all(task_id in after.assignments for task_id in orphaned)  # feasible here: all reassigned
    assert_valid_allocation(after, prob)
    realloc = after.history[-1]
    assert realloc["event"] == "reallocation" and realloc["removed_agents"] == [removed]


def test_add_drone_redistributes_validly():
    agents, tasks, _, before = _initial(30)
    survivors = [a for a in agents if a.id != "DMX-004"]
    seven = CBBAEngine().reallocate(AllocationProblem(tuple(survivors), tuple(tasks)), before, removed_agent_ids=["DMX-004"])

    newcomer = benchmark_agents(9)[-1]
    assert newcomer.id == "DMX-009"
    prob = AllocationProblem(agents=tuple(survivors + [newcomer]), tasks=tuple(tasks))
    t0 = time.perf_counter()
    after = CBBAEngine().reallocate(prob, seven, added_agents=[newcomer])
    elapsed = time.perf_counter() - t0

    assert elapsed < ACCEPTANCE_SECONDS
    assert after.converged
    assert after.assigned_count == len(tasks)
    assert len(after.agent_plans["DMX-009"].path) > 0, "new drone should pick up work"
    assert_valid_allocation(after, prob)


def test_incremental_and_full_reallocation_are_both_valid():
    agents, tasks, _, before = _initial(30)
    survivors = [a for a in agents if a.id != "DMX-002"]
    prob = AllocationProblem(tuple(survivors), tuple(tasks))
    incremental = CBBAEngine().reallocate(prob, before, removed_agent_ids=["DMX-002"])
    full = CBBAEngine().allocate(prob)
    for result in (incremental, full):
        assert result.converged
        assert_valid_allocation(result, prob)
    assert incremental.assigned_count == full.assigned_count == len(tasks)


@pytest.mark.parametrize("n_tasks", [20, 50])
def test_performance_eight_drone_allocation_under_two_seconds(n_tasks):
    agents = benchmark_agents(8)
    prob = AllocationProblem(tuple(agents), tuple(benchmark_tasks(n_tasks)))
    t0 = time.perf_counter()
    result = CBBAEngine().allocate(prob)
    elapsed = time.perf_counter() - t0

    print(f"\n[benchmark] 8 drones / {n_tasks} tasks: {elapsed * 1000:.1f} ms, "
          f"{result.iterations} iterations, converged={result.converged}, "
          f"assigned={result.assigned_count}, unassigned={len(result.unassigned)}")
    assert result.converged
    assert elapsed < ACCEPTANCE_SECONDS


@pytest.mark.parametrize("n_tasks", [20, 50])
def test_performance_add_and_remove_under_two_seconds(n_tasks):
    agents = benchmark_agents(9)
    eight, newcomer = agents[:8], agents[8]
    tasks = tuple(benchmark_tasks(n_tasks))
    base = CBBAEngine().allocate(AllocationProblem(tuple(eight), tasks))

    t0 = time.perf_counter()
    removed = CBBAEngine().reallocate(AllocationProblem(tuple(eight[:3] + eight[4:]), tasks), base, removed_agent_ids=["DMX-004"])
    remove_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    added = CBBAEngine().reallocate(AllocationProblem(tuple(eight + [newcomer]), tasks), base, added_agents=[newcomer])
    add_s = time.perf_counter() - t0

    print(f"\n[benchmark] {n_tasks} tasks: remove 8->7 {remove_s * 1000:.1f} ms ({removed.iterations} it), "
          f"add 8->9 {add_s * 1000:.1f} ms ({added.iterations} it)")
    assert removed.converged and added.converged
    assert remove_s < ACCEPTANCE_SECONDS
    assert add_s < ACCEPTANCE_SECONDS
