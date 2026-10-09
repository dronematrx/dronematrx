"""Sprint 4 benchmark suite -> docs/sprint4/performance/*.json

Measures the REAL systems (no mocks): the ORCA engine, the NATS JetStream
server, the communication manager's 2 Hz loop, and TimescaleDB.

Run from backend/ with docker compose up (Postgres+TimescaleDB, NATS):

    python scripts/benchmark_sprint4.py               # everything
    python scripts/benchmark_sprint4.py orca nats     # selected sections

NATS and TimescaleDB sections use an isolated stream / benchmark
organization and clean up after themselves.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.geo import geojson_to_geography, point_to_geojson  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.messaging.bus import DroneBus  # noqa: E402
from app.models import Drone, Organization  # noqa: E402
from app.services.comms.links import LinkType  # noqa: E402
from app.services.comms.manager import CommunicationManager  # noqa: E402
from app.services.orca.models import OrcaParams  # noqa: E402
from app.services.orca.scenarios import eight_drone_run  # noqa: E402
from app.services.orca.simulation import OrcaSimulation, percentile  # noqa: E402
from app.services.telemetry.ingestor import TelemetryIngestor  # noqa: E402
from app.services.telemetry.store import TelemetryStore, parse_record  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "docs" / "sprint4" / "performance"
settings = get_settings()


def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "avg": round(statistics.fmean(values), 4),
        "p50": round(percentile(values, 50), 4),
        "p95": round(percentile(values, 95), 4),
        "p99": round(percentile(values, 99), 4),
        "max": round(max(values), 4),
        "min": round(min(values), 4),
    }


def write(name: str, data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = {"generated_at": datetime.now(timezone.utc).isoformat(), **data}
    (OUT / name).write_text(json.dumps(data, indent=2, default=str))
    print(f"  -> {OUT / name}")


# ------------------------------------------------------------------ ORCA


async def bench_orca() -> dict:
    print("[orca] 50 consecutive 8-drone runs ...")
    params = OrcaParams()
    runs, step_ms, resolution_ms, delays = [], [], [], []
    for i in range(50):
        family, agents = eight_drone_run(i)
        t0 = time.perf_counter()
        rep = OrcaSimulation(agents, params).run(90)
        s = rep.summary()
        runs.append({"run": i, "family": family, "wall_s": round(time.perf_counter() - t0, 3), **{k: s[k] for k in (
            "sim_time_s", "min_separation_m", "collision_violations", "envelope_breaches", "predicted_conflicts",
            "avoidance_events", "arrived", "step_ms_max", "resolution_ms_max")}})
        step_ms += rep.step_ms
        resolution_ms += rep.resolution_ms
        delays += rep.resolution_sim_delay_s

    print("[orca] real-time 10 Hz loop, 8 drones, 30 s ...")
    _, agents = eight_drone_run(0)
    sim = OrcaSimulation(agents, params)
    period = params.timestep_s
    stamps, compute, missed = [], [], 0
    next_tick = time.monotonic()
    end = next_tick + 30
    while time.monotonic() < end:
        t = time.monotonic()
        if stamps and t - stamps[-1] > 1.5 * period:
            missed += 1
        stamps.append(t)
        c0 = time.perf_counter()
        sim.step()
        compute.append((time.perf_counter() - c0) * 1000)
        if all(a.arrived for a in sim.agents):  # keep traffic flowing for the whole window
            _, agents = eight_drone_run(len(stamps) % 50)
            sim = OrcaSimulation(agents, params)
        next_tick += period
        await asyncio.sleep(max(0.0, next_tick - time.monotonic()))
    intervals = [(b - a) * 1000 for a, b in zip(stamps, stamps[1:])]

    result = {
        "params": params.__dict__,
        "fifty_runs": {
            "runs": 50,
            "drone_runs": 400,
            "collision_violations": sum(r["collision_violations"] for r in runs),
            "envelope_breaches": sum(r["envelope_breaches"] for r in runs),
            "min_separation_m": min(r["min_separation_m"] for r in runs),
            "predicted_conflicts": sum(r["predicted_conflicts"] for r in runs),
            "avoidance_events": sum(r["avoidance_events"] for r in runs),
            "arrived": sum(r["arrived"] for r in runs),
            "per_run": runs,
        },
        "cycle_compute_ms_8_drones": stats(step_ms),
        "resolution_ms": stats(resolution_ms),
        "prediction_to_avoidance_sim_s": stats(delays),
        "realtime_10hz": {
            "duration_s": 30,
            "target_hz": 1 / period,
            "measured_hz": round(1000 / statistics.fmean(intervals), 3),
            "cycles": len(stamps),
            "missed_cycles": missed,
            "interval_ms": stats(intervals),
            "compute_ms": stats(compute),
        },
        "acceptance": {"zero_collisions": sum(r["collision_violations"] for r in runs) == 0, "resolution_under_500ms": max(resolution_ms) < 500},
    }
    write("orca_benchmark.json", result)
    return result


# ------------------------------------------------------------------ NATS


async def _bus_pair(tag: str) -> tuple[DroneBus, DroneBus]:
    pub = DroneBus(settings.NATS_URL, stream=f"BENCH_{tag}", prefix=f"bench{tag}")
    sub = DroneBus(settings.NATS_URL, stream=f"BENCH_{tag}", prefix=f"bench{tag}")
    await pub.connect()
    await sub.connect()
    await pub.ensure_topology()
    return pub, sub


async def bench_nats() -> dict:
    tag = uuid.uuid4().hex[:8]
    pub, sub = await _bus_pair(tag)
    uins = [f"DRN-BENCH-{n:02d}" for n in range(16)]
    try:
        print("[nats] 16 drones x 10 msg/s for 20 s ...")
        latencies: list[float] = []
        seen: set[str] = set()
        dupes = 0

        async def handler(m):
            nonlocal dupes
            if m.msg_id in seen:
                dupes += 1
            seen.add(m.msg_id)
            latencies.append(m.latency_ms)
            await m.ack()

        await sub.subscribe("orchestrator", handler)
        sent = 0
        kinds = ("telemetry", "state", "state", "state", "state", "state", "state", "state", "state", "alert")
        t0 = time.monotonic()
        for tick in range(200):  # 20 s at 10 Hz
            for i, uin in enumerate(uins):
                await pub.publish(uin, kinds[tick % 10], {"tick": tick, "drone": i})
                sent += 1
            await asyncio.sleep(max(0.0, t0 + (tick + 1) * 0.1 - time.monotonic()))
        deadline = time.monotonic() + 5
        while len(latencies) < sent and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await sub.unsubscribe("orchestrator")

        print("[nats] JetStream replay: publish, stop consumer, publish more, replay ...")
        replay_uin = "DRN-REPLAY-01"  # isolated from the delivery phase above
        await asyncio.sleep(0.2)
        start = datetime.now(timezone.utc)
        for i in range(100):
            await pub.publish(replay_uin, "telemetry", {"phase": "before_stop", "i": i})
        before: list = []

        async def collect(m):
            before.append(m)
            await m.ack()

        await sub.subscribe("telemetry-writer", collect)
        await asyncio.sleep(1.0)
        await sub.unsubscribe("telemetry-writer")
        for i in range(50):
            await pub.publish(replay_uin, "telemetry", {"phase": "while_stopped", "i": i})
        r0 = time.perf_counter()
        replayed = await pub.replay(kind="telemetry", uin=replay_uin, start_time=start, limit=10_000)
        replay_ms = (time.perf_counter() - r0) * 1000
        resumed: list = []
        await sub.subscribe("telemetry-writer", collect_after := (lambda m: _ack_into(resumed, m)))
        deadline = time.monotonic() + 5
        while len(resumed) < 50 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        seqs = [m.stream_seq for m in replayed]
        info = await pub.js.stream_info(pub.stream)
        _ = collect_after

        result = {
            "server": settings.NATS_URL,
            "stream_config": {"max_age_hours": info.config.max_age / 3600, "retention": str(info.config.retention), "storage": str(info.config.storage),
                              "duplicate_window_s": info.config.duplicate_window, "subjects": info.config.subjects},
            "delivery": {
                "pattern": "16 drones x 10 msg/s (telemetry/state/alert), publisher and consumer on separate connections",
                "messages_sent": sent,
                "messages_received": len(latencies),
                "lost": sent - len(seen),
                "duplicates_delivered": dupes,
                "latency_ms": stats(latencies),
                "over_50ms": sum(1 for x in latencies if x > 50),
            },
            "replay": {
                "published_before_stop": 100,
                "consumed_before_stop": len([m for m in before if m.uin == replay_uin]),
                "published_while_stopped": 50,
                "replayed": len(replayed),
                "replay_ms": round(replay_ms, 2),
                "ordered": seqs == sorted(seqs),
                "first_payload": replayed[0].payload if replayed else None,
                "last_payload": replayed[-1].payload if replayed else None,
                "resumed_durable_received": len(resumed),
                "how": "bus.replay(kind='telemetry', uin=..., start_time=now-1s) -> ephemeral consumer DeliverPolicy.BY_START_TIME",
            },
            "acceptance": {"under_50ms": all(x < 50 for x in latencies), "replay_complete": len(replayed) == 150 and seqs == sorted(seqs)},
        }
    finally:
        await pub.delete_stream()
        await sub.close()
        await pub.close()
    write("nats_benchmark.json", result)
    return result


async def _ack_into(bucket: list, m) -> None:
    bucket.append(m)
    await m.ack()


# -------------------------------------------------------------- fallback


async def bench_fallback() -> dict:
    print("[fallback] 10 trials x (Wi-Fi->LTE, LTE->LoRa) + recovery on the real 2 Hz loop ...")
    trials = []
    for trial in range(10):
        m = CommunicationManager()
        m.register("bench", "DRN-BENCH-00", "BENCH-00")
        await m.start()
        try:
            await asyncio.sleep(1.0 + 0.05 * trial)  # vary the injection phase vs the poll
            record = {"trial": trial}
            for link, rssi, expect in ((LinkType.WIFI, -84.0, LinkType.LTE), (LinkType.LTE, -114.0, LinkType.LORA)):
                m.environment.degrade("bench", link, now=time.time(), rssi_dbm=rssi)
                deadline = time.time() + 3
                while m.drones["bench"].active != expect and time.time() < deadline:
                    await asyncio.sleep(0.005)
                sw = m.switches[-1]
                record[f"{link.value}_to_{expect.value}"] = {k: sw[k] for k in ("detect_ms", "decision_ms", "activation_ms", "total_ms")}
                await asyncio.sleep(0.3 + 0.07 * trial)
            restored_at = time.time()
            m.environment.restore("bench", None, now=restored_at)
            deadline = time.time() + 8
            while m.drones["bench"].active != LinkType.WIFI and time.time() < deadline:
                await asyncio.sleep(0.01)
            record["recovery_to_wifi_ms"] = round((time.time() - restored_at) * 1000, 1)
            record["poll_hz"] = m.timing()["measured_hz"]
            trials.append(record)
        finally:
            await m.stop()
    w2l = [t["wifi_to_lte"]["total_ms"] for t in trials]
    l2r = [t["lte_to_lora"]["total_ms"] for t in trials]
    result = {
        "thresholds": {link.value: t.__dict__ for link, t in CommunicationManager().thresholds.items()},
        "policy": CommunicationManager().policy.__dict__,
        "wifi_to_lte_total_ms": stats(w2l),
        "lte_to_lora_total_ms": stats(l2r),
        "detect_ms": stats([t["wifi_to_lte"]["detect_ms"] for t in trials] + [t["lte_to_lora"]["detect_ms"] for t in trials]),
        "decision_ms": stats([t["wifi_to_lte"]["decision_ms"] for t in trials] + [t["lte_to_lora"]["decision_ms"] for t in trials]),
        "activation_ms": stats([t["wifi_to_lte"]["activation_ms"] for t in trials] + [t["lte_to_lora"]["activation_ms"] for t in trials]),
        "recovery_to_wifi_ms": stats([t["recovery_to_wifi_ms"] for t in trials]),
        "trials": trials,
        "acceptance": {"fallback_under_1s": max(w2l + l2r) < 1000},
    }
    write("fallback_benchmark.json", result)
    return result


# ----------------------------------------------------------- TimescaleDB


async def bench_timescale() -> dict:
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as s:
        org = Organization(name=f"Benchmark {tag}", slug=f"bench-{tag}")
        s.add(org)
        s.flush()
        fleet = []
        for n in range(16):
            d = Drone(organization_id=org.id, name=f"BENCH-{n + 1:02d}", uin=f"BENCH-{tag}-{n + 1:02d}", model="M30", status="active",
                      battery=90, active=False, location=geojson_to_geography(point_to_geojson(72.63, 23.21)))
            s.add(d)
            fleet.append(d)
        s.commit()
        fleet = [(d.id, d.uin) for d in fleet]
        org_id = org.id
    pub, sub = await _bus_pair(f"ts{tag}")
    store = TelemetryStore(SessionLocal)
    try:
        print("[timescale] 16 drones x 1 Hz x 60 s real time through NATS ...")
        ingestor = TelemetryIngestor(sub, store)
        await ingestor.start()
        start = datetime.now(timezone.utc).replace(microsecond=0)
        t0 = time.monotonic()
        seconds = 60
        for sec in range(seconds):
            ts = start + timedelta(seconds=sec)
            for drone_id, uin in fleet:
                await pub.publish(uin, "telemetry", {
                    "drone_id": str(drone_id), "timestamp": ts.isoformat(), "lat": 23.21 + sec * 1e-5, "lon": 72.63, "alt": 50.0,
                    "speed": 8.0, "heading": 90.0, "soc": 90 - sec * 0.05, "rssi": -58.0, "link_type": "wifi",
                }, msg_id=f"{uin}-{sec}")
            await asyncio.sleep(max(0.0, t0 + sec + 1 - time.monotonic()))
        deadline = time.monotonic() + 10
        while ingestor.stats["inserted"] < seconds * 16 and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        await ingestor.stop()
        with SessionLocal() as s:
            actual = s.execute(text("SELECT count(*) FROM drone_telemetry WHERE drone_id = ANY(:ids) AND timestamp >= :start"),
                               {"ids": [d for d, _ in fleet], "start": start}).scalar()
        ingest = {**ingestor.snapshot(), "expected_rows": seconds * 16, "actual_rows": actual, "missing_rows": seconds * 16 - actual,
                  "latencies_ms": stats(list(ingestor.latencies_ms))}

        print("[timescale] 1 h x 16 drones bulk history + query timings ...")
        hour_end = start - timedelta(seconds=1)
        hour_start = hour_end - timedelta(hours=1)
        for drone_id, _ in fleet:
            store.write_batch([parse_record({
                "drone_id": str(drone_id), "timestamp": (hour_start + timedelta(seconds=k)).isoformat(), "lat": 23.21, "lon": 72.63,
                "alt": 50.0, "speed": 8.0, "heading": float(k % 360), "soc": 80.0, "rssi": -60.0, "link_type": "wifi"}) for k in range(3600)])
        with SessionLocal() as s:
            s.execute(text("ANALYZE drone_telemetry"))
            s.commit()
            single, fleet_ms, single_rows, fleet_rows = [], [], 0, 0
            for k in range(20):
                rows, ms = store.drone_window(s, fleet[k % 16][0], hour_start, hour_end + timedelta(seconds=1))
                single.append(ms)
                single_rows = len(rows)
            for _ in range(10):
                rows, ms = store.fleet_window(s, hour_start, hour_end + timedelta(seconds=1), [d for d, _ in fleet])
                fleet_ms.append(ms)
                fleet_rows = len(rows)
            plan = store.explain(s, fleet[0][0], hour_start, hour_end + timedelta(seconds=1))
            version = s.execute(text("SELECT extversion FROM pg_extension WHERE extname='timescaledb'")).scalar()
            chunks = s.execute(text("SELECT count(*) FROM timescaledb_information.chunks WHERE hypertable_name='drone_telemetry'")).scalar()
        result = {
            "timescaledb_version": version,
            "hypertable": {"name": "drone_telemetry", "time_column": "timestamp", "chunk_interval": "1 day", "chunks": chunks},
            "ingestion_16_drones_1hz": ingest,
            "query_1h_single_drone": {"rows": single_rows, "ms": stats(single),
                                      "sql": "SELECT ... FROM drone_telemetry WHERE drone_id=:id AND timestamp>=:start AND timestamp<:end ORDER BY timestamp"},
            "query_1h_fleet_16_drones": {"rows": fleet_rows, "ms": stats(fleet_ms),
                                         "sql": "SELECT epoch_ms, drone_id::text, ... WHERE timestamp>=:start AND timestamp<:end AND drone_id=ANY(:ids) ORDER BY timestamp, drone_id"},
            "query_plan_single_drone": plan,
            "acceptance": {"no_data_loss": actual == seconds * 16, "query_under_500ms": max(single + fleet_ms) < 500},
        }
    finally:
        await pub.delete_stream()
        await sub.close()
        await pub.close()
        with SessionLocal() as s:
            s.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": org_id})  # cascades drones + telemetry
            s.commit()
    write("timescaledb_benchmark.json", result)
    return result


def _cpu_label() -> str:
    name = platform.processor()
    if platform.system() == "Windows":
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"],
                                 capture_output=True, text=True, timeout=10)
            name = out.stdout.strip() or name
        except (OSError, subprocess.SubprocessError):
            pass
    return f"{name} ({os.cpu_count()} logical CPUs)"


def _os_label() -> str:
    # platform.release() reports "10" on Windows 11; builds >= 22000 are Windows 11.
    release, version = platform.release(), platform.version()
    if platform.system() == "Windows" and release == "10" and int(version.split(".")[-1]) >= 22000:
        release = "11"
    return f"{platform.system()} {release} ({version})"


def environment() -> dict:
    def cmd(*args: str) -> str:
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:
            return "unavailable"

    with SessionLocal() as s:
        pg = s.execute(text("SELECT version()")).scalar()
        ts = s.execute(text("SELECT extversion FROM pg_extension WHERE extname='timescaledb'")).scalar()
        postgis = s.execute(text("SELECT extversion FROM pg_extension WHERE extname='postgis'")).scalar()
    import urllib.request

    try:
        nats_version = json.load(urllib.request.urlopen(f"{settings.NATS_MONITOR_URL}/varz", timeout=3)).get("version")
    except Exception:
        nats_version = "unavailable"
    return {
        "os": _os_label(),
        "machine": platform.machine(),
        "cpu": _cpu_label(),
        "python": platform.python_version(),
        "node": cmd("node", "--version"),
        "postgresql": pg,
        "timescaledb": ts,
        "postgis": postgis,
        "nats_server": nats_version,
        "docker": cmd("docker", "--version"),
    }


async def main(sections: list[str]) -> int:
    write("environment.json", environment())
    results = {}
    runners = {"orca": bench_orca, "nats": bench_nats, "fallback": bench_fallback, "timescale": bench_timescale}
    for name in sections or list(runners):
        results[name] = await runners[name]()
    failed = [f"{n}:{k}" for n, r in results.items() for k, ok in r.get("acceptance", {}).items() if not ok]
    print("FAILED acceptance:", failed if failed else "none")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
