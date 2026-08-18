import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

VALID_STATUSES = ("online", "offline", "active", "maintenance", "warning", "critical")
VALID_GPS_STATUS = ("NO_FIX", "2D_FIX", "3D_FIX", "GPS_LOST")


class Drone(Base):
    __tablename__ = "drones"
    __table_args__ = (
        CheckConstraint(f"status IN {VALID_STATUSES}", name="ck_drones_status"),
        CheckConstraint(f"gps_status IN {VALID_GPS_STATUS}", name="ck_drones_gps_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    uin: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    vehicle_type: Mapped[str] = mapped_column(String(50), default="multirotor", server_default="multirotor")
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    firmware: Mapped[str] = mapped_column(String(50), nullable=False, default="PX4 v1.14")

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="offline")
    battery: Mapped[float] = mapped_column(Float, default=0)

    # PostGIS geography point (lon, lat) in WGS84. Never store lat/lon as bare strings.
    location: Mapped[object | None] = mapped_column(Geography(geometry_type="POINT", srid=4326), nullable=True)
    altitude: Mapped[float] = mapped_column(Float, default=0)
    heading: Mapped[float] = mapped_column(Float, default=0)
    speed: Mapped[float] = mapped_column(Float, default=0)

    gps_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NO_FIX")
    flight_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="STANDBY")
    armed: Mapped[bool] = mapped_column(Boolean, default=False)
    link_quality: Mapped[float] = mapped_column(Float, default=0)

    total_flight_hours: Mapped[float] = mapped_column(Float, default=0)
    battery_cycles: Mapped[int] = mapped_column(Integer, default=0)

    last_telemetry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="drones")  # noqa: F821
    payloads: Mapped[list["Payload"]] = relationship(back_populates="drone", cascade="all, delete-orphan")  # noqa: F821
    maintenance_records: Mapped[list["MaintenanceRecord"]] = relationship(  # noqa: F821
        back_populates="drone", cascade="all, delete-orphan"
    )
    task_bundles: Mapped[list["TaskBundle"]] = relationship(back_populates="drone")  # noqa: F821
