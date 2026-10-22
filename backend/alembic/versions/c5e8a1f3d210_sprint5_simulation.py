"""sprint5 simulation sessions

Adds simulation-only drones (drones.simulated), simulation missions
(missions.execution_mode) and the session / instance / event / report /
comparison tables. Existing mission, drone and telemetry tables are reused.

Revision ID: c5e8a1f3d210
Revises: b7d3c1e9a402
Create Date: 2026-10-20 23:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5e8a1f3d210"
down_revision: Union[str, None] = "b7d3c1e9a402"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SESSION_STATES = ("created", "world_generating", "world_ready", "starting", "running", "paused", "completed", "aborted", "failed", "stopped")
INSTANCE_STATES = ("pending", "starting", "ready", "flying", "returning", "landed", "failed", "unavailable", "comm_lost")
UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("drones", sa.Column("simulated", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("missions", sa.Column("execution_mode", sa.String(length=20), server_default="live", nullable=False))
    op.create_check_constraint("ck_missions_execution_mode", "missions", "execution_mode IN ('live', 'simulation')")

    op.create_table(
        "simulation_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mission_id", UUID, sa.ForeignKey("missions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", UUID, sa.ForeignKey("mission_plans.id", ondelete="SET NULL"), nullable=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("task_type", sa.String(30), nullable=False),
        sa.Column("requested_drones", sa.Integer(), nullable=False),
        sa.Column("spawned_drones", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("speed", sa.Float(), nullable=False, server_default="1"),
        sa.Column("sim_time_s", sa.Float(), nullable=False, server_default="0"),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("world", JSONB, nullable=True),
        sa.Column("environment", JSONB, nullable=True),
        sa.Column("progress", JSONB, nullable=False, server_default="{}"),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"state IN {SESSION_STATES}", name="ck_simulation_sessions_state"),
    )
    op.create_index("ix_simulation_sessions_organization_id", "simulation_sessions", ["organization_id"])
    op.create_index("ix_simulation_sessions_mission_id", "simulation_sessions", ["mission_id"])

    op.create_table(
        "simulation_drone_instances",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, sa.ForeignKey("simulation_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("drone_id", UUID, sa.ForeignKey("drones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("instance_index", sa.Integer(), nullable=False),
        sa.Column("uin", sa.String(50), nullable=False),
        sa.Column("gz_model_name", sa.String(64), nullable=True),
        sa.Column("mavlink_port", sa.Integer(), nullable=True),
        sa.Column("sitl_pid", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("spawn", JSONB, nullable=False, server_default="{}"),
        sa.Column("bundle", JSONB, nullable=False, server_default="[]"),
        sa.Column("route", JSONB, nullable=False, server_default="{}"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("stats", JSONB, nullable=False, server_default="{}"),
        sa.Column("track", JSONB, nullable=False, server_default="[]"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("session_id", "drone_id", name="uq_sim_instance_drone"),
        sa.UniqueConstraint("session_id", "instance_index", name="uq_sim_instance_index"),
        sa.CheckConstraint(f"state IN {INSTANCE_STATES}", name="ck_sim_instance_state"),
    )
    op.create_index("ix_simulation_drone_instances_session_id", "simulation_drone_instances", ["session_id"])

    op.create_table(
        "simulation_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, sa.ForeignKey("simulation_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sim_time_s", sa.Float(), nullable=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("drone_id", UUID, sa.ForeignKey("drones.id", ondelete="SET NULL"), nullable=True),
        sa.Column("previous_state", sa.String(30), nullable=True),
        sa.Column("new_state", sa.String(30), nullable=True),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("mission_outcome", sa.String(20), nullable=True),
        sa.Column("details", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_simulation_events_session_id", "simulation_events", ["session_id"])
    op.create_index("ix_simulation_events_event_type", "simulation_events", ["event_type"])

    op.create_table(
        "simulation_reports",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, sa.ForeignKey("simulation_sessions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("mission_id", UUID, sa.ForeignKey("missions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("data", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_simulation_reports_mission_id", "simulation_reports", ["mission_id"])
    op.create_index("ix_simulation_reports_organization_id", "simulation_reports", ["organization_id"])

    op.create_table(
        "simulation_report_comparisons",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("report_id", UUID, sa.ForeignKey("simulation_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("baseline_report_id", UUID, sa.ForeignKey("simulation_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("compatible", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("data", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_simulation_report_comparisons_report_id", "simulation_report_comparisons", ["report_id"])


def downgrade() -> None:
    op.drop_table("simulation_report_comparisons")
    op.drop_table("simulation_reports")
    op.drop_table("simulation_events")
    op.drop_table("simulation_drone_instances")
    op.drop_table("simulation_sessions")
    op.drop_constraint("ck_missions_execution_mode", "missions", type_="check")
    op.drop_column("missions", "execution_mode")
    op.drop_column("drones", "simulated")
