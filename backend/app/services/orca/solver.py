"""Optimal Reciprocal Collision Avoidance (ORCA) solver.

Port of the agent-agent part of RVO2 (J. van den Berg, S. J. Guy, M. Lin,
D. Manocha, "Reciprocal n-body Collision Avoidance", ISRR 2011; reference
implementation RVO2 Library, Apache-2.0):

1. For each neighbour B, build the velocity obstacle VO(A|B) truncated at
   time horizon tau: a cone of relative velocities that collide within tau.
2. Take u = the smallest change to the relative velocity that leaves the VO.
3. ORCA half-plane: { v | (v - (v_A + r*u)) . n >= 0 }, where r is A's
   share of the responsibility (0.5 = reciprocal).
4. Solve the 2-D linear program: velocity closest to v_pref that satisfies
   every half-plane and |v| <= v_max (linearProgram1/2). If infeasible
   (dense crowds), minimise the maximum penetration (linearProgram3).

Extensions used by Drone MatrX:
* responsibility is split by priority (RTH / emergency drones yield less;
  shares always sum to 1, so the pair still avoids reciprocally);
* a neighbour whose comms are degraded may not react: we take full
  responsibility and inflate its radius by its possible stale displacement;
* identical positions / zero relative velocity get a deterministic escape
  direction instead of a division by zero.
"""
from __future__ import annotations

import math

from app.services.orca.models import OrcaLine, Vec

EPSILON = 1e-9


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1])


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1])


def _mul(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s)


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _det(a: Vec, b: Vec) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _abs_sq(a: Vec) -> float:
    return a[0] * a[0] + a[1] * a[1]


def _norm(a: Vec) -> Vec:
    length = math.sqrt(_abs_sq(a))
    return (a[0] / length, a[1] / length) if length > EPSILON else (0.0, 0.0)


def orca_line(
    position: Vec,
    velocity: Vec,
    other_position: Vec,
    other_velocity: Vec,
    combined_radius: float,
    time_horizon: float,
    timestep: float,
    responsibility: float = 0.5,
    tie_break: Vec = (1.0, 0.0),
    neighbor_id: str = "",
) -> OrcaLine:
    """The ORCA half-plane agent A (position/velocity) must respect for neighbour B."""
    rel_pos = _sub(other_position, position)
    rel_vel = _sub(velocity, other_velocity)
    dist_sq = _abs_sq(rel_pos)
    r_sq = combined_radius * combined_radius
    inv_tau = 1.0 / time_horizon

    if dist_sq > r_sq:
        # No collision yet. w: from cut-off circle centre to relative velocity.
        w = _sub(rel_vel, _mul(rel_pos, inv_tau))
        w_len_sq = _abs_sq(w)
        dot1 = _dot(w, rel_pos)
        if dot1 < 0.0 and dot1 * dot1 > r_sq * w_len_sq:
            # Project on the cut-off circle.
            w_len = math.sqrt(w_len_sq)
            unit_w = _mul(w, 1.0 / w_len) if w_len > EPSILON else tie_break
            direction = (unit_w[1], -unit_w[0])
            u = _mul(unit_w, combined_radius * inv_tau - w_len)
        else:
            # Project on the legs of the cone.
            leg = math.sqrt(dist_sq - r_sq)
            if _det(rel_pos, w) > 0.0:  # left leg
                direction = (
                    (rel_pos[0] * leg - rel_pos[1] * combined_radius) / dist_sq,
                    (rel_pos[0] * combined_radius + rel_pos[1] * leg) / dist_sq,
                )
            else:  # right leg
                direction = (
                    -(rel_pos[0] * leg + rel_pos[1] * combined_radius) / dist_sq,
                    -(-rel_pos[0] * combined_radius + rel_pos[1] * leg) / dist_sq,
                )
            dot2 = _dot(rel_vel, direction)
            u = _sub(_mul(direction, dot2), rel_vel)
    else:
        # Already overlapping: resolve within one timestep.
        inv_dt = 1.0 / timestep
        w = _sub(rel_vel, _mul(rel_pos, inv_dt))
        w_len = math.sqrt(_abs_sq(w))
        unit_w = _mul(w, 1.0 / w_len) if w_len > EPSILON else tie_break
        direction = (unit_w[1], -unit_w[0])
        u = _mul(unit_w, combined_radius * inv_dt - w_len)

    point = _add(velocity, _mul(u, responsibility))
    return OrcaLine(point=point, direction=direction, neighbor_id=neighbor_id)


def _linear_program1(lines: list[OrcaLine], line_no: int, radius: float, opt: Vec, direction_opt: bool) -> Vec | None:
    line = lines[line_no]
    dot = _dot(line.point, line.direction)
    discriminant = dot * dot + radius * radius - _abs_sq(line.point)
    if discriminant < 0.0:
        return None  # max-speed circle fully invalidates this line
    sqrt_disc = math.sqrt(discriminant)
    t_left = -dot - sqrt_disc
    t_right = -dot + sqrt_disc

    for i in range(line_no):
        denominator = _det(line.direction, lines[i].direction)
        numerator = _det(lines[i].direction, _sub(line.point, lines[i].point))
        if abs(denominator) <= EPSILON:
            if numerator < 0.0:
                return None  # parallel and on the wrong side
            continue
        t = numerator / denominator
        if denominator >= 0.0:
            t_right = min(t_right, t)
        else:
            t_left = max(t_left, t)
        if t_left > t_right:
            return None

    if direction_opt:
        t = t_right if _dot(opt, line.direction) > 0.0 else t_left
    else:
        t = _dot(line.direction, _sub(opt, line.point))
        t = min(max(t, t_left), t_right)
    return _add(line.point, _mul(line.direction, t))


def _linear_program2(lines: list[OrcaLine], radius: float, opt: Vec, direction_opt: bool) -> tuple[int, Vec]:
    if direction_opt:
        result = _mul(opt, radius)
    elif _abs_sq(opt) > radius * radius:
        result = _mul(_norm(opt), radius)
    else:
        result = opt
    for i in range(len(lines)):
        if _det(lines[i].direction, _sub(lines[i].point, result)) > 0.0:
            candidate = _linear_program1(lines, i, radius, opt, direction_opt)
            if candidate is None:
                return i, result
            result = candidate
    return len(lines), result


def _linear_program3(lines: list[OrcaLine], begin_line: int, radius: float, result: Vec) -> Vec:
    distance = 0.0
    for i in range(begin_line, len(lines)):
        if _det(lines[i].direction, _sub(lines[i].point, result)) > distance:
            projected: list[OrcaLine] = []
            for j in range(i):
                determinant = _det(lines[i].direction, lines[j].direction)
                if abs(determinant) <= EPSILON:
                    if _dot(lines[i].direction, lines[j].direction) > 0.0:
                        continue  # same direction
                    point = _mul(_add(lines[i].point, lines[j].point), 0.5)
                else:
                    point = _add(
                        lines[i].point,
                        _mul(lines[i].direction, _det(lines[j].direction, _sub(lines[i].point, lines[j].point)) / determinant),
                    )
                projected.append(OrcaLine(point=point, direction=_norm(_sub(lines[j].direction, lines[i].direction))))
            temp = result
            failed, candidate = _linear_program2(projected, radius, (-lines[i].direction[1], lines[i].direction[0]), True)
            result = temp if failed < len(projected) else candidate
            distance = _det(lines[i].direction, _sub(lines[i].point, result))
    return result


def solve(lines: list[OrcaLine], preferred: Vec, max_speed: float) -> tuple[Vec, bool]:
    """Velocity closest to `preferred` inside every half-plane and the speed
    disc. Returns (velocity, infeasible) -- infeasible means the constraints
    could not all be met and the least-penetrating velocity was chosen."""
    failed, result = _linear_program2(lines, max_speed, preferred, False)
    if failed < len(lines):
        return _linear_program3(lines, failed, max_speed, result), True
    return result, False


def satisfies(line: OrcaLine, velocity: Vec, tolerance: float = 1e-6) -> bool:
    """True when `velocity` lies in the permitted half-plane of `line`."""
    return _det(line.direction, _sub(line.point, velocity)) <= tolerance
