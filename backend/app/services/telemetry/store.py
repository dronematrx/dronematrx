"""TimescaleDB telemetry persistence + queries.

Write path (normal operation):
    simulator / drone -> NATS drone.{UIN}.telemetry -> TelemetryIngestor
      -> TelemetryStore.write_batch -> drone_telemetry hypertable

* validation happens before SQL (bad records never reach the table, and the
  table's CHECK constraints are the second line of defence);
* inserts are batched (execute_values) and idempotent:
  ON CONFLICT (timestamp, drone_id) DO NOTHING, so NATS redeliveries and
  duplicate publishes are harmless; delayed / out-of-order rows are simply
  inserted into the right chunk.
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg2.extras import execute_values
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import log_event
from app.models.drone import Drone

logger = logging.getLogger("dronematrx.telemetry.store")

MAX_FUTURE_SKEW = timedelta(minutes=5)
RETENTION = timedelta(days=30)
REQUIRED = ("drone_id", "timestamp", "lat", "lon", "alt", "speed", "heading", "soc")
LINK_TYPES = ("wifi", "lte", "lora")


class InvalidTelemetryError(ValueError):
    pass


@dataclass(frozen=True)
class TelemetryRecord:
    timestamp: datetime
    drone_id: uuid.UUID
    lat: float
    lon: float
    alt: float
    speed: float
    heading: float
    soc: float
    rssi: float | None
    link_type: str | None

    def as_row(self) -> tuple:
        return (self.timestamp, str(self.drone_id), self.lat, self.lon, self.alt, self.speed, self.heading, self.soc, self.rssi, self.link_type)


def _number(payload: dict[str, Any], key: str, low: float | None = None, high: float | None = None) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidTelemetryError(f"{key} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise InvalidTelemetryError(f"{key} must be finite")
    if (low is not None and value < low) or (high is not None and value > high):
        raise InvalidTelemetryError(f"{key}={value} outside [{low}, {high}]")
    return value


def parse_record(payload: dict[str, Any], known_drones: set[uuid.UUID] | None = None, now: datetime | None = None) -> TelemetryRecord:
    """Validate one telemetry message; raises InvalidTelemetryError with the reason."""
    if not isinstance(payload, dict):
        raise InvalidTelemetryError("payload must be an object")
    missing = [key for key in REQUIRED if payload.get(key) is None]
    if missing:
        raise InvalidTelemetryError(f"missing fields: {', '.join(missing)}")
    try:
        drone_id = uuid.UUID(str(payload["drone_id"]))
    except ValueError:
        raise InvalidTelemetryError("drone_id is not a UUID") from None
    if known_drones is not None and drone_id not in known_drones:
        raise InvalidTelemetryError(f"unknown drone {drone_id}")
    raw_ts = payload["timestamp"]
    try:
        ts = raw_ts if isinstance(raw_ts, datetime) else datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
    except ValueError:
        raise InvalidTelemetryError("timestamp is not ISO-8601") from None
    if ts.tzinfo is None:
        raise InvalidTelemetryError("timestamp must include a timezone")
    now = now or datetime.now(timezone.utc)
    if ts > now + MAX_FUTURE_SKEW:
        raise InvalidTelemetryError("timestamp is in the future")
    if ts < now - RETENTION:
        raise InvalidTelemetryError("timestamp is older than the retention window")
    rssi = payload.get("rssi")
    if rssi is not None:
        rssi = _number(payload, "rssi", -150, 0)
    link = payload.get("link_type")
    if link is not None and link not in LINK_TYPES:
        raise InvalidTelemetryError(f"link_type must be one of {LINK_TYPES}")
    return TelemetryRecord(
        timestamp=ts.astimezone(timezone.utc),
        drone_id=drone_id,
        lat=_number(payload, "lat", -90, 90),
        lon=_number(payload, "lon", -180, 180),
        alt=_number(payload, "alt", -500, 10000),
        speed=_number(payload, "speed", 0, 150),
        heading=_number(payload, "heading", 0, 360) % 360,
        soc=_number(payload, "soc", 0, 100),
        rssi=rssi,
        link_type=link,
    )


INSERT_SQL = """
INSERT INTO drone_telemetry (timestamp, drone_id, lat, lon, alt, speed, heading, soc, rssi, link_type)
VALUES %s
ON CONFLICT (timestamp, drone_id) DO NOTHING
RETURNING 1
"""


class TelemetryStore:
    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory
        self._known: set[uuid.UUID] = set()
        self._known_refreshed = 0.0
        self.stats = {"inserted": 0, "duplicates": 0, "rejected": 0, "write_errors": 0, "batches": 0}

    def known_drones(self, max_age_s: float = 30.0) -> set[uuid.UUID]:
        if time.monotonic() - self._known_refreshed > max_age_s or not self._known:
            with self._session_factory() as db:
                self._known = set(db.execute(select(Drone.id)).scalars())
            self._known_refreshed = time.monotonic()
        return self._known

    def write_batch(self, records: list[TelemetryRecord]) -> tuple[int, int]:
        """Insert records; returns (inserted, duplicates). Raises on DB failure."""
        if not records:
            return 0, 0
        with self._session_factory() as db:
            raw = db.connection().connection.dbapi_connection
            cur = raw.cursor()
            try:
                returned = execute_values(cur, INSERT_SQL, [r.as_row() for r in records], page_size=1000, fetch=True)
                db.commit()
            except Exception:
                db.rollback()
                self.stats["write_errors"] += 1
                log_event(logger, "telemetry.ingest_error", logging.ERROR, rows=len(records))
                raise
            finally:
                cur.close()
        inserted = len(returned)
        duplicates = len(records) - inserted
        self.stats["inserted"] += inserted
        self.stats["duplicates"] += duplicates
        self.stats["batches"] += 1
        return inserted, duplicates

    def ingest(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate + write raw payloads (used by the ingestor and tests)."""
        known = self.known_drones()
        good: list[TelemetryRecord] = []
        rejected: list[str] = []
        for payload in payloads:
            try:
                good.append(parse_record(payload, known))
            except InvalidTelemetryError as exc:
                rejected.append(str(exc))
        if rejected:
            self.stats["rejected"] += len(rejected)
            log_event(logger, "telemetry.ingest_rejected", logging.WARNING, count=len(rejected), first=rejected[0])
        inserted, duplicates = self.write_batch(good)
        return {"inserted": inserted, "duplicates": duplicates, "rejected": len(rejected), "reasons": rejected}

    # ---------------------------------------------------------------- queries

    def drone_window(self, db: Session, drone_id: uuid.UUID, start: datetime, end: datetime) -> tuple[list[dict[str, Any]], float]:
        started = time.perf_counter()
        rows = db.execute(
            text(
                """
                SELECT timestamp, drone_id, lat, lon, alt, speed, heading, soc, rssi, link_type
                FROM drone_telemetry
                WHERE drone_id = :drone_id AND timestamp >= :start AND timestamp < :end
                ORDER BY timestamp
                """
            ),
            {"drone_id": drone_id, "start": start, "end": end},
        ).mappings().all()
        elapsed_ms = (time.perf_counter() - started) * 1000
        log_event(logger, "telemetry.query", logging.DEBUG, kind="drone_window", rows=len(rows), ms=round(elapsed_ms, 2))
        return [dict(r) for r in rows], elapsed_ms

    FLEET_COLUMNS = ("timestamp_ms", "drone_id", "lat", "lon", "alt", "speed", "heading", "soc", "rssi", "link_type")

    def fleet_window(self, db: Session, start: datetime, end: datetime, drone_ids: list[uuid.UUID] | None = None) -> tuple[list[tuple], float]:
        """All drones' rows in a window, columnar (see FLEET_COLUMNS).

        Timestamps come back as epoch milliseconds and ids as text: the
        server executes this in ~20 ms for 57,600 rows, and building that many
        Python datetime/UUID objects would otherwise dominate the latency.
        """
        started = time.perf_counter()
        params: dict[str, Any] = {"start": start, "end": end}
        filter_sql = ""
        if drone_ids:
            filter_sql = "AND drone_id = ANY(%(ids)s)"
            params["ids"] = [str(d) for d in drone_ids]
        raw = db.connection().connection.dbapi_connection.cursor()
        try:
            raw.execute(
                f"""
                SELECT (extract(epoch FROM timestamp) * 1000)::bigint, drone_id::text, lat, lon, alt, speed, heading, soc, rssi, link_type
                FROM drone_telemetry
                WHERE timestamp >= %(start)s AND timestamp < %(end)s {filter_sql.replace("ANY(%(ids)s)", "ANY(%(ids)s::uuid[])")}
                ORDER BY timestamp, drone_id
                """,
                params,
            )
            rows = raw.fetchall()
        finally:
            raw.close()
        elapsed_ms = (time.perf_counter() - started) * 1000
        log_event(logger, "telemetry.query", logging.DEBUG, kind="fleet_window", rows=len(rows), ms=round(elapsed_ms, 2))
        return rows, elapsed_ms

    def fleet_summary(self, db: Session, start: datetime, end: datetime, bucket: str = "1 minute") -> list[dict[str, Any]]:
        """Per-drone time_bucket aggregates (row counts, avg SOC/RSSI) for dashboards."""
        if bucket not in ("10 seconds", "1 minute", "5 minutes", "1 hour"):
            raise ValueError("unsupported bucket")
        rows = db.execute(
            text(
                f"""
                SELECT time_bucket(INTERVAL '{bucket}', timestamp) AS bucket, drone_id,
                       count(*) AS samples, avg(soc) AS avg_soc, avg(rssi) AS avg_rssi, max(speed) AS max_speed
                FROM drone_telemetry
                WHERE timestamp >= :start AND timestamp < :end
                GROUP BY bucket, drone_id
                ORDER BY bucket, drone_id
                """
            ),
            {"start": start, "end": end},
        ).mappings().all()
        return [dict(r) for r in rows]

    def counts(self, db: Session, since: datetime | None = None, organization_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        clauses = []
        if since is not None:
            clauses.append("t.timestamp >= :since")
            params["since"] = since
        if organization_id is not None:
            clauses.append("d.organization_id = :org")
            params["org"] = organization_id
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = db.execute(
            text(
                f"""
                SELECT t.drone_id, d.name, count(*) AS rows, min(t.timestamp) AS first, max(t.timestamp) AS last
                FROM drone_telemetry t JOIN drones d ON d.id = t.drone_id
                {where}
                GROUP BY t.drone_id, d.name ORDER BY d.name
                """
            ),
            params,
        ).mappings().all()
        return [dict(r) for r in rows]

    def explain(self, db: Session, drone_id: uuid.UUID, start: datetime, end: datetime) -> list[str]:
        rows = db.execute(
            text(
                """
                EXPLAIN (ANALYZE, BUFFERS)
                SELECT * FROM drone_telemetry
                WHERE drone_id = :drone_id AND timestamp >= :start AND timestamp < :end
                ORDER BY timestamp
                """
            ),
            {"drone_id": drone_id, "start": start, "end": end},
        ).scalars().all()
        return list(rows)

    def enforce_retention(self) -> int:
        """drop_chunks older than 30 days (Apache-edition replacement for add_retention_policy).

        Without TimescaleDB (plain-table fallback, see migration b7d3c1e9a402)
        old rows are deleted instead; the return value is then a row count."""
        with self._session_factory() as db:
            hypertable = db.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")).scalar() is not None
            if hypertable:
                dropped = db.execute(text("SELECT count(*) FROM drop_chunks('drone_telemetry', older_than => INTERVAL '30 days')")).scalar() or 0
            else:
                dropped = db.execute(text("DELETE FROM drone_telemetry WHERE timestamp < now() - INTERVAL '30 days'")).rowcount or 0
            db.commit()
        if dropped:
            log_event(logger, "telemetry.retention", chunks_dropped=dropped)
        return int(dropped)
