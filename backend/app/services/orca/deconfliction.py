"""Global deconfliction layer on top of ORCA: collision prediction,
altitude banding, and the Return-To-Home priority queue."""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from app.services.orca.models import Vec


# --------------------------------------------------------------- prediction


def time_to_collision(p_a: Vec, v_a: Vec, p_b: Vec, v_b: Vec, combined_radius: float) -> float | None:
    """Seconds until two discs (moving at constant velocity) first touch.

    0.0 if already overlapping, None if they never will. Solves
    |dp + dv t| = R for the smallest t >= 0.
    """
    dp = (p_b[0] - p_a[0], p_b[1] - p_a[1])
    dv = (v_b[0] - v_a[0], v_b[1] - v_a[1])
    c = dp[0] * dp[0] + dp[1] * dp[1] - combined_radius * combined_radius
    if c <= 0.0:
        return 0.0
    a = dv[0] * dv[0] + dv[1] * dv[1]
    b = 2.0 * (dp[0] * dv[0] + dp[1] * dv[1])
    if a <= 1e-12 or b >= 0.0:
        return None  # not moving relative to each other, or separating
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return None  # closest approach stays outside R
    return (-b - math.sqrt(disc)) / (2.0 * a)


# ---------------------------------------------------------- altitude bands


@dataclass
class AltitudeBandManager:
    """Global vertical deconfliction.

    Band k flies at `base_altitude_m + k * band_spacing_m`. Band 0 is reserved
    for drones returning home (PRD: the RTH drone always gets the lowest band;
    survey drones yield). Cruise bands hold at most `max_per_band` drones;
    when every band is full a drone is told to HOLD until one frees up.
    """

    base_altitude_m: float = 30.0
    band_spacing_m: float = 10.0
    cruise_bands: int = 4  # bands 1..cruise_bands
    max_per_band: int = 8
    assignments: dict[str, int] = field(default_factory=dict)

    def altitude_for(self, band: int) -> float:
        return self.base_altitude_m + band * self.band_spacing_m

    def occupancy(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for band in self.assignments.values():
            counts[band] = counts.get(band, 0) + 1
        return counts

    def assign_cruise(self, drone_id: str, preferred_band: int | None = None) -> int | None:
        """Lowest cruise band with room (or `preferred_band` if it has room). None = all full -> HOLD."""
        current = self.assignments.get(drone_id)
        if current is not None and current >= 1:
            return current
        counts = self.occupancy()
        candidates = list(range(1, self.cruise_bands + 1))
        if preferred_band in candidates:
            candidates.remove(preferred_band)
            candidates.insert(0, preferred_band)  # type: ignore[arg-type]
        for band in candidates:
            if counts.get(band, 0) < self.max_per_band:
                self.assignments[drone_id] = band
                return band
        self.assignments.pop(drone_id, None)
        return None

    def assign_rth(self, drone_id: str) -> int:
        self.assignments[drone_id] = 0
        return 0

    def release(self, drone_id: str) -> None:
        self.assignments.pop(drone_id, None)

    @staticmethod
    def vertically_separated(alt_a: float, alt_b: float, threshold_m: float) -> bool:
        return abs(alt_a - alt_b) >= threshold_m


# ----------------------------------------------------------- RTH queue

RTH_REASON_RANK = {
    "critical_battery": 0,
    "emergency": 1,
    "comm_degraded": 2,
    "normal": 3,
}


@dataclass(frozen=True)
class RTHPolicy:
    """Priority rules (documented in docs/sprint4):

    1. critical battery (SOC <= critical_battery_pct)  -- most urgent
    2. emergency / fault state
    3. communication degradation (on LoRa / link lost)
    4. normal RTH (mission complete, operator command)

    Ties: lower SOC first, then earlier request, then drone id (deterministic).
    At most `max_concurrent` drones fly home at once; the rest HOLD in place
    so return trajectories are released one slot at a time.
    """

    critical_battery_pct: float = 15.0
    max_concurrent: int = 2


@dataclass(order=True)
class _Entry:
    key: tuple
    drone_id: str = field(compare=False)
    reason: str = field(compare=False)
    battery_pct: float = field(compare=False)
    requested_at: float = field(compare=False)


class RTHPriorityQueue:
    def __init__(self, policy: RTHPolicy | None = None):
        self.policy = policy or RTHPolicy()
        self._heap: list[_Entry] = []
        self._queued: dict[str, _Entry] = {}
        self.active: dict[str, _Entry] = {}
        self._counter = 0

    def classify(self, battery_pct: float, emergency: bool = False, comm_degraded: bool = False) -> str:
        if battery_pct <= self.policy.critical_battery_pct:
            return "critical_battery"
        if emergency:
            return "emergency"
        if comm_degraded:
            return "comm_degraded"
        return "normal"

    def request(self, drone_id: str, reason: str, battery_pct: float, now: float) -> bool:
        """Queue (or re-prioritise) an RTH request. Returns False if already flying home."""
        if reason not in RTH_REASON_RANK:
            raise ValueError(f"Unknown RTH reason '{reason}'")
        if drone_id in self.active:
            return False
        existing = self._queued.get(drone_id)
        requested_at = existing.requested_at if existing else now
        if existing is not None and RTH_REASON_RANK[reason] >= RTH_REASON_RANK[existing.reason] and battery_pct >= existing.battery_pct:
            return True  # nothing more urgent than before
        self._counter += 1
        entry = _Entry(
            key=(RTH_REASON_RANK[reason], round(battery_pct, 3), requested_at, drone_id),
            drone_id=drone_id,
            reason=reason,
            battery_pct=battery_pct,
            requested_at=requested_at,
        )
        self._queued[drone_id] = entry
        heapq.heappush(self._heap, entry)
        return True

    def grant(self) -> list[str]:
        """Release the highest-priority queued drones into free RTH slots."""
        granted: list[str] = []
        while self._heap and len(self.active) < self.policy.max_concurrent:
            entry = heapq.heappop(self._heap)
            if self._queued.get(entry.drone_id) is not entry:
                continue  # superseded by a re-prioritised entry
            del self._queued[entry.drone_id]
            self.active[entry.drone_id] = entry
            granted.append(entry.drone_id)
        return granted

    def complete(self, drone_id: str) -> None:
        self.active.pop(drone_id, None)
        self._queued.pop(drone_id, None)

    def waiting(self) -> list[dict]:
        live = sorted(e for e in self._heap if self._queued.get(e.drone_id) is e)
        return [{"drone_id": e.drone_id, "reason": e.reason, "battery_pct": e.battery_pct, "position": n + 1} for n, e in enumerate(live)]

    def snapshot(self) -> dict:
        return {
            "active": [{"drone_id": e.drone_id, "reason": e.reason, "battery_pct": e.battery_pct} for e in sorted(self.active.values())],
            "queued": self.waiting(),
            "max_concurrent": self.policy.max_concurrent,
        }

    def is_waiting(self, drone_id: str) -> bool:
        return drone_id in self._queued
