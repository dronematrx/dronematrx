import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

VALID_MISSION_TYPES = ("SAR", "AGRI", "INSPECT", "PATROL", "SURVEY", "OTHER")
VALID_MISSION_STATUS = (
    "draft",
    "planning",
    "validating",
    "armed",
    "active",
    "completed",
    "aborted",
)
VALID_TEMPLATES = ("survey", "search", "inspection", "mapping", "agriculture", "custom")
VALID_EXECUTION_MODES = ("live", "simulation")


class Mission(Base):
    __tablename__ = "missions"
    __table_args__ = (
        CheckConstraint(f"mission_type IN {VALID_MISSION_TYPES}", name="ck_missions_type"),
        CheckConstraint(f"status IN {VALID_MISSION_STATUS}", name="ck_missions_status"),
        CheckConstraint(f"execution_mode IN {VALID_EXECUTION_MODES}", name="ck_missions_execution_mode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mission_type: Mapped[str] = mapped_column(String(20), nullable=False, default="OTHER")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")

    # AOI polygon drawn by the operator, stored as PostGIS geography.
    aoi_geometry: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POLYGON", srid=4326), nullable=True
    )

    # Mission planning wizard state (Sprint 3). `config` holds the validated
    # per-step sections (template, flight, sensors, constraints) so a draft
    # can be resumed from any browser; `wizard_step` is the furthest step the
    # operator reached.
    template: Mapped[str | None] = mapped_column(String(30), nullable=True)
    wizard_step: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # Sprint 5: "simulation" missions run only against Gazebo/PX4 SITL drones.
    execution_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="live", server_default="live")
    validation_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="missions")  # noqa: F821
    task_bundles: Mapped[list["TaskBundle"]] = relationship(back_populates="mission", cascade="all, delete-orphan", passive_deletes=True)  # noqa: F821
    tasks: Mapped[list["MissionTask"]] = relationship(back_populates="mission", cascade="all, delete-orphan", passive_deletes=True)  # noqa: F821
    plans: Mapped[list["MissionPlan"]] = relationship(back_populates="mission", cascade="all, delete-orphan", passive_deletes=True)  # noqa: F821
    state_history: Mapped[list["MissionStateHistory"]] = relationship(  # noqa: F821
        back_populates="mission", cascade="all, delete-orphan", passive_deletes=True
    )
