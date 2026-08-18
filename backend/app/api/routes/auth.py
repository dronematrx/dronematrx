from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.repositories.audit_repository import AuditRepository
from app.schemas.user import LoginRequest, TokenResponse, UserOut
from app.services.auth_service import AuthService, InvalidCredentialsError

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    service = AuthService(db)
    try:
        user = service.authenticate(payload.email, payload.password)
    except InvalidCredentialsError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    access_token, refresh_token = service.issue_tokens(user)

    AuditRepository(db).record(
        user_id=user.id,
        action="login",
        resource="auth",
        resource_id=str(user.id),
        ip_address=request.client.host if request.client else None,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
def read_me(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current_user)
