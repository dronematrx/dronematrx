"""Regression: the dev telemetry simulator must keep drones near home.

Before Sprint 3 each new target was picked around the drone's *current*
position, so a long-running backend random-walked the fleet kilometres away
(which then made every planned mission infeasible)."""
import math
import random

from app.services.telemetry.simulator import SimulationTelemetryProvider, _SimDroneState


def test_simulated_drone_stays_within_patrol_radius_of_home():
    random.seed(7)
    state = _SimDroneState(drone_id="d1", name="D1", lat=23.2156, lon=72.6369, altitude=40)
    state.pick_new_target()
    provider = SimulationTelemetryProvider(session_factory=None, tick_seconds=1.0)  # type: ignore[arg-type]
    furthest = 0.0
    for _ in range(20_000):  # ~5.5 hours of 1 Hz ticks
        provider._advance(state)
        furthest = max(furthest, math.hypot(state.lat - 23.2156, state.lon - 72.6369))
    assert furthest <= 0.0041  # patrol radius (0.004 deg ~ 440 m) + one step
