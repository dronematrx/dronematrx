import uuid
from datetime import datetime

from sqlalchemy import String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    logo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    subscription_tier: Mapped[str] = mapped_column(String(50), default="open_core", server_default="open_core")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="organization")
    drones: Mapped[list["Drone"]] = relationship(back_populates="organization")
    missions: Mapped[list["Mission"]] = relationship(back_populates="organization")
    geofences: Mapped[list["Geofence"]] = relationship(back_populates="organization")
