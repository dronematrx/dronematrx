import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

VALID_CATEGORIES = ("routine", "repair", "upgrade", "inspection")


class MaintenanceRecord(Base):
    __tablename__ = "maintenance_records"
    __table_args__ = (CheckConstraint(f"category IN {VALID_CATEGORIES}", name="ck_maintenance_category"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    drone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drones.id", ondelete="CASCADE"))
    service_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="routine")
    technician: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    flight_hours_at_service: Mapped[float] = mapped_column(Float, default=0)
    battery_cycles_at_service: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    drone: Mapped["Drone"] = relationship(back_populates="maintenance_records")  # noqa: F821
