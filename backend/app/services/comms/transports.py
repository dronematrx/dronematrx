"""Transport providers. Each one owns how a message is framed for its radio
and accounts for what it carried; the CommunicationManager decides which one
is active per drone and every outbound message goes through `send`.

All three hand the frame to the edge NATS server (the drone's link
terminates there) and tag it with `Dm-Link`, so consumers can see which
transport carried it. LoRa only carries a compact telemetry/alert frame
(PRD: "heartbeat + basic commands only"); bulky fields are dropped.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.services.comms.links import LinkType

LORA_TELEMETRY_FIELDS = ("drone_id", "uin", "timestamp", "lat", "lon", "alt", "speed", "heading", "soc", "rssi", "link_type", "status")
LORA_MAX_FRAME_BYTES = 255  # SX1276 max payload


@dataclass
class Transport:
    link: LinkType
    stats: dict[str, int] = field(default_factory=lambda: {"messages": 0, "bytes": 0, "dropped": 0})

    def frame(self, kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        return payload

    async def send(self, bus: Any, uin: str, kind: str, payload: dict[str, Any], msg_id: str | None = None) -> bool:
        framed = self.frame(kind, payload)
        if framed is None:
            self.stats["dropped"] += 1
            return False
        framed = {**framed, "link_type": self.link.value}
        await bus.publish(uin, kind, framed, msg_id=msg_id, link=self.link.value)
        self.stats["messages"] += 1
        self.stats["bytes"] += len(json.dumps(framed, default=str))
        return True


class WifiTransport(Transport):
    def __init__(self) -> None:
        super().__init__(LinkType.WIFI)


class LteTransport(Transport):
    def __init__(self) -> None:
        super().__init__(LinkType.LTE)


class LoRaTransport(Transport):
    def __init__(self) -> None:
        super().__init__(LinkType.LORA)

    def frame(self, kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if kind == "telemetry":
            compact = {k: payload[k] for k in LORA_TELEMETRY_FIELDS if k in payload}
            for key in ("lat", "lon"):
                if isinstance(compact.get(key), float):
                    compact[key] = round(compact[key], 6)
            for key in ("alt", "speed", "heading", "soc", "rssi"):
                if isinstance(compact.get(key), float):
                    compact[key] = round(compact[key], 1)
            return compact
        if kind in ("alert", "command"):
            return payload
        # Full state snapshots do not fit the LoRa budget: drop (ORCA state is
        # local to the edge orchestrator anyway).
        if len(json.dumps(payload, default=str)) > LORA_MAX_FRAME_BYTES:
            return None
        return payload


def default_transports() -> dict[LinkType, Transport]:
    return {LinkType.WIFI: WifiTransport(), LinkType.LTE: LteTransport(), LinkType.LORA: LoRaTransport()}
