"""Sprint 4 HARD acceptance: 50 consecutive 8-drone ORCA runs with ZERO
collision violations, and collision-response resolution < 500 ms.

Nothing is mocked: each run is a full closed-loop simulation through the
real engine. Resolution is the wall time of the complete pipeline in the
control cycle where a conflict is first predicted (prediction -> ORCA LP ->
safe velocity -> integrated trajectory).
"""
import math
import statistics

from app.services.orca.models import OrcaParams
from app.services.orca.scenarios import FAMILIES, eight_drone_run
from app.services.orca.simulation import OrcaSimulation, percentile

RUNS = 50
RESOLUTION_LIMIT_MS = 500.0


def test_fifty_consecutive_eight_drone_runs_zero_collisions():
    params = OrcaParams()
    envelope = 2 * (params.agent_radius_m + params.safety_margin_m)
    totals = {"violations": 0, "breaches": 0, "conflicts": 0, "avoidance": 0, "arrived": 0}
    min_sep = math.inf
    step_ms: list[float] = []
    resolution_ms: list[float] = []
    families_seen = set()

    for run in range(RUNS):
        family, agents = eight_drone_run(run)
        families_seen.add(family)
        assert len(agents) == 8
        report = OrcaSimulation(agents, params).run(max_time_s=90)
        totals["violations"] += report.collision_violations
        totals["breaches"] += report.envelope_breaches
        totals["conflicts"] += report.predicted_conflicts
        totals["avoidance"] += report.avoidance_events
        totals["arrived"] += report.arrived
        min_sep = min(min_sep, report.min_separation_m)
        step_ms += report.step_ms
        resolution_ms += report.resolution_ms
        assert report.collision_violations == 0, f"run {run} ({family}) collided"

    print(
        f"\n[orca-50] runs={RUNS} drone-runs={RUNS * 8} violations={totals['violations']} "
        f"envelope_breaches={totals['breaches']} min_sep={min_sep:.3f}m conflicts={totals['conflicts']} "
        f"avoidance_events={totals['avoidance']} arrived={totals['arrived']}/400 "
        f"cycle avg={statistics.fmean(step_ms):.3f}ms max={max(step_ms):.3f}ms "
        f"resolution avg={statistics.fmean(resolution_ms):.3f}ms p95={percentile(resolution_ms, 95):.3f}ms max={max(resolution_ms):.3f}ms"
    )
    assert families_seen == set(FAMILIES)
    assert totals["violations"] == 0
    assert totals["breaches"] == 0
    assert min_sep >= envelope - 0.05
    assert totals["conflicts"] > 100  # the scenarios are genuinely conflicted
    assert totals["avoidance"] > 100
    assert totals["arrived"] == RUNS * 8
    assert max(resolution_ms) < RESOLUTION_LIMIT_MS
    # 10 Hz budget: every control cycle for 8 drones fits in its 100 ms period.
    assert max(step_ms) < params.timestep_s * 1000
