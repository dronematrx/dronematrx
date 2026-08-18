from sqlalchemy.orm import Session

from app.core.security import create_access_token, create_refresh_token, verify_password
from app.models.user import User
from app.repositories.user_repository import UserRepository


class InvalidCredentialsError(Exception):
    pass


class AuthService:
    def __init__(self, db: Session):
        self.db = db
        self.users = UserRepository(db)

    def authenticate(self, email: str, password: str) -> User:
        user = self.users.get_by_email(email.lower())
        if user is None or not user.active or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("Invalid email or password")
        self.users.touch_last_login(user)
        return user

    def issue_tokens(self, user: User) -> tuple[str, str]:
        claims = {"role": user.role, "org_id": str(user.organization_id)}
        access_token = create_access_token(str(user.id), claims)
        refresh_token = create_refresh_token(str(user.id))
        return access_token, refresh_token
