"""Sprint 4 -- TimescaleDB telemetry: hypertable, validation/integrity,
16-drone 1 Hz ingestion THROUGH NATS, and 1-hour query latency.

These tests write committed rows (the ingestor runs on its own DB
connections), so they use their own organization/drones and delete them --
cascading to their telemetry -- at the end.
"""
import asyncio
import math
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.geo import geojson_to_geography, point_to_geojson
from app.messaging.bus import DroneBus
from app.models import Drone, Organization
from app.services.telemetry.ingestor import TelemetryIngestor
from app.services.telemetry.store import InvalidTelemetryError, TelemetryStore, parse_record

settings = get_settings()
QUERY_LIMIT_MS = 500.0


@pytest.fixture()
def committed_fleet(engine):
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        org = Organization(name="TS QA", slug=f"ts-qa-{uuid.uuid4().hex[:8]}")
        s.add(org)
        s.flush()
        drones = []
        for n in range(16):
            d = Drone(
                organization_id=org.id, name=f"TS-{n:02d}", uin=f"TSQA-{uuid.uuid4().hex[:6]}-{n:02d}", model="M30",
                status="active", battery=90, location=geojson_to_geography(point_to_geojson(72.63, 23.21)),
            )
            s.add(d)
            drones.append(d)
        s.commit()
        info = [(d.id, d.uin) for d in drones]
        org_id = org.id
    yield Session, info
    with Session() as s:
        s.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": org_id})
        s.commit()


def payload(drone_id, ts, **overrides):
    base = {
        "drone_id": str(drone_id), "timestamp": ts.isoformat(), "lat": 23.2156, "lon": 72.6369, "alt": 50.0,
        "speed": 8.5, "heading": 123.0, "soc": 77.5, "rssi": -58.0, "link_type": "wifi",
    }
    base.update(overrides)
    return base


def test_hypertable_configured(engine):
    with engine.connect() as conn:
        hyper = conn.execute(text("SELECT hypertable_name FROM timescaledb_information.hypertables")).scalars().all()
        dims = conn.execute(text("SELECT column_name, time_interval FROM timescaledb_information.dimensions WHERE hypertable_name='drone_telemetry'")).all()
        cols = dict(conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='drone_telemetry'")).all())
    assert "drone_telemetry" in hyper
    assert dims == [("timestamp", timedelta(days=1))]
    for col, typ in {"timestamp": "timestamp with time zone", "drone_id": "uuid", "lat": "double precision", "lon": "double precision",
                     "alt": "real", "speed": "real", "heading": "real", "soc": "real", "rssi": "real"}.items():
        assert cols[col] == typ, col


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"lat": 91.0}, "lat"), ({"lon": -181.0}, "lon"), ({"lat": math.nan}, "finite"), ({"soc": 101.0}, "soc"),
        ({"rssi": 5.0}, "rssi"), ({"speed": -1.0}, "speed"), ({"heading": "north"}, "heading"),
        ({"drone_id": "not-a-uuid"}, "UUID"), ({"timestamp": "yesterday"}, "ISO"), ({"timestamp": "2026-10-06T10:00:00"}, "timezone"),
        ({"link_type": "satcom"}, "link_type"), ({"alt": None}, "missing"), ({"soc": True}, "soc"),
    ],
)
def test_malformed_records_rejected(overrides, reason):
    record = payload(uuid.uuid4(), datetime.now(timezone.utc))
    record.update(overrides)
    with pytest.raises(InvalidTelemetryError, match=reason):
        parse_record(record)


def test_unknown_drone_and_future_timestamps_rejected():
    known = {uuid.uuid4()}
    with pytest.raises(InvalidTelemetryError, match="unknown drone"):
        parse_record(payload(uuid.uuid4(), datetime.now(timezone.utc)), known)
    with pytest.raises(InvalidTelemetryError, match="future"):
        parse_record(payload(next(iter(known)), datetime.now(timezone.utc) + timedelta(hours=1)), known)


def test_integrity_duplicates_delayed_out_of_order(committed_fleet):
    Session, fleet = committed_fleet
    store = TelemetryStore(Session)
    drone_id = fleet[0][0]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    in_order = [payload(drone_id, now - timedelta(seconds=s), speed=float(s)) for s in (5, 4, 3)]
    result = store.ingest(in_order)
    assert result == {"inserted": 3, "duplicates": 0, "rejected": 0, "reasons": []}
    # duplicate (redelivery), delayed (10 min late), out-of-order (older than what we have)
    late = [payload(drone_id, now - timedelta(seconds=4)), payload(drone_id, now - timedelta(minutes=10)), payload(drone_id, now - timedelta(seconds=30))]
    bad = [payload(drone_id, now, lat=200.0), payload(uuid.uuid4(), now), {"drone_id": str(drone_id)}]
    result = store.ingest(late + bad)
    assert (result["inserted"], result["duplicates"], result["rejected"]) == (2, 1, 3)
    with Session() as s:
        rows, _ = store.drone_window(s, drone_id, now - timedelta(hours=1), now + timedelta(seconds=1))
    assert [r["timestamp"] for r in rows] == sorted(r["timestamp"] for r in rows)  # stored/returned in time order
    assert len(rows) == 5
    stored = rows[-1]
    assert (stored["lat"], stored["lon"], stored["alt"], stored["heading"], stored["soc"], stored["rssi"], stored["link_type"]) == (
        23.2156, 72.6369, 50.0, 123.0, 77.5, -58.0, "wifi",
    )
    assert stored["drone_id"] == drone_id


def test_database_constraints_are_second_line_of_defence(committed_fleet):
    Session, fleet = committed_fleet
    with Session() as s, pytest.raises(Exception):
        s.execute(
            text("INSERT INTO drone_telemetry (timestamp, drone_id, lat, lon, alt, speed, heading, soc) VALUES (now(), :d, 95, 0, 1, 1, 1, 1)"),
            {"d": fleet[0][0]},
        )
        s.commit()


@pytest_asyncio.fixture()
async def nats_bus():
    tag = uuid.uuid4().hex[:8]
    pub = DroneBus(settings.NATS_URL, stream=f"TSQA_{tag}", prefix=f"tsqa{tag}")
    sub = DroneBus(settings.NATS_URL, stream=f"TSQA_{tag}", prefix=f"tsqa{tag}")
    await pub.connect()
    await sub.connect()
    await pub.ensure_topology()
    yield pub, sub
    await pub.delete_stream()
    await sub.close()
    await pub.close()


@pytest.mark.asyncio
async def test_sixteen_drones_one_hz_through_nats_without_loss(committed_fleet, nats_bus):
    """16 drones x 1 Hz for 20 s in real time: publisher -> NATS JetStream ->
    telemetry-writer consumer -> TimescaleDB. Every row must land."""
    Session, fleet = committed_fleet
    pub, sub = nats_bus
    store = TelemetryStore(Session)
    ingestor = TelemetryIngestor(sub, store)
    await ingestor.start()
    seconds = 20
    start = datetime.now(timezone.utc).replace(microsecond=0)
    tick0 = time.monotonic()
    for second in range(seconds):
        ts = start + timedelta(seconds=second)
        for drone_id, uin in fleet:
            await pub.publish(uin, "telemetry", payload(drone_id, ts, soc=90 - second * 0.1), msg_id=f"{uin}-{second}", link="wifi")
        await asyncio.sleep(max(0.0, tick0 + second + 1 - time.monotonic()))  # real 1 Hz cadence
    deadline = time.monotonic() + 5
    while ingestor.stats["inserted"] < seconds * 16 and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    await ingestor.stop()

    with Session() as s:
        rows = s.execute(text("SELECT drone_id, count(*) FROM drone_telemetry WHERE drone_id = ANY(:ids) GROUP BY drone_id"),
                         {"ids": [d for d, _ in fleet]}).all()
    per_drone = {d: n for d, n in rows}
    snap = ingestor.snapshot()
    print(f"\n[timescale-16] expected={seconds * 16} inserted={snap['inserted']} rejected={snap['rejected']} "
          f"write_errors={snap['write_errors']} ingest_latency avg={snap['ingest_latency_ms_avg']}ms p95={snap['ingest_latency_ms_p95']}ms")
    assert len(per_drone) == 16 and all(n == seconds for n in per_drone.values())
    assert snap["inserted"] == seconds * 16 and snap["rejected"] == 0 and snap["write_errors"] == 0


def test_one_hour_window_query_under_500ms(committed_fleet):
    Session, fleet = committed_fleet
    store = TelemetryStore(Session)
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(hours=1)
    # 1 Hz x 1 h x 16 drones = 57,600 rows (bulk-loaded; ingestion path is tested above).
    for drone_id, _ in fleet:
        records = [parse_record(payload(drone_id, start + timedelta(seconds=s), heading=float(s % 360))) for s in range(3600)]
        store.write_batch(records)
    with Session() as s:
        s.execute(text("ANALYZE drone_telemetry"))
        timings = []
        for _ in range(5):
            rows, ms = store.drone_window(s, fleet[3][0], start, end + timedelta(seconds=1))
            timings.append(ms)
        fleet_rows, fleet_ms = store.fleet_window(s, start, end + timedelta(seconds=1), [d for d, _ in fleet])
        plan = "\n".join(store.explain(s, fleet[3][0], start, end))
    print(f"\n[timescale-query] drone rows={len(rows)} median={statistics.median(timings):.1f}ms max={max(timings):.1f}ms "
          f"fleet rows={len(fleet_rows)} {fleet_ms:.1f}ms")
    assert len(rows) == 3600
    assert len(fleet_rows) == 57_600
    assert max(timings) < QUERY_LIMIT_MS
    assert fleet_ms < QUERY_LIMIT_MS
    assert "Index" in plan and "_hyper_" in plan  # chunk index scans, not a sequential table scan


@pytest.mark.asyncio
async def test_telemetry_from_newly_registered_drone_is_not_lost(committed_fleet, nats_bus, engine):
    """Regression: the known-drones cache must not reject a drone created after it was filled."""
    Session, fleet = committed_fleet
    pub, sub = nats_bus
    store = TelemetryStore(Session)
    ingestor = TelemetryIngestor(sub, store)
    await ingestor.start()
    store.known_drones()  # warm the cache BEFORE the new drone exists
    with Session() as s:
        org_id = s.execute(text("SELECT organization_id FROM drones WHERE id = :d"), {"d": fleet[0][0]}).scalar()
        late = Drone(organization_id=org_id, name="LATE-1", uin=f"LATE-{uuid.uuid4().hex[:6]}", model="M30", status="active", battery=90)
        s.add(late)
        s.commit()
        late_id, late_uin = late.id, late.uin
    now = datetime.now(timezone.utc).replace(microsecond=0)
    await pub.publish(late_uin, "telemetry", payload(late_id, now))
    deadline = time.monotonic() + 5
    while ingestor.stats["inserted"] < 1 and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    await ingestor.stop()
    assert ingestor.stats["inserted"] == 1 and ingestor.stats["rejected"] == 0
