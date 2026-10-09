"""Sprint 4 -- NATS JetStream tests against the REAL NATS server
(docker compose service `nats`). Each test uses its own stream + subject
prefix so it never touches the live `DRONES` stream.

Nothing here is mocked: messages travel publisher -> NATS server ->
JetStream stream -> durable consumer -> handler.
"""
import asyncio
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.messaging import subjects
from app.messaging.bus import DroneBus, NatsUnavailableError

settings = get_settings()
UINS = [f"DRN-01-2109{n:02d}" for n in range(16)]


@pytest_asyncio.fixture()
async def bus_pair():
    """Publisher + consumer on separate connections, isolated stream."""
    tag = uuid.uuid4().hex[:8]
    pub = DroneBus(settings.NATS_URL, stream=f"QA_{tag}", prefix=f"qa{tag}")
    sub = DroneBus(settings.NATS_URL, stream=f"QA_{tag}", prefix=f"qa{tag}")
    await pub.connect()
    await sub.connect()
    await pub.ensure_topology()
    yield pub, sub
    await pub.delete_stream()
    await sub.close()
    await pub.close()


async def collect(bus: DroneBus, durable: str, expected: int, timeout: float = 5.0, ack: bool = True) -> list:
    received: list = []
    done = asyncio.Event()

    async def handler(msg):
        received.append(msg)
        if ack:
            await msg.ack()
        if len(received) >= expected:
            done.set()

    await bus.subscribe(durable, handler)
    try:
        await asyncio.wait_for(done.wait(), timeout)
    finally:
        await bus.unsubscribe(durable)
    return received


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,durable", [("telemetry", "telemetry-writer"), ("state", "ui"), ("alert", "ui"), ("command", "command-dispatch")])
async def test_publish_and_consume_each_kind(bus_pair, kind, durable):
    pub, sub = bus_pair
    ack = await pub.publish(UINS[0], kind, {"kind": kind, "value": 42}, link="wifi")
    assert ack.stream == pub.stream and ack.seq >= 1
    [msg] = await collect(sub, durable, 1)
    assert msg.subject == f"{pub.prefix}.drone.{UINS[0]}.{kind}"
    assert (msg.uin, msg.kind, msg.payload, msg.link) == (UINS[0], kind, {"kind": kind, "value": 42}, "wifi")
    assert msg.as_dict()["topic"] == f"drone/{UINS[0]}/{kind}"
    assert msg.latency_ms is not None and msg.latency_ms >= 0


@pytest.mark.asyncio
async def test_consumer_filters_route_kinds_correctly(bus_pair):
    pub, sub = bus_pair
    for kind in subjects.KINDS:
        await pub.publish(UINS[1], kind, {"k": kind})
    orchestrator = await collect(sub, "orchestrator", 4)
    assert sorted(m.kind for m in orchestrator) == sorted(subjects.KINDS)
    commands = await collect(sub, "command-dispatch", 1)
    assert [m.kind for m in commands] == ["command"]


@pytest.mark.asyncio
async def test_multiple_drones(bus_pair):
    pub, sub = bus_pair
    for i, uin in enumerate(UINS):
        await pub.publish(uin, "telemetry", {"i": i})
    msgs = await collect(sub, "telemetry-writer", 16)
    assert sorted(m.uin for m in msgs) == sorted(UINS)
    assert sorted(m.payload["i"] for m in msgs) == list(range(16))


@pytest.mark.asyncio
async def test_multiple_consumers_each_get_every_message(bus_pair):
    pub, sub = bus_pair
    for i in range(10):
        await pub.publish(UINS[i % 4], "telemetry", {"i": i})
    writer, orchestrator = await asyncio.gather(collect(sub, "telemetry-writer", 10), collect(sub, "orchestrator", 10))
    assert sorted(m.payload["i"] for m in writer) == list(range(10))
    assert sorted(m.payload["i"] for m in orchestrator) == list(range(10))


@pytest.mark.asyncio
async def test_queue_group_shares_load_between_instances(bus_pair):
    """Two orchestrator instances in one consumer group split the stream, no duplicates."""
    pub, sub = bus_pair
    other = DroneBus(settings.NATS_URL, stream=pub.stream, prefix=pub.prefix)
    await other.connect()
    got_a, got_b = [], []

    async def a(m):
        got_a.append(m.payload["i"])
        await m.ack()

    async def b(m):
        got_b.append(m.payload["i"])
        await m.ack()

    await sub.subscribe("orchestrator", a)
    await other.subscribe("orchestrator", b)
    for i in range(60):
        await pub.publish(UINS[i % 16], "state", {"i": i})
    deadline = time.monotonic() + 5
    while len(got_a) + len(got_b) < 60 and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    await other.close()
    assert sorted(got_a + got_b) == list(range(60))
    assert got_a and got_b  # both instances did work


@pytest.mark.asyncio
async def test_consumer_restart_resumes_without_loss(bus_pair):
    pub, sub = bus_pair
    for i in range(5):
        await pub.publish(UINS[2], "telemetry", {"i": i})
    first = await collect(sub, "telemetry-writer", 5)
    assert [m.payload["i"] for m in first] == list(range(5))
    # Consumer process "crashes" (connection gone) while drones keep publishing.
    await sub.close()
    for i in range(5, 12):
        await pub.publish(UINS[2], "telemetry", {"i": i})
    restarted = DroneBus(settings.NATS_URL, stream=pub.stream, prefix=pub.prefix)
    await restarted.connect()
    try:
        resumed = await collect(restarted, "telemetry-writer", 7)
    finally:
        await restarted.close()
    assert [m.payload["i"] for m in resumed] == list(range(5, 12))  # nothing lost, nothing repeated


@pytest.mark.asyncio
async def test_unacked_message_is_redelivered(bus_pair):
    pub, sub = bus_pair
    await pub.publish(UINS[3], "alert", {"level": "critical"})
    calls = []

    async def failing(m):
        calls.append(m.num_delivered)
        if len(calls) == 1:
            raise RuntimeError("handler crashed")  # bus NAKs -> redelivery
        await m.ack()

    await sub.subscribe("ui", failing)
    deadline = time.monotonic() + 6
    while len(calls) < 2 and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    await sub.unsubscribe("ui")
    assert calls[:2] == [1, 2]


@pytest.mark.asyncio
async def test_jetstream_replay_after_consumer_stop(bus_pair):
    pub, sub = bus_pair
    start = datetime.now(timezone.utc) - timedelta(seconds=1)
    for i in range(10):
        await pub.publish(UINS[4], "telemetry", {"i": i})
    await collect(sub, "telemetry-writer", 10)
    await sub.close()  # consumer stops
    for i in range(10, 15):
        await pub.publish(UINS[4], "telemetry", {"i": i})
    replayer = DroneBus(settings.NATS_URL, stream=pub.stream, prefix=pub.prefix)
    await replayer.connect()
    try:
        replayed = await replayer.replay(kind="telemetry", uin=UINS[4], start_time=start)
        from_seq = await replayer.replay(kind="telemetry", start_seq=replayed[10].stream_seq)
    finally:
        await replayer.close()
    assert [m.payload["i"] for m in replayed] == list(range(15))  # all history, in order
    seqs = [m.stream_seq for m in replayed]
    assert seqs == sorted(seqs) and len(set(seqs)) == 15
    assert all(m.published_ns and m.msg_id for m in replayed)  # metadata preserved
    assert [m.payload["i"] for m in from_seq] == list(range(10, 15))
    info = await pub.js.stream_info(pub.stream)
    assert info.config.max_age == 24 * 3600  # retention == 24 h replay window


@pytest.mark.asyncio
async def test_duplicate_message_ids_are_rejected_by_jetstream(bus_pair):
    pub, sub = bus_pair
    first = await pub.publish(UINS[5], "telemetry", {"v": 1}, msg_id="dup-1")
    second = await pub.publish(UINS[5], "telemetry", {"v": 1}, msg_id="dup-1")
    assert not first.duplicate and second.duplicate and second.seq == first.seq
    msgs = await collect(sub, "telemetry-writer", 1)
    await asyncio.sleep(0.3)
    assert len(msgs) == 1
    info = await pub.js.stream_info(pub.stream)
    assert info.state.messages == 1


@pytest.mark.parametrize("uin,kind", [("DRN.01", "telemetry"), ("DRN 01", "state"), ("DRN-*", "alert"), ("", "command"), ("DRN-01", "status"), ("DRN->", "telemetry")])
def test_invalid_subject_rejected(uin, kind):
    with pytest.raises(subjects.InvalidSubjectError):
        subjects.subject(uin, kind)


@pytest.mark.asyncio
async def test_invalid_subject_never_reaches_nats(bus_pair):
    pub, _ = bus_pair
    with pytest.raises(subjects.InvalidSubjectError):
        await pub.publish("bad.uin", "telemetry", {})
    info = await pub.js.stream_info(pub.stream)
    assert info.state.messages == 0


@pytest.mark.asyncio
async def test_nats_unavailable_raises_clean_error():
    bus = DroneBus("nats://127.0.0.1:4999", stream="NOPE", connect_timeout_s=0.5)
    with pytest.raises(NatsUnavailableError):
        await bus.connect()
    with pytest.raises(NatsUnavailableError):
        await bus.publish(UINS[0], "telemetry", {})
    status = await bus.status()
    assert status["connected"] is False


@pytest.mark.asyncio
async def test_delivery_latency_under_50ms(bus_pair):
    """16 drones x 10 msg/s through the real server; latency = consumer receive
    time - publisher timestamp header (both wall clock, same host)."""
    pub, sub = bus_pair
    latencies: list[float] = []

    async def handler(m):
        latencies.append(m.latency_ms)
        await m.ack()

    await sub.subscribe("orchestrator", handler)
    total = 16 * 10 * 3  # 3 s of traffic
    for i in range(total):
        await pub.publish(UINS[i % 16], "telemetry" if i % 2 else "state", {"i": i})
        if i % 16 == 15:
            await asyncio.sleep(0.1)
    deadline = time.monotonic() + 5
    while len(latencies) < total and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    await sub.unsubscribe("orchestrator")
    ordered = sorted(latencies)
    p99 = ordered[int(0.99 * len(ordered)) - 1]
    print(f"\n[nats] n={len(ordered)} avg={statistics.fmean(ordered):.2f}ms p99={p99:.2f}ms max={ordered[-1]:.2f}ms")
    assert len(ordered) == total
    assert sum(1 for x in ordered if x > 50) == 0
