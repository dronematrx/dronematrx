import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# inclusion: drones must stay inside; exclusion: drones must stay outside;
# nfz: regulatory no-fly zone, always enforced and never optional.
VALID_GEOFENCE_TYPES = ("inclusion", "exclusion", "nfz")
# Sprint 2 called inclusion zones plain "geofence"; still accepted on input.
LEGACY_TYPE_ALIASES = {"geofence": "inclusion"}


class Geofence(Base):
    __tablename__ = "geofences"
    __table_args__ = (CheckConstraint(f"type IN {VALID_GEOFENCE_TYPES}", name="ck_geofences_type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="inclusion")

    geometry: Mapped[object] = mapped_column(Geography(geometry_type="POLYGON", srid=4326), nullable=False)

    altitude_min: Mapped[float] = mapped_column(Float, default=0)
    altitude_max: Mapped[float] = mapped_column(Float, default=120)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="geofences")  # noqa: F821
