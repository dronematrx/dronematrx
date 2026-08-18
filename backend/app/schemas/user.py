import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.user import VALID_ROLES


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    email: EmailStr
    full_name: str
    role: str
    active: bool
    last_login: datetime | None
    created_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


assert set(VALID_ROLES) == {"ADMIN", "OPERATOR", "OBSERVER", "ANALYST"}
