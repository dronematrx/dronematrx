import uuid
from datetime import datetime

from sqlalchemy import REAL, CheckConstraint, DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Hypertable partitioning (TimescaleDB): see create_hypertable() in migration
# b7d3c1e9a402 and tests/conftest.py. Chunk interval 1 day; 30-day retention via
# drop_chunks() from TelemetryStore.enforce_retention (Apache edition: no TSL policies).
TELEMETRY_CHUNK_INTERVAL = "1 day"
TELEMETRY_RETENTION = "30 days"


class DroneTelemetry(Base):
    """1 Hz per-drone telemetry history (TimescaleDB hypertable on `timestamp`).

    The primary key (drone_id, timestamp) makes ingestion idempotent: a
    redelivered or duplicated NATS message hits ON CONFLICT DO NOTHING.
    """

    __tablename__ = "drone_telemetry"
    __table_args__ = (
        CheckConstraint("lat BETWEEN -90 AND 90", name="ck_drone_telemetry_lat"),
        CheckConstraint("lon BETWEEN -180 AND 180", name="ck_drone_telemetry_lon"),
        CheckConstraint("speed >= 0", name="ck_drone_telemetry_speed"),
        CheckConstraint("heading >= 0 AND heading < 360", name="ck_drone_telemetry_heading"),
        CheckConstraint("soc BETWEEN 0 AND 100", name="ck_drone_telemetry_soc"),
        CheckConstraint("rssi IS NULL OR rssi BETWEEN -150 AND 0", name="ck_drone_telemetry_rssi"),
    )

    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    drone_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("drones.id", ondelete="CASCADE"), primary_key=True)

    # ix_drone_telemetry_drone_time (drone_id, timestamp DESC) is declared in the migration.
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    alt: Mapped[float] = mapped_column(REAL, nullable=False)
    speed: Mapped[float] = mapped_column(REAL, nullable=False)
    heading: Mapped[float] = mapped_column(REAL, nullable=False)
    soc: Mapped[float] = mapped_column(REAL, nullable=False)
    rssi: Mapped[float | None] = mapped_column(REAL, nullable=True)
    link_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
