import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Not the CBBA engine itself -- just a durable place for CBBA-generated
# task/drone assignments to land once the allocation algorithm exists (Sprint 3+).
VALID_TASK_BUNDLE_STATUS = ("pending", "assigned", "in_progress", "completed", "failed")


class TaskBundle(Base):
    __tablename__ = "task_bundles"
    __table_args__ = (CheckConstraint(f"status IN {VALID_TASK_BUNDLE_STATUS}", name="ck_task_bundles_status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"))
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    drone_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("drones.id", ondelete="SET NULL"), nullable=True)

    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    geometry: Mapped[object | None] = mapped_column(Geography(geometry_type="POINT", srid=4326), nullable=True)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0)
    eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    mission: Mapped["Mission"] = relationship(back_populates="task_bundles")  # noqa: F821
    drone: Mapped["Drone | None"] = relationship(back_populates="task_bundles")  # noqa: F821
