"""Test group A -- CBBA algorithm unit tests (no database, no mocks)."""
import pytest

from app.services.coordination import (
    AllocationProblem,
    CBBAEngine,
    DefaultUtility,
    InvalidProblemError,
    PathStep,
    RangeLimited,
    UtilityModel,
    get_algorithm,
)
from app.services.coordination.cbba import NONE, AgentState
from app.services.coordination.scenarios import benchmark_problem
from tests.cbba_helpers import agent, assert_valid_allocation, point_task, problem


def test_single_drone_single_task():
    prob = problem([agent("A")], [point_task("T1", 300, 0)])
    result = CBBAEngine().allocate(prob)

    assert result.converged
    assert result.assignments == {"T1": "A"}
    plan = result.agent_plans["A"]
    assert [s.task_id for s in plan.path] == ["T1"]
    assert plan.path[0].eta_s == pytest.approx(30.0)  # 300 m at 10 m/s
    assert plan.distance_m == pytest.approx(600.0)  # out and back home
    assert 0 < plan.energy_pct < 90
    assert_valid_allocation(result, prob)


def test_multiple_drones_take_their_nearby_tasks():
    agents = [agent("WEST", -2000, 0), agent("EAST", 2000, 0), agent("NORTH", 0, 2000)]
    tasks = [
        point_task("W1", -1900, 50), point_task("W2", -1950, -80),
        point_task("E1", 1900, 30), point_task("E2", 2050, 60),
        point_task("N1", 20, 1950), point_task("N2", -40, 2100),
    ]
    prob = problem(agents, tasks)
    result = CBBAEngine().allocate(prob)

    assert result.converged
    assert {t: a for t, a in result.assignments.items()} == {
        "W1": "WEST", "W2": "WEST", "E1": "EAST", "E2": "EAST", "N1": "NORTH", "N2": "NORTH",
    }
    assert_valid_allocation(result, prob)


@pytest.mark.parametrize("n_tasks", [20, 50])
def test_eight_drones_full_scenario(n_tasks):
    prob = benchmark_problem(drones=8, tasks=n_tasks)
    assert len(prob.agents) == 8 and len(prob.tasks) == n_tasks

    result = CBBAEngine().allocate(prob)

    assert result.converged
    assert result.assigned_count == n_tasks
    assert not result.unassigned
    # Work is genuinely shared across the swarm, not swept by one drone.
    assert sum(1 for size in result.bundle_sizes().values() if size > 0) >= 6
    assert_valid_allocation(result, prob)


def test_capability_mismatch_leaves_task_unassigned():
    prob = problem([agent("CAM", caps=("camera",))], [point_task("HOT", 100, 0, cap="thermal")])
    result = CBBAEngine().allocate(prob)

    assert result.assignments == {}
    assert result.unassigned == {"HOT": "no_drone_with_thermal"}
    assert_valid_allocation(result, prob)


def test_task_requirement_routes_to_capable_drone_even_if_farther():
    agents = [agent("CAM", 0, 0, caps=("camera",)), agent("THERM", 3000, 0, caps=("thermal",))]
    prob = problem(agents, [point_task("HOT", 100, 0, cap="thermal"), point_task("PHOTO", 120, 0, cap="camera")])
    result = CBBAEngine().allocate(prob)

    assert result.assignments == {"HOT": "THERM", "PHOTO": "CAM"}
    assert_valid_allocation(result, prob)


def test_battery_constraint_blocks_far_task():
    # 25% battery, 20% reserve, 30-min endurance -> ~90 s of flight available.
    weak = agent("WEAK", 0, 0, battery=25)
    strong = agent("STRONG", -500, 0, battery=95)
    far = point_task("FAR", 1500, 0)
    result = CBBAEngine().allocate(problem([weak, strong], [far]))
    assert result.assignments == {"FAR": "STRONG"}

    alone = CBBAEngine().allocate(problem([weak], [far]))
    assert alone.unassigned == {"FAR": "exceeds_battery_range_or_flight_time"}
    assert_valid_allocation(alone, problem([weak], [far]))


def test_max_range_constraint():
    short = agent("SHORT", 0, 0, max_range_m=1000)
    prob = problem([short], [point_task("NEAR", 300, 0), point_task("FAR", 800, 0)])
    result = CBBAEngine().allocate(prob)

    assert result.assignments == {"NEAR": "SHORT"}
    assert "FAR" in result.unassigned
    assert result.agent_plans["SHORT"].distance_m <= 1000
    assert_valid_allocation(result, prob)


def test_max_flight_time_constraint():
    a = agent("A", 0, 0, max_flight_time_s=100)
    prob = problem([a], [point_task("T1", 200, 0, hover=10), point_task("T2", -900, 0, hover=10)])
    result = CBBAEngine().allocate(prob)

    assert result.assignments == {"T1": "A"}
    assert result.agent_plans["A"].duration_s <= 100
    assert_valid_allocation(result, prob)


def test_priority_wins_when_capacity_is_limited():
    a = agent("A", 0, 0, max_tasks=1)
    prob = problem([a], [point_task("ROUTINE", 100, 0, priority=1), point_task("URGENT", 400, 0, priority=5)])
    result = CBBAEngine().allocate(prob)

    assert result.assignments == {"URGENT": "A"}
    assert result.unassigned == {"ROUTINE": "insufficient_capacity"}


def test_duplicate_winner_conflict_resolved_deterministically():
    # Two identical drones bid exactly the same for one task: lower index wins.
    prob = problem([agent("A", 0, 0), agent("B", 0, 0)], [point_task("T1", 500, 0)])
    result = CBBAEngine().allocate(prob)

    assert result.converged
    assert result.assignments == {"T1": "A"}
    assert result.agent_plans["B"].path == []
    assert_valid_allocation(result, prob)


def _state(index, n_tasks=1, n_agents=3):
    return AgentState(index=index, agent=agent(f"A{index}"), y=[0.0] * n_tasks, z=[NONE] * n_tasks, s=[0.0] * n_agents)


def test_consensus_rules_update_reset_leave():
    engine = CBBAEngine()

    # Sender k=1 claims the task with a higher bid than receiver i=0's own claim -> update.
    receiver = _state(0)
    receiver.y, receiver.z = [5.0], [0]
    engine.resolve_conflicts(receiver, 1, [7.0], [1], [0.0, 1.0, 0.0])
    assert (receiver.y, receiver.z) == ([7.0], [1])

    # Lower competing bid -> leave.
    receiver = _state(0)
    receiver.y, receiver.z = [9.0], [0]
    engine.resolve_conflicts(receiver, 1, [7.0], [1], [0.0, 1.0, 0.0])
    assert (receiver.y, receiver.z) == ([9.0], [0])

    # Each thinks the other wins -> receiver resets.
    receiver = _state(0)
    receiver.y, receiver.z = [4.0], [1]
    engine.resolve_conflicts(receiver, 1, [6.0], [0], [0.0, 1.0, 0.0])
    assert (receiver.y, receiver.z) == ([0.0], [NONE])

    # Sender has fresher information about third agent m=2 -> update.
    receiver = _state(0)
    receiver.y, receiver.z = [3.0], [NONE]
    engine.resolve_conflicts(receiver, 1, [8.0], [2], [0.0, 1.0, 5.0])
    assert (receiver.y, receiver.z) == ([8.0], [2])

    # Sender's info about m=2 is staler than receiver's -> leave.
    receiver = _state(0)
    receiver.s = [0.0, 0.0, 9.0]
    receiver.y, receiver.z = [2.0], [NONE]
    engine.resolve_conflicts(receiver, 1, [8.0], [2], [0.0, 1.0, 5.0])
    assert (receiver.y, receiver.z) == ([2.0], [NONE])


def test_bundle_construction_bids_are_non_increasing():
    engine = CBBAEngine()
    prob = problem([agent("A", 0, 0, max_tasks=5)], [point_task(f"T{n}", 100 * n, 50 * (n % 3)) for n in range(1, 7)])
    states = engine.initialize(prob)
    engine._validate(prob)
    engine._task_ids = [t.id for t in prob.tasks]
    engine.build_bundles(states, prob, {t.id: t for t in prob.tasks})

    state = states[0]
    assert len(state.bundle) == 5
    bids = [state.own_bids[j] for j in state.bundle]
    assert bids == sorted(bids, reverse=True)  # bid warping -> diminishing marginal gain
    assert {s.task_id for s in state.path} == {prob.tasks[j].id for j in state.bundle}
    assert all(state.z[j] == 0 for j in state.bundle)


def test_bundle_rebuilding_releases_tasks_after_outbid():
    engine = CBBAEngine()
    prob = problem([agent("A", 0, 0, max_tasks=4)], [point_task(f"T{n}", 150 * n, 0) for n in range(1, 5)])
    engine._validate(prob)
    engine._task_ids = [t.id for t in prob.tasks]
    states = engine.initialize(prob)
    engine.build_bundles(states, prob, {t.id: t for t in prob.tasks})
    state = states[0]
    original = list(state.bundle)
    assert len(original) == 4

    # Consensus told us another agent (index 1) won the 2nd task of our bundle.
    state.z[original[1]] = 1
    state.y[original[1]] = 1e6
    engine.update_bundles(states)

    assert state.bundle == original[:1]
    assert [s.task_id for s in state.path] == [prob.tasks[original[0]].id]
    for j in original[2:]:
        assert state.z[j] == NONE and state.y[j] == 0.0  # released and reset
    assert state.z[original[1]] == 1  # kept knowledge of the real winner


def test_convergence_reported():
    prob = benchmark_problem(8, 30)
    result = CBBAEngine().allocate(prob)
    assert result.converged and not result.timed_out
    assert 1 <= result.iterations < prob.params.max_iterations
    assert result.history[-1]["agree"] and result.history[-1]["unchanged"]
    assert result.consensus_rounds == result.iterations
    assert result.messages_exchanged == result.iterations * 8 * 7  # fully connected


def test_deterministic_output():
    first = CBBAEngine().allocate(benchmark_problem(8, 40))
    second = CBBAEngine().allocate(benchmark_problem(8, 40))
    assert first.assignments == second.assignments
    assert first.winning_bids == second.winning_bids
    for agent_id, plan in first.agent_plans.items():
        assert [s.task_id for s in plan.path] == [s.task_id for s in second.agent_plans[agent_id].path]
    assert first.iterations == second.iterations


def test_unassigned_tasks_due_to_capacity():
    prob = problem([agent("A", max_tasks=3)], [point_task(f"T{n}", 80 * n, 0) for n in range(1, 6)])
    result = CBBAEngine().allocate(prob)

    assert result.assigned_count == 3
    assert len(result.unassigned) == 2
    assert set(result.unassigned.values()) == {"insufficient_capacity"}
    assert_valid_allocation(result, prob)


def test_empty_task_list():
    result = CBBAEngine().allocate(problem([agent("A"), agent("B")], []))
    assert result.converged
    assert result.assignments == {} and result.unassigned == {}
    assert all(plan.path == [] for plan in result.agent_plans.values())


def test_empty_drone_list():
    result = CBBAEngine().allocate(problem([], [point_task("T1", 0, 0), point_task("T2", 10, 0)]))
    assert result.converged
    assert result.assignments == {}
    assert result.unassigned == {"T1": "no_drones_available", "T2": "no_drones_available"}


@pytest.mark.parametrize(
    "agents,tasks",
    [
        ([agent("A"), agent("A")], [point_task("T1", 0, 0)]),
        ([agent("A")], [point_task("T1", 0, 0), point_task("T1", 5, 0)]),
        ([agent("A", speed=0)], [point_task("T1", 0, 0)]),
        ([agent("A", battery=140)], [point_task("T1", 0, 0)]),
        ([agent("A")], [point_task("T1", float("nan"), 0)]),
        ([agent("A")], [point_task("T1", 0, 0, hover=-5)]),
    ],
)
def test_invalid_input_rejected(agents, tasks):
    with pytest.raises(InvalidProblemError):
        CBBAEngine().allocate(AllocationProblem(agents=tuple(agents), tasks=tuple(tasks)))


def test_fast_bid_matches_generic_reference_implementation():
    """DefaultUtility's O(L) insertion must agree with brute-force re-scoring."""
    util = DefaultUtility()
    a = agent("A", -100, -50, battery=80)
    tasks = {t.id: t for t in [point_task(f"P{n}", 90 * n, 40 * (n % 2), hover=5 * n) for n in range(1, 5)]}
    segment = benchmark_problem(1, 6).tasks[0]
    tasks[segment.id] = segment
    path = [PathStep("P2", 0), PathStep("P4", 0), PathStep("P1", 0)]

    for task_id in ("P3", segment.id):
        fast = util.calculate_bid(a, tasks[task_id], path, tasks)
        slow = UtilityModel.calculate_bid(util, a, tasks[task_id], path, tasks)
        assert fast is not None and slow is not None
        assert fast.value == pytest.approx(slow.value, rel=1e-9)
        assert (fast.position, fast.orientation) == (slow.position, slow.orientation)


def test_partitioned_network_never_double_books():
    # Range-limited comms with two isolated pairs: agents cannot reach agreement,
    # but the output must still be conflict-free.
    agents = [agent("A", 0, 0), agent("B", 50, 0), agent("C", 5000, 0), agent("D", 5050, 0)]
    tasks = [point_task(f"T{n}", 2500 + 10 * n, 0) for n in range(4)]
    prob = problem(agents, tasks)
    result = CBBAEngine(communication=RangeLimited(200)).allocate(prob)

    assert not result.converged
    assert_valid_allocation(result, prob)


def test_registry_exposes_cbba():
    assert isinstance(get_algorithm("cbba"), CBBAEngine)
    with pytest.raises(ValueError):
        get_algorithm("does-not-exist")
