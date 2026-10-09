"""Deterministic multi-drone conflict scenarios (seeded, reproducible).

`eight_drone_run(i)` cycles through five families so the 50 consecutive
runs cover: antipodal circle swaps, two crossing streams, converging flows,
random box traffic, and head-on lanes -- with varied start positions,
speeds (5-15 m/s) and geometry per run.
"""
from __future__ import annotations

import math
import random

from app.services.orca.simulation import SimAgent

FAMILIES = ("circle_swap", "crossing_streams", "converging", "random_box", "head_on_lanes")


def _ids(n: int) -> list[str]:
    return [f"DMX-{k + 1:03d}" for k in range(n)]


def circle_swap(rng: random.Random, n: int = 8) -> list[SimAgent]:
    radius = rng.uniform(60, 160)
    phase = rng.uniform(0, 2 * math.pi)
    agents = []
    for k, drone_id in enumerate(_ids(n)):
        angle = phase + 2 * math.pi * k / n
        start = (radius * math.cos(angle), radius * math.sin(angle))
        agents.append(SimAgent(drone_id, start, (-start[0], -start[1]), cruise_speed=rng.uniform(6, 14)))
    return agents


def crossing_streams(rng: random.Random, n: int = 8) -> list[SimAgent]:
    half = n // 2
    gap = rng.uniform(10, 18)
    reach = rng.uniform(90, 140)
    angle = rng.uniform(math.radians(60), math.radians(120))  # crossing angle between streams
    agents = []
    ids = _ids(n)
    for k in range(half):  # stream A along +x
        offset = (k - (half - 1) / 2) * gap
        agents.append(SimAgent(ids[k], (-reach, offset), (reach, offset), cruise_speed=rng.uniform(7, 13)))
    ux, uy = math.cos(angle), math.sin(angle)
    for k in range(n - half):  # stream B along the crossing angle
        offset = (k - (n - half - 1) / 2) * gap
        start = (-reach * ux - offset * uy, -reach * uy + offset * ux)
        goal = (reach * ux - offset * uy, reach * uy + offset * ux)
        agents.append(SimAgent(ids[half + k], start, goal, cruise_speed=rng.uniform(7, 13)))
    return agents


def converging(rng: random.Random, n: int = 8) -> list[SimAgent]:
    agents = []
    for k, drone_id in enumerate(_ids(n)):
        angle = 2 * math.pi * k / n + rng.uniform(-0.2, 0.2)
        r = rng.uniform(80, 150)
        start = (r * math.cos(angle), r * math.sin(angle))
        exit_angle = angle + math.pi + rng.uniform(-0.6, 0.6)  # pass near the centre, exit elsewhere
        goal = (r * math.cos(exit_angle), r * math.sin(exit_angle))
        agents.append(SimAgent(drone_id, start, goal, cruise_speed=rng.uniform(5, 15)))
    return agents


def random_box(rng: random.Random, n: int = 8, size: float = 240.0, min_gap: float = 15.0) -> list[SimAgent]:
    def sample(existing: list[tuple[float, float]]) -> tuple[float, float]:
        while True:
            p = (rng.uniform(-size / 2, size / 2), rng.uniform(-size / 2, size / 2))
            if all(math.hypot(p[0] - q[0], p[1] - q[1]) >= min_gap for q in existing):
                return p

    starts: list[tuple[float, float]] = []
    goals: list[tuple[float, float]] = []
    for _ in range(n):
        starts.append(sample(starts))
    for s in starts:
        g = sample(goals)
        while math.hypot(g[0] - s[0], g[1] - s[1]) < size / 2:
            g = sample(goals)
        goals.append(g)
    return [SimAgent(i, s, g, cruise_speed=rng.uniform(5, 15)) for i, s, g in zip(_ids(n), starts, goals)]


def head_on_lanes(rng: random.Random, n: int = 8) -> list[SimAgent]:
    agents = []
    ids = _ids(n)
    reach = rng.uniform(80, 130)
    gap = rng.uniform(8, 12)
    pairs = n // 2
    for k in range(pairs):
        # Lanes 8-12 m apart: start positions respect the 6 m avoidance envelope,
        # but passing drones in adjacent lanes still have to make room.
        y = (k - (pairs - 1) / 2) * gap
        jitter = rng.uniform(-1.0, 1.0)
        agents.append(SimAgent(ids[2 * k], (-reach, y), (reach, y + jitter), cruise_speed=rng.uniform(6, 15)))
        agents.append(SimAgent(ids[2 * k + 1], (reach, y + jitter), (-reach, y), cruise_speed=rng.uniform(6, 15)))
    return agents


def eight_drone_run(run_index: int, seed: int = 2026) -> tuple[str, list[SimAgent]]:
    family = FAMILIES[run_index % len(FAMILIES)]
    rng = random.Random(seed * 1000 + run_index)
    builder = {
        "circle_swap": circle_swap,
        "crossing_streams": crossing_streams,
        "converging": converging,
        "random_box": random_box,
        "head_on_lanes": head_on_lanes,
    }[family]
    return family, builder(rng)
