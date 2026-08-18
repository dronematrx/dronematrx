import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
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


class Mission(Base):
    __tablename__ = "missions"
    __table_args__ = (
        CheckConstraint(f"mission_type IN {VALID_MISSION_TYPES}", name="ck_missions_type"),
        CheckConstraint(f"status IN {VALID_MISSION_STATUS}", name="ck_missions_status"),
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

    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="missions")  # noqa: F821
    task_bundles: Mapped[list["TaskBundle"]] = relationship(back_populates="mission", cascade="all, delete-orphan")  # noqa: F821
