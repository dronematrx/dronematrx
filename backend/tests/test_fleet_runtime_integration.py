"""Sprint 4 backend end-to-end: the real FleetRuntime (simulator at 10 Hz,
ORCA, comms manager, NATS JetStream on an isolated stream, TimescaleDB on
the test database) running the deterministic 8-drone demo scenario.

Checks, from the outside:
* ORCA predicts the scripted conflicts >= 3 s ahead and keeps separation
* ORCA state and alerts travel through NATS to the UI consumer
* telemetry travels through NATS into the hypertable at 1 Hz per drone
* a degraded link falls back (Wi-Fi -> LTE) in < 1 s and telemetry continues
"""
import asyncio
import math
from datetime import datetime
import time
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.geo import geojson_to_geography, point_to_geojson
from app.models import Drone, Organization
from app.services.comms.links import LinkType
from app.services.runtime import FleetRuntime
from app.websocket.connection_manager import ConnectionManager


@pytest.fixture()
def demo_fleet(engine):
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        org = Organization(name="Runtime QA", slug=f"rt-qa-{uuid.uuid4().hex[:8]}")
        s.add(org)
        s.flush()
        for n in range(1, 9):
            s.add(Drone(organization_id=org.id, name=f"DMX-{n:03d}", uin=f"RTQA-{uuid.uuid4().hex[:6]}-{n}", model="M30",
                        status="active", battery=92,
                        # Distinct parking spots ~45 m apart (co-located drones would be a real conflict).
                        location=geojson_to_geography(point_to_geojson(72.6369 + 0.00045 * n, 23.2140)),
                        max_speed=15, endurance_minutes=30))
        s.commit()
        org_id = org.id
    yield Session
    with Session() as s:
        s.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": org_id})
        s.commit()


@pytest.mark.asyncio
async def test_demo_scenario_through_the_whole_stack(demo_fleet):
    tag = uuid.uuid4().hex[:8]
    settings = Settings(NATS_STREAM=f"RTQA_{tag}", NATS_SUBJECT_PREFIX=f"rtqa{tag}", ORCA_ENABLED=True)
    runtime = FleetRuntime(settings, demo_fleet, ConnectionManager())
    await runtime.start()
    try:
        assert runtime.nats_ok
        started = time.time()
        await runtime.demo.start()
        names = {s.drone_id: s.name for s in runtime.simulator.states}

        min_sep = math.inf
        while time.time() - started < 22:  # past both conflicts (~T+20)
            await asyncio.sleep(0.2)
            states = [s for s in runtime.simulator.states if s.band == 1]
            for i, a in enumerate(states):
                for b in states[i + 1:]:
                    ax, ay = runtime.orca.frame.to_local(a.lat, a.lon)
                    bx, by = runtime.orca.frame.to_local(b.lat, b.lon)
                    min_sep = min(min_sep, math.hypot(bx - ax, by - ay))
        # The demo script degrades DMX-003's Wi-Fi at T+20: wait for the switch
        # and use the manager's own recorded breach -> activation timestamps.
        drone3 = next(d for d, n in names.items() if n == "DMX-003")
        while runtime.comms.drones[drone3].active != LinkType.LTE and time.time() - started < 26:
            await asyncio.sleep(0.05)
        switch = next(sw for sw in runtime.comms.switches if sw["name"] == "DMX-003" and sw["from"] == "wifi" and sw["to"] == "lte")
        fallback_ms = switch["total_ms"]
        await asyncio.sleep(3)  # telemetry continues over LTE

        # Only what happened after the scenario placed the drones.
        events = [e for e in runtime.orca.engine.events if datetime.fromisoformat(e.wall_time).timestamp() >= started]
        predicted = [e for e in events if e.kind == "conflict_predicted"]
        pairs = {tuple(sorted(names[d] for d in e.drones)) for e in predicted}
        alerts = [a for a in runtime.alerts if a.get("type") == "orca"]
        timing = runtime.orca.timing()
        with demo_fleet() as s:
            rows = s.execute(text("SELECT d.name, count(*), count(*) FILTER (WHERE t.link_type = 'lte') FROM drone_telemetry t "
                                  "JOIN drones d ON d.id = t.drone_id GROUP BY d.name ORDER BY d.name")).all()
        print(f"\n[runtime-e2e] pairs={sorted(pairs)} min_band1_sep={min_sep:.2f}m orca={timing['measured_hz']}Hz "
              f"missed={timing['missed_cycles']} fallback={fallback_ms:.0f}ms rows={rows}")

        assert {("DMX-001", "DMX-002"), ("DMX-003", "DMX-004")} <= pairs
        assert all(e.details["time_to_collision_s"] >= 3.0 for e in predicted if tuple(sorted(names[d] for d in e.drones)) in
                   {("DMX-001", "DMX-002"), ("DMX-003", "DMX-004")})
        assert any(e.kind == "avoidance_applied" for e in events)
        assert min_sep >= 6.0 - 0.15  # avoidance envelope held in the live 10 Hz loop
        assert alerts, "ORCA events must reach the UI consumer as NATS alerts"
        assert any(st.get("via") == "nats" for st in runtime.orca_view()["drones"])  # state delivered over NATS
        assert timing["missed_cycles"] <= 2 and 9.5 <= timing["measured_hz"] <= 10.5
        assert fallback_ms is not None and 0 < fallback_ms < 1000
        counts = {name: (n, lte) for name, n, lte in rows}
        assert len(counts) == 8 and all(n >= 20 for n, _ in counts.values())  # ~1 Hz each for ~25 s
        assert counts["DMX-003"][1] >= 2  # rows that arrived over LTE after the fallback
        assert runtime.ingestor.stats["rejected"] == 0 and runtime.ingestor.stats["write_errors"] == 0
    finally:
        await runtime.stop()
        cleanup = runtime.bus.__class__(settings.NATS_URL, settings.NATS_STREAM, settings.NATS_SUBJECT_PREFIX)
        await cleanup.connect()
        await cleanup.delete_stream()
        await cleanup.close()
