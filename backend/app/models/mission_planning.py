"""Mission planning persistence: generated tasks, CBBA plans, per-task
assignments, and the mission state-transition history."""
import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

VALID_TASK_STATUS = ("pending", "assigned", "unassigned", "completed")
VALID_TASK_SOURCE = ("generated", "manual")


class MissionTask(Base):
    __tablename__ = "mission_tasks"
    __table_args__ = (
        UniqueConstraint("mission_id", "task_key", name="uq_mission_tasks_key"),
        CheckConstraint(f"status IN {VALID_TASK_STATUS}", name="ck_mission_tasks_status"),
        CheckConstraint(f"source IN {VALID_TASK_SOURCE}", name="ck_mission_tasks_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    task_key: Mapped[str] = mapped_column(String(16), nullable=False)
    task_type: Mapped[str] = mapped_column(String(30), nullable=False, default="coverage")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="generated")
    # LINESTRING for coverage segments, POINT for inspection tasks.
    geometry: Mapped[object] = mapped_column(Geography(geometry_type="GEOMETRY", srid=4326), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    required_capability: Mapped[str | None] = mapped_column(String(30), nullable=True)
    length_m: Mapped[float] = mapped_column(Float, default=0)
    hover_time_s: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    unassigned_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    mission: Mapped["Mission"] = relationship(back_populates="tasks")  # noqa: F821


class MissionPlan(Base):
    """One coordination-algorithm run for a mission. Exactly one plan per
    mission is `is_current`; older rows are kept as summaries for audit."""

    __tablename__ = "mission_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    algorithm: Mapped[str] = mapped_column(String(40), nullable=False, default="cbba")
    trigger: Mapped[str] = mapped_column(String(40), nullable=False, default="allocate")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)

    converged: Mapped[bool] = mapped_column(Boolean, default=False)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    consensus_rounds: Mapped[int] = mapped_column(Integer, default=0)
    messages_exchanged: Mapped[int] = mapped_column(Integer, default=0)
    convergence_time_ms: Mapped[float] = mapped_column(Float, default=0)

    drone_count: Mapped[int] = mapped_column(Integer, default=0)
    task_count: Mapped[int] = mapped_column(Integer, default=0)
    assigned_count: Mapped[int] = mapped_column(Integer, default=0)
    unassigned_count: Mapped[int] = mapped_column(Integer, default=0)
    total_distance_m: Mapped[float] = mapped_column(Float, default=0)
    estimated_duration_s: Mapped[float] = mapped_column(Float, default=0)
    estimated_energy_wh: Mapped[float] = mapped_column(Float, default=0)
    coverage_pct: Mapped[float] = mapped_column(Float, default=0)
    max_coverage_pct: Mapped[float] = mapped_column(Float, default=0)
    average_bid: Mapped[float] = mapped_column(Float, default=0)

    params: Mapped[dict] = mapped_column(JSONB, default=dict)  # flight/constraint snapshot used
    diagnostics: Mapped[dict] = mapped_column(JSONB, default=dict)  # history, excluded drones, warnings...

    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    mission: Mapped["Mission"] = relationship(back_populates="plans")  # noqa: F821
    bundles: Mapped[list["TaskBundle"]] = relationship(back_populates="plan", cascade="all, delete-orphan", passive_deletes=True)  # noqa: F821
    assignments: Mapped[list["TaskAssignment"]] = relationship(back_populates="plan", cascade="all, delete-orphan", passive_deletes=True)


class TaskAssignment(Base):
    __tablename__ = "task_assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mission_plans.id", ondelete="CASCADE"), index=True)
    bundle_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("task_bundles.id", ondelete="CASCADE"))
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mission_tasks.id", ondelete="CASCADE"), unique=True)
    drone_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("drones.id", ondelete="SET NULL"), nullable=True)
    bid: Mapped[float] = mapped_column(Float, default=0)
    assignment_order: Mapped[int] = mapped_column(Integer, default=0)
    eta_seconds: Mapped[float] = mapped_column(Float, default=0)
    energy_pct: Mapped[float] = mapped_column(Float, default=0)
    assigned_at: Mapped[datetime] = mapped_column(server_default=func.now())

    plan: Mapped["MissionPlan"] = relationship(back_populates="assignments")


class MissionStateHistory(Base):
    __tablename__ = "mission_state_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    previous_state: Mapped[str] = mapped_column(String(20), nullable=False)
    new_state: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    mission: Mapped["Mission"] = relationship(back_populates="state_history")  # noqa: F821
