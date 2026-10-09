"""sprint3 cbba planning

Adds CBBA mission planning persistence (mission_tasks, mission_plans,
task_assignments, mission_state_history), reshapes the previously unused
task_bundles table into per-drone CBBA bundles, adds wizard/config columns
to missions and performance columns to drones, and replaces the geofence
type set ('geofence', 'nfz') with ('inclusion', 'exclusion', 'nfz').

Revision ID: 45a893a48f92
Revises: a2605dcc768e
Create Date: 2026-10-04 19:33:16.670184

"""
from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "45a893a48f92"
down_revision: Union[str, None] = "a2605dcc768e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _geog(geometry_type: str) -> geoalchemy2.types.Geography:
    # spatial_index=False: GiST indexes are created explicitly below.
    return geoalchemy2.types.Geography(geometry_type=geometry_type, srid=4326, spatial_index=False)


def upgrade() -> None:
    # --- geofence types -------------------------------------------------
    op.drop_constraint("ck_geofences_type", "geofences", type_="check")
    op.execute("UPDATE geofences SET type = 'inclusion' WHERE type = 'geofence'")
    op.alter_column("geofences", "type", server_default=None)
    op.create_check_constraint("ck_geofences_type", "geofences", "type IN ('inclusion', 'exclusion', 'nfz')")

    # --- drones / missions ----------------------------------------------
    op.add_column("drones", sa.Column("max_speed", sa.Float(), server_default="15", nullable=False))
    op.add_column("drones", sa.Column("endurance_minutes", sa.Float(), server_default="30", nullable=False))
    op.add_column("missions", sa.Column("template", sa.String(length=30), nullable=True))
    op.add_column("missions", sa.Column("wizard_step", sa.Integer(), server_default="1", nullable=False))
    op.add_column(
        "missions",
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.add_column("missions", sa.Column("validation_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("missions", sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True))

    # --- new planning tables ----------------------------------------------
    op.create_table(
        "mission_plans",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mission_id", sa.UUID(), nullable=False),
        sa.Column("algorithm", sa.String(length=40), nullable=False),
        sa.Column("trigger", sa.String(length=40), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("converged", sa.Boolean(), nullable=False),
        sa.Column("timed_out", sa.Boolean(), nullable=False),
        sa.Column("iterations", sa.Integer(), nullable=False),
        sa.Column("consensus_rounds", sa.Integer(), nullable=False),
        sa.Column("messages_exchanged", sa.Integer(), nullable=False),
        sa.Column("convergence_time_ms", sa.Float(), nullable=False),
        sa.Column("drone_count", sa.Integer(), nullable=False),
        sa.Column("task_count", sa.Integer(), nullable=False),
        sa.Column("assigned_count", sa.Integer(), nullable=False),
        sa.Column("unassigned_count", sa.Integer(), nullable=False),
        sa.Column("total_distance_m", sa.Float(), nullable=False),
        sa.Column("estimated_duration_s", sa.Float(), nullable=False),
        sa.Column("estimated_energy_wh", sa.Float(), nullable=False),
        sa.Column("coverage_pct", sa.Float(), nullable=False),
        sa.Column("max_coverage_pct", sa.Float(), nullable=False),
        sa.Column("average_bid", sa.Float(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("diagnostics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mission_plans_mission_id"), "mission_plans", ["mission_id"], unique=False)

    op.create_table(
        "mission_state_history",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mission_id", sa.UUID(), nullable=False),
        sa.Column("previous_state", sa.String(length=20), nullable=False),
        sa.Column("new_state", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mission_state_history_mission_id"), "mission_state_history", ["mission_id"], unique=False)

    op.create_table(
        "mission_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mission_id", sa.UUID(), nullable=False),
        sa.Column("task_key", sa.String(length=16), nullable=False),
        sa.Column("task_type", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("geometry", _geog("GEOMETRY"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("required_capability", sa.String(length=30), nullable=True),
        sa.Column("length_m", sa.Float(), nullable=False),
        sa.Column("hover_time_s", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("unassigned_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("source IN ('generated', 'manual')", name="ck_mission_tasks_source"),
        sa.CheckConstraint("status IN ('pending', 'assigned', 'unassigned', 'completed')", name="ck_mission_tasks_status"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mission_id", "task_key", name="uq_mission_tasks_key"),
    )
    op.create_index("idx_mission_tasks_geometry", "mission_tasks", ["geometry"], unique=False, postgresql_using="gist")
    op.create_index(op.f("ix_mission_tasks_mission_id"), "mission_tasks", ["mission_id"], unique=False)

    # --- task_bundles: per-task placeholder (never written) -> per-drone CBBA bundle
    op.execute("DELETE FROM task_bundles")
    op.drop_index("idx_task_bundles_geometry", table_name="task_bundles", postgresql_using="gist", if_exists=True)
    for column in ("eta", "estimated_cost", "task_id", "priority", "geometry"):
        op.drop_column("task_bundles", column)
    op.add_column("task_bundles", sa.Column("plan_id", sa.UUID(), nullable=False))
    op.add_column("task_bundles", sa.Column("color_index", sa.Integer(), server_default="0", nullable=False))
    op.add_column("task_bundles", sa.Column("task_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("task_bundles", sa.Column("path", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("task_bundles", sa.Column("waypoints", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("task_bundles", sa.Column("route", _geog("LINESTRING"), nullable=True))
    for column in ("distance_m", "eta_seconds", "energy_pct", "battery_start_pct", "battery_end_pct", "coverage_pct"):
        op.add_column("task_bundles", sa.Column(column, sa.Float(), server_default="0", nullable=False))
    op.alter_column("task_bundles", "status", server_default="assigned")
    op.create_index("idx_task_bundles_route", "task_bundles", ["route"], unique=False, postgresql_using="gist")
    op.create_index(op.f("ix_task_bundles_plan_id"), "task_bundles", ["plan_id"], unique=False)
    op.create_foreign_key("fk_task_bundles_plan_id", "task_bundles", "mission_plans", ["plan_id"], ["id"], ondelete="CASCADE")

    op.create_table(
        "task_assignments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("bundle_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("drone_id", sa.UUID(), nullable=True),
        sa.Column("bid", sa.Float(), nullable=False),
        sa.Column("assignment_order", sa.Integer(), nullable=False),
        sa.Column("eta_seconds", sa.Float(), nullable=False),
        sa.Column("energy_pct", sa.Float(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["bundle_id"], ["task_bundles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["drone_id"], ["drones.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plan_id"], ["mission_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["mission_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index(op.f("ix_task_assignments_plan_id"), "task_assignments", ["plan_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_task_assignments_plan_id"), table_name="task_assignments")
    op.drop_table("task_assignments")

    op.execute("DELETE FROM task_bundles")
    op.drop_constraint("fk_task_bundles_plan_id", "task_bundles", type_="foreignkey")
    op.drop_index(op.f("ix_task_bundles_plan_id"), table_name="task_bundles")
    op.drop_index("idx_task_bundles_route", table_name="task_bundles", postgresql_using="gist")
    for column in (
        "coverage_pct", "battery_end_pct", "battery_start_pct", "energy_pct", "eta_seconds", "distance_m",
        "route", "waypoints", "path", "task_count", "color_index", "plan_id",
    ):
        op.drop_column("task_bundles", column)
    op.alter_column("task_bundles", "status", server_default=None)
    op.add_column("task_bundles", sa.Column("geometry", _geog("POINT"), nullable=True))
    op.add_column("task_bundles", sa.Column("priority", sa.Integer(), nullable=False))
    op.add_column("task_bundles", sa.Column("task_id", sa.String(length=64), nullable=False))
    op.add_column("task_bundles", sa.Column("estimated_cost", sa.Float(), nullable=False))
    op.add_column("task_bundles", sa.Column("eta", sa.DateTime(timezone=True), nullable=True))
    op.create_index("idx_task_bundles_geometry", "task_bundles", ["geometry"], unique=False, postgresql_using="gist")

    op.drop_index(op.f("ix_mission_tasks_mission_id"), table_name="mission_tasks")
    op.drop_index("idx_mission_tasks_geometry", table_name="mission_tasks", postgresql_using="gist")
    op.drop_table("mission_tasks")
    op.drop_index(op.f("ix_mission_state_history_mission_id"), table_name="mission_state_history")
    op.drop_table("mission_state_history")
    op.drop_index(op.f("ix_mission_plans_mission_id"), table_name="mission_plans")
    op.drop_table("mission_plans")

    for column in ("validated_at", "validation_report", "config", "wizard_step", "template"):
        op.drop_column("missions", column)
    op.drop_column("drones", "endurance_minutes")
    op.drop_column("drones", "max_speed")

    op.drop_constraint("ck_geofences_type", "geofences", type_="check")
    op.execute("UPDATE geofences SET type = 'geofence' WHERE type IN ('inclusion', 'exclusion')")
    op.create_check_constraint("ck_geofences_type", "geofences", "type IN ('geofence', 'nfz')")
