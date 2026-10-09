"""Link types, quality thresholds, and the simulated radio environment.

Fallback chain (highest preference first):  Wi-Fi  ->  LTE  ->  LoRa

Quality classification of one 500 ms sample (thresholds below, configurable):
    DOWN      breach: RSSI below `breach_rssi_dbm`, OR loss > `breach_loss_pct`,
              OR latency > `breach_latency_ms`, OR no carrier
    GOOD      RSSI >= `recover_rssi_dbm` AND loss <= `recover_loss_pct` AND
              latency <= `recover_latency_ms`  (stricter than the breach line)
    DEGRADED  anything in between (usable, but not good enough to switch UP to)

The gap between the breach and recover lines is the hysteresis band; together
with the recovery hold time in the manager it prevents Wi-Fi <-> LTE flapping.

The radio environment is SIMULATED (no physical radios are attached to the
edge server): each drone/link has a stable baseline with small deterministic
noise, and operators/tests inject degradation through the comms API.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class LinkType(str, Enum):
    WIFI = "wifi"
    LTE = "lte"
    LORA = "lora"


CHAIN: tuple[LinkType, ...] = (LinkType.WIFI, LinkType.LTE, LinkType.LORA)


@dataclass(frozen=True)
class LinkThresholds:
    breach_rssi_dbm: float
    recover_rssi_dbm: float
    breach_loss_pct: float
    recover_loss_pct: float
    breach_latency_ms: float
    recover_latency_ms: float


DEFAULT_THRESHOLDS: dict[LinkType, LinkThresholds] = {
    LinkType.WIFI: LinkThresholds(-75.0, -67.0, 10.0, 3.0, 150.0, 80.0),
    LinkType.LTE: LinkThresholds(-105.0, -95.0, 15.0, 5.0, 300.0, 150.0),
    LinkType.LORA: LinkThresholds(-125.0, -118.0, 30.0, 10.0, 2000.0, 1200.0),
}

# Baselines when healthy: (RSSI dBm, packet loss %, latency ms).
BASELINES: dict[LinkType, tuple[float, float, float]] = {
    LinkType.WIFI: (-58.0, 0.2, 21.0),
    LinkType.LTE: (-88.0, 0.8, 78.0),
    LinkType.LORA: (-104.0, 2.0, 420.0),
}


@dataclass(frozen=True)
class LinkSample:
    link: LinkType
    rssi_dbm: float
    packet_loss_pct: float
    latency_ms: float
    available: bool
    timestamp: float

    def as_dict(self) -> dict:
        return {
            "link": self.link.value,
            "rssi_dbm": round(self.rssi_dbm, 1),
            "packet_loss_pct": round(self.packet_loss_pct, 2),
            "latency_ms": round(self.latency_ms, 1),
            "available": self.available,
        }


def classify(sample: LinkSample, thresholds: LinkThresholds) -> str:
    if (
        not sample.available
        or sample.rssi_dbm < thresholds.breach_rssi_dbm
        or sample.packet_loss_pct > thresholds.breach_loss_pct
        or sample.latency_ms > thresholds.breach_latency_ms
    ):
        return "down"
    if (
        sample.rssi_dbm >= thresholds.recover_rssi_dbm
        and sample.packet_loss_pct <= thresholds.recover_loss_pct
        and sample.latency_ms <= thresholds.recover_latency_ms
    ):
        return "good"
    return "degraded"


@dataclass
class _Override:
    rssi_dbm: float | None = None
    packet_loss_pct: float | None = None
    latency_ms: float | None = None
    available: bool | None = None
    applied_at: float = 0.0


@dataclass
class LinkEnvironment:
    """Simulated per-drone radio conditions."""

    noise_db: float = 1.5
    overrides: dict[tuple[str, LinkType], _Override] = field(default_factory=dict)
    injected_at: dict[tuple[str, LinkType], float] = field(default_factory=dict)

    def degrade(self, drone_id: str, link: LinkType, now: float, rssi_dbm: float | None = None,
                packet_loss_pct: float | None = None, latency_ms: float | None = None, available: bool | None = None) -> None:
        self.overrides[(drone_id, link)] = _Override(rssi_dbm, packet_loss_pct, latency_ms, available, now)
        self.injected_at[(drone_id, link)] = now

    def restore(self, drone_id: str, link: LinkType | None = None, now: float = 0.0) -> None:
        for key in [k for k in self.overrides if k[0] == drone_id and (link is None or k[1] == link)]:
            del self.overrides[key]
            self.injected_at[key] = now

    def sample(self, drone_id: str, link: LinkType, now: float, index: int = 0) -> LinkSample:
        rssi, loss, latency = BASELINES[link]
        # Small deterministic jitter (no RNG: reproducible tests and demos).
        phase = now * 0.9 + index * 1.7 + CHAIN.index(link) * 0.5
        rssi += self.noise_db * math.sin(phase)
        latency *= 1.0 + 0.05 * math.sin(phase * 1.3)
        available = True
        override = self.overrides.get((drone_id, link))
        if override is not None:
            rssi = override.rssi_dbm if override.rssi_dbm is not None else rssi
            loss = override.packet_loss_pct if override.packet_loss_pct is not None else loss
            latency = override.latency_ms if override.latency_ms is not None else latency
            available = override.available if override.available is not None else available
        return LinkSample(link, rssi, loss, latency, available, now)
