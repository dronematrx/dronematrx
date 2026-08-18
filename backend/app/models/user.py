import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

VALID_ROLES = ("ADMIN", "OPERATOR", "OBSERVER", "ANALYST")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(f"role IN {VALID_ROLES}", name="ck_users_role"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="OBSERVER")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="users")  # noqa: F821
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="actor")  # noqa: F821
