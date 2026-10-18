"""认证 API：注册 / 验证 / 登录 / 登出 / 忘记密码 / 重置密码。"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from ..app.database import get_db
from ..app.models import User
from ..services.auth import (
    AuthError, change_password, forgot_password, login, logout,
    register, resend_verification, reset_password, verify_email,
)
from .deps import get_current_user, request_client_ip

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    username: str
    password: str
    accept_terms: bool = False

    @field_validator("accept_terms")
    @classmethod
    def _must_accept(cls, v):
        if not v:
            raise ValueError("必须同意用户协议与隐私政策")
        return v


class VerifyEmailIn(BaseModel):
    token: str


class LoginIn(BaseModel):
    identifier: str
    password: str


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    new_password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


def _err(exc: AuthError):
    from fastapi import HTTPException
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/register", status_code=201)
def api_register(payload: RegisterIn, request: Request,
                 db: Session = Depends(get_db)):
    try:
        user = register(db, email=payload.email, username=payload.username,
                        password=payload.password,
                        client_ip=request_client_ip(request))
    except AuthError as exc:
        raise _err(exc)
    return {"user_id": user.id, "username": user.username,
            "email": user.email, "email_verified": False}


@router.post("/verify-email")
def api_verify_email(payload: VerifyEmailIn, db: Session = Depends(get_db)):
    try:
        user = verify_email(db, token=payload.token)
    except AuthError as exc:
        raise _err(exc)
    from ..services.trial import start_trial
    trial = start_trial(db, user)  # 首次验证后自动创建 7 天试用（幂等）
    return {"verified": True, "trial_expires_at": trial.expires_at.isoformat() + "Z"}


@router.post("/resend-verification")
def api_resend(payload: ForgotIn, request: Request,
               db: Session = Depends(get_db)):
    try:
        resend_verification(db, email=payload.email,
                            client_ip=request_client_ip(request))
    except AuthError as exc:
        raise _err(exc)
    return {"sent": True}


@router.post("/login")
def api_login(payload: LoginIn, request: Request, db: Session = Depends(get_db)):
    try:
        user, token = login(db, identifier=payload.identifier,
                            password=payload.password,
                            client_ip=request_client_ip(request))
    except AuthError as exc:
        raise _err(exc)
    return {
        "access_token": token,
        "token_type": "Bearer",  # nosec B105 - OAuth2 令牌类型标识，非密码
        "user": {"id": user.id, "username": user.username,
                 "email": user.email, "is_admin": user.is_admin},
    }


@router.post("/logout")
def api_logout(request: Request, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    auth = request.headers.get("authorization", "")
    token = auth.replace("Bearer ", "", 1).strip()
    logout(db, token=token)
    return {"logged_out": True}


@router.post("/forgot-password")
def api_forgot(payload: ForgotIn, request: Request, db: Session = Depends(get_db)):
    try:
        forgot_password(db, email=payload.email,
                        client_ip=request_client_ip(request))
    except AuthError as exc:
        raise _err(exc)
    return {"sent": True}


@router.post("/reset-password")
def api_reset(payload: ResetIn, db: Session = Depends(get_db)):
    try:
        reset_password(db, token=payload.token,
                       new_password=payload.new_password)
    except AuthError as exc:
        raise _err(exc)
    return {"reset": True}


@router.post("/change-password")
def api_change_password(payload: ChangePasswordIn,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    try:
        change_password(db, user, old_password=payload.old_password,
                        new_password=payload.new_password)
    except AuthError as exc:
        raise _err(exc)
    return {"changed": True}
