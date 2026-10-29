"""hardware flight: real-aircraft drones and operator flight permissions

`drones.hardware` marks a real aircraft flown through the hardware bridge
(ArduPilot over MAVLink). It is fixed at registration: the development
telemetry simulator never drives a hardware drone, so real and simulated
telemetry can never mix.

`flight_permissions` records every human authorization to fly: who granted
it, the safety acknowledgements they confirmed, when it expires, and who
revoked it. ARM / TAKEOFF / GOTO / MISSION_START on hardware are refused
without an active permission.

Revision ID: e8a4c2f61b37
Revises: d6f2b8c4e715
Create Date: 2026-10-29 09:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e8a4c2f61b37"
down_revision: Union[str, None] = "d6f2b8c4e715"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("drones", sa.Column("hardware", sa.Boolean(), nullable=False, server_default="false"))
    op.create_check_constraint("ck_drones_not_sim_and_hardware", "drones", "NOT (simulated AND hardware)")
    op.create_table(
        "flight_permissions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("drone_id", UUID, sa.ForeignKey("drones.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mission_id", UUID, sa.ForeignKey("missions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("granted_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("granted_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("revoked_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoke_reason", sa.String(200), nullable=True),
        sa.Column("acknowledgements", JSONB, nullable=False, server_default="{}"),
        sa.Column("note", sa.String(500), nullable=True),
    )
    op.create_index("ix_flight_permissions_drone", "flight_permissions", ["drone_id", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_flight_permissions_drone", table_name="flight_permissions")
    op.drop_table("flight_permissions")
    op.drop_constraint("ck_drones_not_sim_and_hardware", "drones", type_="check")
    op.drop_column("drones", "hardware")
