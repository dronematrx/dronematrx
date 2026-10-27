"""sprint6 mission control

Flight-controller bindings, the per-drone command log, video sources and
detections (with an operator review and a PostGIS point for accepted
detections). Drones, missions, users and audit_logs are reused.

Revision ID: d6f2b8c4e715
Revises: c5e8a1f3d210
Create Date: 2026-10-23 10:00:00

"""
from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d6f2b8c4e715"
down_revision: Union[str, None] = "c5e8a1f3d210"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "flight_controller_bindings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("drone_id", UUID, sa.ForeignKey("drones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fc_identity", sa.String(120), nullable=False),
        sa.Column("connection_type", sa.String(10), nullable=False),
        sa.Column("endpoint", sa.String(200), nullable=False),
        sa.Column("system_id", sa.Integer(), nullable=True),
        sa.Column("firmware", sa.String(80), nullable=True),
        sa.Column("sitl_session_id", sa.String(64), nullable=True),
        sa.Column("sitl_index", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("bound_via", sa.String(12), nullable=False),
        sa.Column("bound_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("bound_at", TS, nullable=True),
        sa.Column("unbound_at", TS, nullable=True),
        sa.CheckConstraint("state IN ('binding', 'bound', 'connection_failed', 'unbound')", name="ck_fc_binding_state"),
        sa.CheckConstraint("connection_type IN ('sitl', 'udp', 'serial')", name="ck_fc_binding_conn_type"),
    )
    op.create_index("ix_flight_controller_bindings_organization_id", "flight_controller_bindings", ["organization_id"])
    op.create_index("ix_flight_controller_bindings_drone_id", "flight_controller_bindings", ["drone_id"])
    op.create_index("uq_fc_binding_active_fc", "flight_controller_bindings", ["fc_identity"], unique=True,
                    postgresql_where=sa.text("state IN ('binding', 'bound')"))
    op.create_index("uq_fc_binding_active_drone", "flight_controller_bindings", ["drone_id"], unique=True,
                    postgresql_where=sa.text("state IN ('binding', 'bound')"))

    op.create_table(
        "drone_commands",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("correlation_id", UUID, nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=True, unique=True),
        sa.Column("fleet_command_id", UUID, nullable=True),
        sa.Column("drone_id", UUID, sa.ForeignKey("drones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_role", sa.String(20), nullable=False),
        sa.Column("command", sa.String(20), nullable=False),
        sa.Column("params", JSONB, nullable=True),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("adapter", sa.String(30), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("ack_result", sa.Integer(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("expected_state", JSONB, nullable=True),
        sa.Column("timings", JSONB, nullable=True),
        sa.Column("requested_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("dispatched_at", TS, nullable=True),
        sa.Column("acknowledged_at", TS, nullable=True),
        sa.Column("completed_at", TS, nullable=True),
        sa.CheckConstraint("state IN ('requested', 'dispatched', 'accepted', 'confirmed', 'rejected', 'timed_out', 'failed', 'unsupported', 'cancelled')",
                           name="ck_drone_command_state"),
    )
    for col in ("organization_id", "correlation_id", "fleet_command_id", "drone_id"):
        op.create_index(f"ix_drone_commands_{col}", "drone_commands", [col])

    op.create_table(
        "video_sources",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("drone_id", UUID, sa.ForeignKey("drones.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("path", sa.String(120), nullable=False, unique=True),
        sa.Column("low_path", sa.String(120), nullable=True),
        sa.Column("record", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('sitl_camera', 'test_pattern', 'hardware_camera')", name="ck_video_source_kind"),
    )
    op.create_index("ix_video_sources_organization_id", "video_sources", ["organization_id"])

    op.create_table(
        "detections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.String(80), nullable=False, unique=True),
        sa.Column("drone_id", UUID, sa.ForeignKey("drones.id", ondelete="SET NULL"), nullable=True),
        sa.Column("mission_id", UUID, sa.ForeignKey("missions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("label", sa.String(40), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("bbox", JSONB, nullable=True),
        sa.Column("thumbnail", sa.LargeBinary(), nullable=True),
        sa.Column("thumbnail_type", sa.String(30), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("location_error", sa.String(120), nullable=True),
        sa.Column("accepted_location", geoalchemy2.types.Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True),
        sa.Column("inferred_at", TS, nullable=False),
        sa.Column("received_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("review_status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", TS, nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("details", JSONB, nullable=True),
        sa.CheckConstraint("review_status IN ('pending', 'accepted', 'rejected')", name="ck_detection_review_status"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_detection_confidence"),
        sa.CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="ck_detection_latlon_pair"),
        sa.CheckConstraint("latitude IS NULL OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)", name="ck_detection_latlon_range"),
    )
    for col in ("organization_id", "drone_id", "mission_id", "review_status"):
        op.create_index(f"ix_detections_{col}", "detections", [col])
    op.create_index("idx_detections_accepted_location", "detections", ["accepted_location"], postgresql_using="gist")


def downgrade() -> None:
    op.drop_table("detections")
    op.drop_table("video_sources")
    op.drop_table("drone_commands")
    op.drop_table("flight_controller_bindings")
