"""Sprint 4 -- communication fallback manager (Wi-Fi -> LTE -> LoRa)."""
import asyncio
import time
import uuid

import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.messaging.bus import DroneBus
from app.services.comms.links import CHAIN, DEFAULT_THRESHOLDS, LinkEnvironment, LinkSample, LinkType, classify
from app.services.comms.manager import CommsPolicy, CommunicationManager

settings = get_settings()
WIFI, LTE, LORA = LinkType.WIFI, LinkType.LTE, LinkType.LORA


def manager(**policy):
    m = CommunicationManager(policy=CommsPolicy(**policy))
    m.register("d1", "DRN-01-000001", "DMX-001")
    m.register("d2", "DRN-01-000002", "DMX-002")
    return m


def run(m, start, seconds, hz=2.0):
    """Advance the manager's 2 Hz poll over simulated time."""
    events, t = [], start
    while t < start + seconds - 1e-9:
        events += m.poll_once(t)
        t += 1.0 / hz
    return events, t


def test_threshold_classification():
    wifi = DEFAULT_THRESHOLDS[WIFI]
    s = lambda rssi, loss=0.2, lat=20.0, up=True: LinkSample(WIFI, rssi, loss, lat, up, 0.0)  # noqa: E731
    assert classify(s(-58), wifi) == "good"
    assert classify(s(-70), wifi) == "degraded"  # inside the hysteresis band
    assert classify(s(-80), wifi) == "down"
    assert classify(s(-58, loss=12), wifi) == "down"
    assert classify(s(-58, lat=200), wifi) == "down"
    assert classify(s(-58, up=False), wifi) == "down"


def test_wifi_healthy_stays_on_wifi():
    m = manager()
    events, _ = run(m, 0.0, 10)
    assert all(d.active == WIFI for d in m.drones.values())
    assert not [e for e in events if e["kind"] in ("fallback", "recovery")]
    snap = m.snapshot()["drones"][0]
    assert snap["active_link"] == "wifi" and -62 < snap["rssi_dbm"] < -54 and snap["latency_ms"] > 0


def test_fallback_chain_and_recovery_policy():
    m = manager()
    run(m, 0.0, 3)
    # Wi-Fi degrades -> LTE on the very next poll.
    m.environment.degrade("d1", WIFI, now=3.0, rssi_dbm=-82)
    events = m.poll_once(3.0)
    assert m.drones["d1"].active == LTE and m.drones["d2"].active == WIFI  # per-drone, not fleet-wide
    kinds = [e["kind"] for e in events]
    assert kinds.index("threshold_breach") < kinds.index("fallback")
    fallback = next(e for e in events if e["kind"] == "fallback")
    assert (fallback["from"], fallback["to"]) == ("wifi", "lte")

    # LTE degrades too -> LoRa.
    m.environment.degrade("d1", LTE, now=6.0, rssi_dbm=-112)
    run(m, 3.5, 3)
    assert m.drones["d1"].active == LORA

    # Wi-Fi recovers: not instantly (hold time), then straight back to Wi-Fi (skips degraded LTE).
    m.environment.restore("d1", WIFI, now=8.0)
    run(m, 6.5, 3)  # t = 6.5 .. 9.0: only ~1 s of good Wi-Fi so far
    assert m.drones["d1"].active == LORA
    events, _ = run(m, 9.5, 3)  # >= 3 s good
    assert m.drones["d1"].active == WIFI
    recovery = next(e for e in events if e["kind"] == "recovery")
    assert (recovery["from"], recovery["to"]) == ("lora", "wifi")


def test_hysteresis_prevents_flapping():
    m = manager()
    run(m, 0.0, 3)
    switches = 0
    t = 3.0
    for k in range(40):  # 20 s of RSSI oscillating across the breach line every poll
        m.environment.degrade("d1", WIFI, now=t, rssi_dbm=-80 if k % 2 == 0 else -72)
        switches += sum(1 for e in m.poll_once(t) if e["kind"] in ("fallback", "recovery"))
        t += 0.5
    assert switches == 1  # one fallback to LTE; -72 dBm is never GOOD (recover line -67), so no bounce back
    assert m.drones["d1"].active == LTE


def test_all_links_lost_raises_alert_and_keeps_transport():
    m = manager()
    run(m, 0.0, 2)
    for link in CHAIN:
        m.environment.degrade("d1", link, now=2.0, available=False)
    events = m.poll_once(2.0)
    assert [e["kind"] for e in events if e["drone_id"] == "d1"][-1] == "all_links_lost"
    assert m.drones["d1"].all_lost


@pytest.mark.asyncio
async def test_transports_really_switch_and_lora_frames_are_compact():
    tag = uuid.uuid4().hex[:8]
    bus = DroneBus(settings.NATS_URL, stream=f"CQA_{tag}", prefix=f"cqa{tag}")
    await bus.connect()
    await bus.ensure_topology()
    try:
        m = manager()
        m.bus = bus
        m.poll_once(0.0)
        telemetry = {"drone_id": "d1", "uin": "DRN-01-000001", "timestamp": "2026-10-06T10:00:00+00:00", "lat": 23.215612345678,
                     "lon": 72.63691234567, "alt": 50.123, "speed": 6.04, "heading": 91.27, "soc": 80.555, "flight_mode": "MISSION", "armed": True}
        await m.send("d1", "DRN-01-000001", "telemetry", telemetry)
        m.environment.degrade("d1", WIFI, now=1.0, rssi_dbm=-85)
        m.environment.degrade("d1", LTE, now=1.0, rssi_dbm=-115)
        m.poll_once(1.0)
        assert m.drones["d1"].active == LORA
        await m.send("d1", "DRN-01-000001", "telemetry", telemetry)
        msgs = await bus.replay(kind="telemetry")
        assert [msg.link for msg in msgs] == ["wifi", "lora"]  # Dm-Link header set by the active transport
        wifi_frame, lora_frame = msgs[0].payload, msgs[1].payload
        assert "flight_mode" in wifi_frame and "flight_mode" not in lora_frame
        assert lora_frame["lat"] == 23.215612 and lora_frame["link_type"] == "lora"
        assert m.transports[WIFI].stats["messages"] == 1 and m.transports[LORA].stats["messages"] == 1
    finally:
        await bus.delete_stream()
        await bus.close()


@pytest.mark.asyncio
async def test_fallback_within_one_second_real_time():
    """Real 2 Hz loop, wall-clock timestamps: injected RSSI drop -> detection
    -> decision -> transport activation, for Wi-Fi->LTE and LTE->LoRa."""
    m = CommunicationManager()
    m.register("d1", "DRN-01-000001", "DMX-001")
    await m.start()
    try:
        await asyncio.sleep(1.2)
        results = []
        for link, rssi, expected in ((WIFI, -84.0, LTE), (LTE, -114.0, LORA)):
            m.environment.degrade("d1", link, now=time.time(), rssi_dbm=rssi)
            deadline = time.time() + 3
            while m.drones["d1"].active != expected and time.time() < deadline:
                await asyncio.sleep(0.01)
            assert m.drones["d1"].active == expected
            results.append(m.switches[-1])
            await asyncio.sleep(0.6)
        timing = m.timing()
    finally:
        await m.stop()
    for r in results:
        print(f"\n[fallback] {r['from']}->{r['to']} detect={r['detect_ms']}ms decision={r['decision_ms']}ms "
              f"activation={r['activation_ms']}ms total={r['total_ms']}ms")
        assert r["total_ms"] < 1000
    assert 1.8 <= timing["measured_hz"] <= 2.2  # really polling at 2 Hz
