"""Sprint 4 -- ORCA unit tests (real solver, no mocks). Every test checks
velocity / separation behaviour, not just that something was returned."""
import math

import pytest

from app.services.orca.deconfliction import AltitudeBandManager, RTHPolicy, RTHPriorityQueue, time_to_collision
from app.services.orca.engine import OrcaEngine
from app.services.orca.models import AgentState, InvalidAgentStateError, OrcaParams
from app.services.orca.simulation import OrcaSimulation, SimAgent
from app.services.orca.solver import satisfies

P = OrcaParams()  # radius 1 m + 2 m margin -> 6 m combined avoidance envelope


def agent(agent_id, pos, vel=(0.0, 0.0), pref=None, **kw):
    return AgentState(id=agent_id, position=pos, velocity=vel, preferred_velocity=pref if pref is not None else vel, **kw)


def run(agents, max_time=60.0, params=P):
    sim = OrcaSimulation(agents, params)
    return sim, sim.run(max_time)


def assert_safe(report, params=P, envelope=True):
    assert report.collision_violations == 0
    if envelope:
        assert report.envelope_breaches == 0
        assert report.min_separation_m >= 2 * (params.agent_radius_m + params.safety_margin_m) - 0.05


def speed(v):
    return math.hypot(*v)


def test_01_head_on_two_drones_deviate_and_pass():
    a = agent("A", (-50.0, 0.0), (10.0, 0.0))
    b = agent("B", (50.0, 0.0), (-10.0, 0.0))
    step = OrcaEngine(P).step([a, b])
    da, db = step.decisions["A"], step.decisions["B"]
    assert step.predictions and step.predictions[0].time_to_collision_s == pytest.approx((100 - 6) / 20)
    assert da.safe_velocity[1] != 0 and db.safe_velocity[1] != 0  # both sidestep...
    assert math.copysign(1, da.safe_velocity[1]) != math.copysign(1, db.safe_velocity[1])  # ...in opposite directions
    for line in da.constraints:
        assert satisfies(line, da.safe_velocity)
    _, rep = run([SimAgent("A", (-50, 0), (50, 0)), SimAgent("B", (50, 0), (-50, 0))])
    assert rep.arrived == 2
    assert_safe(rep)


def test_02_same_direction_no_interference():
    a = agent("A", (0.0, 0.0), (10.0, 0.0))
    b = agent("B", (0.0, 20.0), (10.0, 0.0))
    step = OrcaEngine(P).step([a, b])
    assert step.predictions == []
    assert step.decisions["A"].safe_velocity == pytest.approx((10.0, 0.0))
    assert step.decisions["B"].safe_velocity == pytest.approx((10.0, 0.0))


def test_03_crossing_trajectories():
    _, rep = run([SimAgent("A", (-60, 0), (60, 0)), SimAgent("B", (0, -60), (0, 60))])
    assert rep.predicted_conflicts >= 1 and rep.avoidance_events >= 1
    assert rep.arrived == 2
    assert_safe(rep)


def test_04_parallel_trajectories_outside_envelope_are_untouched():
    _, rep = run([SimAgent("A", (-60, 0), (60, 0)), SimAgent("B", (-60, 8), (60, 8))])
    assert rep.predicted_conflicts == 0 and rep.avoidance_events == 0
    assert rep.min_separation_m >= 8.0 - 1e-6  # never closer than their lane spacing


def test_05_stationary_drone_is_avoided():
    sim, rep = run([SimAgent("MOVER", (-60, 0.5), (60, 0.5)), SimAgent("HOVER", (0, 0), (0, 0))])
    assert rep.avoidance_events >= 1
    assert_safe(rep)
    by_id = {a.id: a for a in sim.agents}
    assert by_id["HOVER"].position == pytest.approx((0, 0), abs=0.5)  # hovering drone barely moved
    assert by_id["MOVER"].arrived  # the mover went around it


@pytest.mark.parametrize("n", [3, 4, 8])
def test_06_07_08_n_drone_symmetric_conflict(n):
    agents = []
    for k in range(n):
        ang = 2 * math.pi * k / n
        start = (70 * math.cos(ang), 70 * math.sin(ang))
        agents.append(SimAgent(f"D{k}", start, (-start[0], -start[1]), cruise_speed=10))
    _, rep = run(agents, 90)
    assert rep.arrived == n
    assert rep.predicted_conflicts >= n - 1
    assert_safe(rep)


def test_09_different_radii():
    big = SimAgent("BIG", (-50, 0), (50, 0), radius=3.0)
    small = SimAgent("SMALL", (50, 0.3), (-50, 0.3), radius=0.5)
    _, rep = run([big, small])
    # envelope = (3+2) + (0.5+2) = 7.5 m
    assert rep.min_separation_m >= 7.5 - 0.05
    assert rep.collision_violations == 0


def test_10_different_max_speeds_respected():
    slow = agent("SLOW", (-40.0, 0.0), (4.0, 0.0), max_speed=4.0)
    fast = agent("FAST", (40.0, 0.0), (-14.0, 0.0), max_speed=15.0)
    step = OrcaEngine(P).step([slow, fast])
    assert speed(step.decisions["SLOW"].safe_velocity) <= 4.0 + 1e-6
    assert speed(step.decisions["FAST"].safe_velocity) <= 15.0 + 1e-6
    _, rep = run([SimAgent("SLOW", (-40, 0), (40, 0), cruise_speed=4, max_speed=4), SimAgent("FAST", (40, 0), (-40, 0), cruise_speed=14)])
    assert_safe(rep)


def test_11_altitude_bands_separate_conflicts():
    low = agent("LOW", (-50.0, 0.0), (10.0, 0.0), altitude=40.0)
    high = agent("HIGH", (50.0, 0.0), (-10.0, 0.0), altitude=60.0)
    step = OrcaEngine(P).step([low, high])
    assert step.predictions == []  # 20 m apart vertically >= 8 m threshold
    assert step.decisions["LOW"].neighbors == [] and step.decisions["LOW"].safe_velocity == (10.0, 0.0)
    same = agent("HIGH", (50.0, 0.0), (-10.0, 0.0), altitude=45.0)  # only 5 m apart: same band
    step = OrcaEngine(P).step([low, same])
    assert step.predictions and step.decisions["LOW"].neighbors == ["HIGH"]

    bands = AltitudeBandManager(base_altitude_m=30, band_spacing_m=10, cruise_bands=2, max_per_band=1)
    assert bands.assign_cruise("A") == 1 and bands.assign_cruise("B") == 2
    assert bands.assign_cruise("C") is None  # every band full -> hold
    assert bands.assign_rth("A") == 0 and bands.altitude_for(0) == 30.0
    assert bands.assign_cruise("C") == 1  # A left band 1 for the RTH band


def test_12_rth_priority_queue_order_and_concurrency():
    q = RTHPriorityQueue(RTHPolicy(critical_battery_pct=15, max_concurrent=2))
    q.request("NORMAL", q.classify(60), 60, now=1)
    q.request("COMMS", q.classify(55, comm_degraded=True), 55, now=2)
    q.request("FAULT", q.classify(70, emergency=True), 70, now=3)
    q.request("LOWBAT", q.classify(12), 12, now=4)
    assert q.grant() == ["LOWBAT", "FAULT"]  # critical battery first, then emergency
    assert [w["drone_id"] for w in q.waiting()] == ["COMMS", "NORMAL"]
    assert q.grant() == []  # both slots busy: remaining drones hold
    q.complete("LOWBAT")
    assert q.grant() == ["COMMS"]
    # Escalation: a waiting drone whose battery goes critical jumps the queue.
    q.request("LATE", "normal", 50, now=5)
    q.request("NORMAL", q.classify(10), 10, now=6)
    q.complete("FAULT")
    assert q.grant() == ["NORMAL"]

    # RTH priority also shifts ORCA responsibility: the RTH drone yields less.
    rth = agent("RTH", (-30.0, 0.0), (10.0, 0.0), priority=3)
    survey = agent("SURVEY", (30.0, 0.0), (-10.0, 0.0), priority=0)
    step = OrcaEngine(P).step([rth, survey])
    assert speed(step.decisions["RTH"].avoidance_vector) < speed(step.decisions["SURVEY"].avoidance_vector)


def test_13_no_neighbors_keeps_preferred_velocity():
    step = OrcaEngine(P).step([agent("SOLO", (0.0, 0.0), (3.0, 4.0))])
    d = step.decisions["SOLO"]
    assert d.safe_velocity == (3.0, 4.0) and d.neighbors == [] and d.constraints == [] and not d.collision_predicted


def test_14_zero_velocity_agents_stay_put():
    step = OrcaEngine(P).step([agent("A", (0.0, 0.0)), agent("B", (20.0, 0.0))])
    assert step.decisions["A"].safe_velocity == pytest.approx((0, 0))
    assert step.predictions == []


def test_15_identical_positions_are_pushed_apart_deterministically():
    a, b = agent("A", (0.0, 0.0)), agent("B", (0.0, 0.0))
    d = OrcaEngine(P).step([a, b]).decisions
    va, vb = d["A"].safe_velocity, d["B"].safe_velocity
    assert speed(va) > 0 and speed(vb) > 0
    assert va[0] * vb[0] + va[1] * vb[1] < 0  # opposite directions
    _, rep = run([SimAgent("A", (0, 0), (0, 0)), SimAgent("B", (0, 0), (0, 0))], 10)
    assert rep.collision_violations <= 2  # they start overlapping; must separate within a couple of ticks
    sim = OrcaSimulation([SimAgent("A", (0, 0), (0, 0)), SimAgent("B", (0, 0), (0, 0))], P)
    for _ in range(10):
        sim.step()
    assert math.dist(sim.agents[0].position, sim.agents[1].position) >= 6.0 - 0.05


def test_16_very_close_drones_separate():
    sim = OrcaSimulation([SimAgent("A", (0, 0), (0, 0)), SimAgent("B", (3.0, 0), (3.0, 0))], P)  # inside 6 m envelope
    sim.step()
    assert sim.agents[0].velocity[0] < 0 and sim.agents[1].velocity[0] > 0  # moving apart immediately
    for _ in range(20):
        sim.step()
    assert math.dist(sim.agents[0].position, sim.agents[1].position) >= 6.0 - 0.05
    assert sim.report.collision_violations == 0  # 3 m > 2 m physical contact


def test_17_high_speed_approach():
    fast = OrcaParams(max_speed_mps=25.0)
    _, rep = run([SimAgent("A", (-150, 0), (150, 0), cruise_speed=25), SimAgent("B", (150, 0.2), (-150, 0.2), cruise_speed=25)], 60, fast)
    assert rep.arrived == 2
    assert_safe(rep, fast)


def test_18_comm_degraded_neighbor_gets_full_responsibility_and_buffer():
    me = agent("ME", (-40.0, 0.0), (10.0, 0.0))
    normal = agent("THEM", (40.0, 0.0), (-10.0, 0.0))
    degraded = agent("THEM", (40.0, 0.0), (-10.0, 0.0), comm_degraded=True)
    reciprocal = OrcaEngine(P).step([me, normal]).decisions["ME"]
    cautious = OrcaEngine(P).step([me, degraded]).decisions["ME"]
    assert speed(cautious.avoidance_vector) > speed(reciprocal.avoidance_vector)
    # Even if the degraded drone never reacts (flies straight), we stay clear.
    sim = OrcaSimulation([SimAgent("ME", (-40, 0), (40, 0)), SimAgent("THEM", (40, 0), (-40, 0), comm_degraded=True)], P)
    for _ in range(120):
        sim.step()
        them = sim.agents[1]
        them.velocity = (-10.0, 0.0)  # override: it ignores ORCA entirely
        them.position = (them.position[0] - 10.0 * P.timestep_s + 0.0, 0.0)
        if them.position[0] < -40:
            break
    assert sim.report.collision_violations == 0


@pytest.mark.parametrize(
    "bad",
    [
        [agent("A", (math.nan, 0.0))],
        [agent("A", (0.0, 0.0), (math.inf, 0.0))],
        [agent("A", (0.0, 0.0)), agent("A", (5.0, 0.0))],
        [agent("", (0.0, 0.0))],
        [agent("A", (0.0, 0.0), radius=-1.0)],
        [agent("A", (0.0, 0.0), max_speed=0.0)],
    ],
)
def test_19_invalid_state_rejected(bad):
    with pytest.raises(InvalidAgentStateError):
        OrcaEngine(P).step(bad)


def test_19b_invalid_params_rejected():
    with pytest.raises(InvalidAgentStateError):
        OrcaEngine(OrcaParams(time_horizon_s=0))


def test_20_deterministic_output():
    def once():
        from app.services.orca.scenarios import eight_drone_run

        _, agents = eight_drone_run(7)
        sim = OrcaSimulation(agents, P)
        sim.run(90)
        return [(a.id, round(a.position[0], 9), round(a.position[1], 9)) for a in sim.agents], sim.report.min_separation_m

    assert once() == once()


def test_time_to_collision_math():
    assert time_to_collision((0, 0), (10, 0), (100, 0), (-10, 0), 6.0) == pytest.approx(4.7)
    assert time_to_collision((0, 0), (10, 0), (0, 20), (10, 0), 6.0) is None  # parallel
    assert time_to_collision((0, 0), (0, 0), (3, 0), (0, 0), 6.0) == 0.0  # overlapping
    assert time_to_collision((0, 0), (-10, 0), (100, 0), (10, 0), 6.0) is None  # separating


def test_safe_velocity_satisfies_all_constraints_when_feasible():
    agents = [agent(f"D{k}", (30 * math.cos(k), 30 * math.sin(k)), (-3 * math.cos(k), -3 * math.sin(k))) for k in range(6)]
    for d in OrcaEngine(P).step(agents).decisions.values():
        if not d.infeasible:
            assert all(satisfies(line, d.safe_velocity) for line in d.constraints)
        assert speed(d.safe_velocity) <= P.max_speed_mps + 1e-6


def test_events_timeline_sequence():
    sim = OrcaSimulation([SimAgent("A", (-60, 0), (60, 0)), SimAgent("B", (60, 0), (-60, 0))], P)
    sim.run(30)
    kinds = [e.kind for e in sim.engine.events]
    assert kinds.index("conflict_predicted") < kinds.index("constraint_generated") < kinds.index("conflict_cleared")
    assert "avoidance_applied" in kinds
    cleared = next(e for e in sim.engine.events if e.kind == "conflict_cleared")
    assert cleared.details["min_separation_m"] >= 6.0 - 0.05
    predicted = next(e for e in sim.engine.events if e.kind == "conflict_predicted")
    assert predicted.details["time_to_collision_s"] >= 3.0  # alert well before the intersection
