"""sprint4 timescale telemetry

Enables the TimescaleDB extension in the existing database (the server must
preload it: docker-compose passes shared_preload_libraries=timescaledb) and
creates the 1 Hz `drone_telemetry` hypertable partitioned on `timestamp`
(1-day chunks). Retention (30 days) is enforced by the backend calling
drop_chunks() periodically: the PGDG timescaledb package is the Apache-2.0
edition, where add_retention_policy() (a TSL feature) is unavailable.

Revision ID: b7d3c1e9a402
Revises: 45a893a48f92
Create Date: 2026-10-06 11:30:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d3c1e9a402"
down_revision: Union[str, None] = "45a893a48f92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timescaledb_available() -> bool:
    # Managed Postgres hosts (e.g. Render) may not ship TimescaleDB: fall back to
    # a plain table with the same columns and indexes so deploys still migrate.
    query = sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    return op.get_bind().execute(query).scalar() is not None


def upgrade() -> None:
    timescale = _timescaledb_available()
    if timescale:
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.create_table(
        "drone_telemetry",
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("drone_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("alt", sa.REAL(), nullable=False),
        sa.Column("speed", sa.REAL(), nullable=False),
        sa.Column("heading", sa.REAL(), nullable=False),
        sa.Column("soc", sa.REAL(), nullable=False),
        sa.Column("rssi", sa.REAL(), nullable=True),
        sa.Column("link_type", sa.String(length=8), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("lat BETWEEN -90 AND 90", name="ck_drone_telemetry_lat"),
        sa.CheckConstraint("lon BETWEEN -180 AND 180", name="ck_drone_telemetry_lon"),
        sa.CheckConstraint("speed >= 0", name="ck_drone_telemetry_speed"),
        sa.CheckConstraint("heading >= 0 AND heading < 360", name="ck_drone_telemetry_heading"),
        sa.CheckConstraint("soc BETWEEN 0 AND 100", name="ck_drone_telemetry_soc"),
        sa.CheckConstraint("rssi IS NULL OR rssi BETWEEN -150 AND 0", name="ck_drone_telemetry_rssi"),
        sa.ForeignKeyConstraint(["drone_id"], ["drones.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("timestamp", "drone_id"),
    )
    if timescale:
        # Hypertable on `timestamp`; also creates the (timestamp DESC) index used by fleet queries.
        op.execute("SELECT create_hypertable('drone_telemetry', by_range('timestamp', INTERVAL '1 day'))")
    else:
        op.create_index("drone_telemetry_timestamp_idx", "drone_telemetry", [sa.text("timestamp DESC")])
    # Per-drone window queries: WHERE drone_id = ? AND timestamp BETWEEN ... ORDER BY timestamp
    op.create_index("ix_drone_telemetry_drone_time", "drone_telemetry", ["drone_id", sa.text("timestamp DESC")])


def downgrade() -> None:
    op.drop_index("ix_drone_telemetry_drone_time", table_name="drone_telemetry")
    op.drop_table("drone_telemetry")
    # The extension is left installed: dropping it would need superuser and
    # could break other hypertables.
