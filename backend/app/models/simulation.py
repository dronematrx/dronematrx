"""Sprint 5 simulation persistence.

A simulation session belongs to exactly one mission (missions.execution_mode
= 'simulation'); its drones are rows of the existing `drones` table with
`simulated = true`, so the same drone id flows through CBBA, SITL, Gazebo
and the map. Nothing here duplicates missions, drones or telemetry tables.
"""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

SESSION_STATES = (
    "created",
    "world_generating",
    "world_ready",
    "starting",
    "running",
    "paused",
    "completed",
    "aborted",
    "failed",
    "stopped",
)
TERMINAL_SESSION_STATES = ("completed", "aborted", "failed", "stopped")
INSTANCE_STATES = ("pending", "starting", "ready", "flying", "returning", "landed", "failed", "unavailable", "comm_lost")


class SimulationSession(Base):
    __tablename__ = "simulation_sessions"
    __table_args__ = (CheckConstraint(f"state IN {SESSION_STATES}", name="ck_simulation_sessions_state"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mission_plans.id", ondelete="SET NULL"), nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="created")
    task_type: Mapped[str] = mapped_column(String(30), nullable=False)
    requested_drones: Mapped[int] = mapped_column(Integer, nullable=False)
    spawned_drones: Mapped[int] = mapped_column(Integer, default=0)
    speed: Mapped[float] = mapped_column(Float, default=1.0)
    sim_time_s: Mapped[float] = mapped_column(Float, default=0.0)
    # Snapshot of everything that defines the run (flight params, DEM source,
    # link-loss policy, seeds...). Compared by the report diff view.
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    world: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # world metadata (frame, DEM, timings)
    environment: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # simhost: gz/PX4 versions, capacity
    progress: Mapped[dict] = mapped_column(JSONB, default=dict)  # task status, coverage, guardrail state
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    instances: Mapped[list["SimulationDroneInstance"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True, order_by="SimulationDroneInstance.instance_index"
    )


class SimulationDroneInstance(Base):
    """One spawned drone: drone row <-> PX4 instance <-> Gazebo model."""

    __tablename__ = "simulation_drone_instances"
    __table_args__ = (
        UniqueConstraint("session_id", "drone_id", name="uq_sim_instance_drone"),
        UniqueConstraint("session_id", "instance_index", name="uq_sim_instance_index"),
        CheckConstraint(f"state IN {INSTANCE_STATES}", name="ck_sim_instance_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("simulation_sessions.id", ondelete="CASCADE"), index=True)
    drone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drones.id", ondelete="CASCADE"))
    instance_index: Mapped[int] = mapped_column(Integer, nullable=False)  # PX4 -i N
    uin: Mapped[str] = mapped_column(String(50), nullable=False)
    gz_model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mavlink_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sitl_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    spawn: Mapped[dict] = mapped_column(JSONB, default=dict)  # lat/lon/ground elevation + gazebo x/y/z/yaw
    bundle: Mapped[list] = mapped_column(JSONB, default=list)  # ordered task keys assigned by CBBA
    route: Mapped[dict] = mapped_column(JSONB, default=dict)  # uploaded mission items + seq->task mapping
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)  # battery start/end, distance, waypoints reached
    track: Mapped[list] = mapped_column(JSONB, default=list)  # downsampled [sim_t, lat, lon, alt] from telemetry
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped["SimulationSession"] = relationship(back_populates="instances")


class SimulationEvent(Base):
    __tablename__ = "simulation_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("simulation_sessions.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sim_time_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="orchestrator")  # simhost | orchestrator | operator
    drone_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("drones.id", ondelete="SET NULL"), nullable=True)
    previous_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    new_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    mission_outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)  # continued | replanned | aborted
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class SimulationReport(Base):
    __tablename__ = "simulation_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("simulation_sessions.id", ondelete="CASCADE"), unique=True)
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    generation_ms: Mapped[float] = mapped_column(Float, default=0.0)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SimulationReportComparison(Base):
    __tablename__ = "simulation_report_comparisons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("simulation_reports.id", ondelete="CASCADE"), index=True)
    baseline_report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("simulation_reports.id", ondelete="CASCADE"))
    compatible: Mapped[bool] = mapped_column(default=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
