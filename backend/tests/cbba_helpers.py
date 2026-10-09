"""Shared helpers for CBBA tests: tiny problem builders and an invariant checker."""
from app.services.coordination import Agent, AllocationProblem, AllocationResult, Task


def agent(agent_id: str, x: float = 0.0, y: float = 0.0, *, battery: float = 90.0, speed: float = 10.0,
          caps: tuple[str, ...] = ("camera",), endurance_min: float = 30.0, reserve: float = 20.0,
          max_tasks: int = 10, max_range_m: float | None = None, max_flight_time_s: float | None = None) -> Agent:
    return Agent(
        id=agent_id,
        name=agent_id,
        position=(x, y),
        battery_pct=battery,
        speed_mps=speed,
        capabilities=frozenset(caps),
        drain_pct_per_s=100.0 / (endurance_min * 60),
        reserve_pct=reserve,
        max_tasks=max_tasks,
        max_range_m=max_range_m,
        max_flight_time_s=max_flight_time_s,
    )


def point_task(task_id: str, x: float, y: float, *, cap: str | None = "camera", priority: int = 1, hover: float = 10.0) -> Task:
    return Task(id=task_id, endpoints=((x, y), (x, y)), priority=priority, required_capability=cap, hover_time_s=hover, task_type="inspect")


def problem(agents, tasks) -> AllocationProblem:
    return AllocationProblem(agents=tuple(agents), tasks=tuple(tasks))


def assert_valid_allocation(result: AllocationResult, prob: AllocationProblem) -> None:
    """Invariants every allocation must satisfy regardless of scenario."""
    agents = {a.id: a for a in prob.agents}
    tasks = {t.id: t for t in prob.tasks}

    seen: dict[str, str] = {}
    for agent_id, plan in result.agent_plans.items():
        assert agent_id in agents, f"plan for unknown agent {agent_id}"
        a = agents[agent_id]
        assert len(plan.path) <= a.max_tasks
        assert sorted(s.task_id for s in plan.path) == sorted(plan.bundle)
        for step in plan.path:
            assert step.task_id not in seen, f"task {step.task_id} has two winners ({seen[step.task_id]}, {agent_id})"
            seen[step.task_id] = agent_id
            task = tasks[step.task_id]
            assert task.required_capability is None or task.required_capability in a.capabilities
        if plan.path:
            assert plan.battery_end_pct >= a.reserve_pct - 1e-6, f"{agent_id} violates battery reserve"
            if a.max_range_m is not None:
                assert plan.distance_m <= a.max_range_m + 1e-6
            if a.max_flight_time_s is not None:
                assert plan.duration_s <= a.max_flight_time_s + 1e-6
            etas = [s.eta_s for s in plan.path]
            assert etas == sorted(etas)

    assert seen == result.assignments
    assert set(result.assignments) | set(result.unassigned) == set(tasks)
    assert not set(result.assignments) & set(result.unassigned)
