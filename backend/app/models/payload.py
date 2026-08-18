import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

VALID_PAYLOAD_TYPES = ("camera", "thermal", "lidar", "multispectral", "sprayer", "custom")
VALID_PAYLOAD_STATUS = ("calibrated", "due", "uncalibrated", "fault")


class Payload(Base):
    __tablename__ = "payloads"
    __table_args__ = (
        CheckConstraint(f"type IN {VALID_PAYLOAD_TYPES}", name="ck_payloads_type"),
        CheckConstraint(f"status IN {VALID_PAYLOAD_STATUS}", name="ck_payloads_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    drone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drones.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    calibrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uncalibrated")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    drone: Mapped["Drone"] = relationship(back_populates="payloads")  # noqa: F821
