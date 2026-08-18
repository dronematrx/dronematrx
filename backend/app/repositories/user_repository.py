import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return self.db.execute(stmt).scalar_one_or_none()

    def get(self, user_id: uuid.UUID) -> User | None:
        stmt = select(User).where(User.id == user_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def touch_last_login(self, user: User) -> User:
        from datetime import datetime, timezone

        user.last_login = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(user)
        return user
