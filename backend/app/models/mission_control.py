"""Sprint 6 mission-control persistence.

Everything references the existing `drones`, `missions`, `users` and
`organizations` rows; audit events go to the existing `audit_logs` table.
Nothing here duplicates the fleet, mission or telemetry models.
"""
import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BINDING_STATES = ("binding", "bound", "connection_failed", "unbound")
CONNECTION_TYPES = ("sitl", "udp", "serial")
COMMAND_STATES = ("requested", "dispatched", "accepted", "confirmed", "rejected", "timed_out", "failed", "unsupported", "cancelled")
VIDEO_KINDS = ("sitl_camera", "test_pattern", "hardware_camera")
REVIEW_STATUSES = ("pending", "accepted", "rejected")


class FlightControllerBinding(Base):
    """A flight controller (identified by its endpoint + MAVLink system id)
    bound to one drone record. History is kept: unbinding closes the row."""

    __tablename__ = "flight_controller_bindings"
    __table_args__ = (
        CheckConstraint(f"state IN {BINDING_STATES}", name="ck_fc_binding_state"),
        CheckConstraint(f"connection_type IN {CONNECTION_TYPES}", name="ck_fc_binding_conn_type"),
        # One active binding per controller identity, and per drone.
        Index("uq_fc_binding_active_fc", "fc_identity", unique=True, postgresql_where=text("state IN ('binding', 'bound')")),
        Index("uq_fc_binding_active_drone", "drone_id", unique=True, postgresql_where=text("state IN ('binding', 'bound')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    drone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drones.id", ondelete="CASCADE"), index=True)
    fc_identity: Mapped[str] = mapped_column(String(120), nullable=False)  # e.g. sitl:<session>:<index> | udp:<host>:<port>
    connection_type: Mapped[str] = mapped_column(String(10), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(200), nullable=False)
    system_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firmware: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sitl_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sitl_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="binding")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    bound_via: Mapped[str] = mapped_column(String(12), nullable=False, default="discovery")  # discovery | qr | manual
    bound_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DroneCommand(Base):
    """One command to one drone with its tracked lifecycle. A fleet command
    is a set of these sharing `fleet_command_id`."""

    __tablename__ = "drone_commands"
    __table_args__ = (CheckConstraint(f"state IN {COMMAND_STATES}", name="ck_drone_command_state"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True, default=uuid.uuid4)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)
    fleet_command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    drone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drones.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user_role: Mapped[str] = mapped_column(String(20), nullable=False)
    command: Mapped[str] = mapped_column(String(20), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)  # sitl | kinematic_demo | hardware
    adapter: Mapped[str] = mapped_column(String(30), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="requested")
    ack_result: Mapped[int | None] = mapped_column(Integer, nullable=True)  # MAV_RESULT when the FC acknowledged
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    timings: Mapped[dict] = mapped_column(JSONB, default=dict)  # ms per boundary, see services/mission_control/commands.py
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VideoSource(Base):
    """A configured video source served through the media relay (MediaMTX)."""

    __tablename__ = "video_sources"
    __table_args__ = (CheckConstraint(f"kind IN {VIDEO_KINDS}", name="ck_video_source_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    drone_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("drones.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    path: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)  # relay path of the high rendition
    low_path: Mapped[str | None] = mapped_column(String(120), nullable=True)  # low rendition for degraded links
    record: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Detection(Base):
    """One inference event and its operator review. The original event is
    never deleted; a rejection is a review state, not a removal."""

    __tablename__ = "detections"
    __table_args__ = (
        CheckConstraint(f"review_status IN {REVIEW_STATUSES}", name="ck_detection_review_status"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_detection_confidence"),
        CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="ck_detection_latlon_pair"),
        CheckConstraint("latitude IS NULL OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)", name="ck_detection_latlon_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)  # producer id: ingest is idempotent
    drone_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("drones.id", ondelete="SET NULL"), nullable=True, index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="SET NULL"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)  # sim_colour_detector | test_fixture
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    bbox: Mapped[dict] = mapped_column(JSONB, default=dict)
    thumbnail: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    thumbnail_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_error: Mapped[str | None] = mapped_column(String(120), nullable=True)  # why there is no location
    # Written only for an ACCEPTED detection with a valid location.
    accepted_location: Mapped[object | None] = mapped_column(Geography(geometry_type="POINT", srid=4326), nullable=True)
    inferred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    review_status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending", index=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
