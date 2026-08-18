import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User
from app.repositories.user_repository import UserRepository

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user = UserRepository(db).get(uuid.UUID(payload["sub"]))
    if user is None or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


class RequireRole:
    """Backend-enforced RBAC gate. Frontend button-hiding is UX only --
    every mutating/sensitive route depends on one of these.
    """

    def __init__(self, *allowed_roles: str):
        self.allowed_roles = set(allowed_roles)

    def __call__(self, user: User = Depends(get_current_user)) -> User:
        if user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' is not permitted to perform this action",
            )
        return user


# Convenience role gates matching the RBAC matrix:
#   ADMIN    - full access
#   OPERATOR - fleet management + mission operations
#   OBSERVER - read-only
#   ANALYST  - read telemetry, missions, analytics
require_admin = RequireRole("ADMIN")
require_fleet_write = RequireRole("ADMIN", "OPERATOR")
require_mission_write = RequireRole("ADMIN", "OPERATOR")
require_any_authenticated = RequireRole("ADMIN", "OPERATOR", "OBSERVER", "ANALYST")
