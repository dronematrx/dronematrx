import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

VALID_TASK_BUNDLE_STATUS = ("pending", "assigned", "in_progress", "completed", "failed")


class TaskBundle(Base):
    """One drone's CBBA output for one mission plan: ordered task path, the
    flown route, and its ETA/energy estimates (PRD TaskBundle)."""

    __tablename__ = "task_bundles"
    __table_args__ = (CheckConstraint(f"status IN {VALID_TASK_BUNDLE_STATUS}", name="ck_task_bundles_status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"))
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mission_plans.id", ondelete="CASCADE"), index=True)
    drone_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("drones.id", ondelete="SET NULL"), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="assigned")
    color_index: Mapped[int] = mapped_column(Integer, default=0)
    task_count: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[list] = mapped_column(JSONB, default=list)  # ordered task keys
    waypoints: Mapped[list] = mapped_column(JSONB, default=list)  # [[lon, lat], ...] incl. launch/return
    route: Mapped[object | None] = mapped_column(Geography(geometry_type="LINESTRING", srid=4326), nullable=True)

    distance_m: Mapped[float] = mapped_column(Float, default=0)
    eta_seconds: Mapped[float] = mapped_column(Float, default=0)  # time to finish the bundle and land
    energy_pct: Mapped[float] = mapped_column(Float, default=0)
    battery_start_pct: Mapped[float] = mapped_column(Float, default=0)
    battery_end_pct: Mapped[float] = mapped_column(Float, default=0)
    coverage_pct: Mapped[float] = mapped_column(Float, default=0)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    mission: Mapped["Mission"] = relationship(back_populates="task_bundles")  # noqa: F821
    plan: Mapped["MissionPlan"] = relationship(back_populates="bundles")  # noqa: F821
    drone: Mapped["Drone | None"] = relationship(back_populates="task_bundles")  # noqa: F821
