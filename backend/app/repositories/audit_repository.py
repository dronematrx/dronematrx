import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


class AuditRepository:
    def __init__(self, db: Session):
        self.db = db

    def record(
        self,
        user_id: uuid.UUID | None,
        action: str,
        resource: str,
        resource_id: str | None = None,
        ip_address: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            user_id=user_id,
            action=action,
            resource=resource,
            resource_id=resource_id,
            ip_address=ip_address,
            event_metadata=metadata or {},
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry
