"""认证服务：注册、邮箱验证、登录、登出、密码重置。

安全要点：
- 注册/登录/验证/重置全部限流；
- 验证与重置令牌一次性、有有效期、库内仅存 SHA-256 哈希；
- 登录失败统一错误文案（防账户枚举）；
- 会话短期有效、可撤销。
"""

import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..app.models import (
    EmailVerification, PasswordResetToken, Session as SessionModel, User,
)
from ..app.security import (
    expires_at, generate_token, hash_password, hash_token, needs_rehash,
    rate_limiter, utc_now, verify_password,
)
from ..app.config import settings

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _validate_registration(email: str, username: str, password: str):
    if not EMAIL_RE.match(email or ""):
        raise AuthError("邮箱格式不正确")
    if not USERNAME_RE.match(username or ""):
        raise AuthError("用户名须为 3-32 位字母、数字、- 或 _")
    if not password or len(password) < 8:
        raise AuthError("密码至少 8 位")
    if len(password) > 128:
        raise AuthError("密码过长")


def register(db: Session, *, email: str, username: str, password: str,
             client_ip: str = "") -> User:
    if not rate_limiter.check(f"register:{client_ip}",
                              settings.rate_limit_register):
        raise AuthError("注册过于频繁，请稍后再试", status_code=429)
    _validate_registration(email, username, password)
    email = email.strip().lower()
    username = username.strip()
    user = User(
        email=email,
        username=username,
        password_hash=hash_password(password),
        account_status="pending",
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # 不区分是邮箱还是用户名重复，统一文案防枚举
        raise AuthError("该邮箱或用户名已被注册", status_code=409) from exc
    _issue_email_verification(db, user)
    db.commit()
    db.refresh(user)
    return user


def _issue_email_verification(db: Session, user: User) -> str:
    token = generate_token()
    ver = EmailVerification(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=utc_now() + timedelta(
            hours=settings.email_token_ttl_hours),
    )
    db.add(ver)
    db.flush()
    from ..app.mailer import send_mail, verification_email
    subject, body = verification_email(token)
    send_mail(user.email, subject, body)
    return token


def resend_verification(db: Session, *, email: str, client_ip: str = "") -> bool:
    if not rate_limiter.check(f"resend:{client_ip}", settings.rate_limit_email):
        raise AuthError("请求过于频繁，请稍后再试", status_code=429)
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user and not user.email_verified_at:
        _issue_email_verification(db, user)
        db.commit()
    # 无论邮箱是否存在都返回成功（防枚举）
    return True


def verify_email(db: Session, *, token: str) -> User:
    token_hash = hash_token(token)
    ver = db.scalar(
        select(EmailVerification).where(
            EmailVerification.token_hash == token_hash)
    )
    if not ver or ver.consumed_at is not None or ver.expires_at < utc_now():
        raise AuthError("验证链接无效或已过期", status_code=400)
    user = db.get(User, ver.user_id)
    if not user:
        raise AuthError("账号不存在", status_code=400)
    ver.consumed_at = utc_now()
    user.email_verified_at = utc_now()
    user.account_status = "active"
    db.commit()
    db.refresh(user)
    return user


def login(db: Session, *, identifier: str, password: str,
          client_ip: str = "") -> tuple[User, str]:
    if not rate_limiter.check(f"login:{client_ip}", settings.rate_limit_login):
        raise AuthError("登录尝试过于频繁，请稍后再试", status_code=429)
    ident = identifier.strip()
    user = db.scalar(
        select(User).where(
            (User.username == ident) | (User.email == ident.lower()))
    )
    # 统一错误文案，防账户枚举
    if not user or not verify_password(password, user.password_hash):
        raise AuthError("用户名/邮箱或密码错误", status_code=401)
    if user.account_status != "active":
        raise AuthError("账号尚未激活或已被停用", status_code=403)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = utc_now()
    token = _create_session(db, user)
    db.commit()
    return user, token


def _create_session(db: Session, user: User) -> str:
    token = generate_token()
    db.add(SessionModel(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=utc_now() + timedelta(hours=settings.session_ttl_hours),
    ))
    db.flush()
    return token


def logout(db: Session, *, token: str) -> bool:
    token_hash = hash_token(token)
    session = db.scalar(select(SessionModel).where(
        SessionModel.token_hash == token_hash))
    if session and session.revoked_at is None:
        session.revoked_at = utc_now()
        db.commit()
        return True
    return False


def get_user_by_token(db: Session, token: str) -> User | None:
    token_hash = hash_token(token)
    session = db.scalar(select(SessionModel).where(
        SessionModel.token_hash == token_hash))
    if not session or session.revoked_at is not None:
        return None
    if session.expires_at < utc_now():
        return None
    user = db.get(User, session.user_id)
    if not user or user.account_status != "active":
        return None
    session.last_seen_at = utc_now()
    return user


def forgot_password(db: Session, *, email: str, client_ip: str = "") -> bool:
    if not rate_limiter.check(f"forgot:{client_ip}", settings.rate_limit_email):
        raise AuthError("请求过于频繁，请稍后再试", status_code=429)
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user:
        token = generate_token()
        db.add(PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=utc_now() + timedelta(
                hours=settings.reset_token_ttl_hours),
        ))
        db.commit()
        from ..app.mailer import send_mail, reset_email
        subject, body = reset_email(token)
        send_mail(user.email, subject, body)
    return True  # 防枚举：统一返回


def reset_password(db: Session, *, token: str, new_password: str) -> User:
    if not new_password or len(new_password) < 8:
        raise AuthError("新密码至少 8 位")
    token_hash = hash_token(token)
    prt = db.scalar(select(PasswordResetToken).where(
        PasswordResetToken.token_hash == token_hash))
    if not prt or prt.consumed_at is not None or prt.expires_at < utc_now():
        raise AuthError("重置链接无效或已过期", status_code=400)
    user = db.get(User, prt.user_id)
    if not user:
        raise AuthError("账号不存在", status_code=400)
    prt.consumed_at = utc_now()
    user.password_hash = hash_password(new_password)
    # 重置后撤销全部既有会话（安全默认）
    db.query(SessionModel).where(
        SessionModel.user_id == user.id,
        SessionModel.revoked_at.is_(None),
    ).update({"revoked_at": utc_now()})
    db.commit()
    db.refresh(user)
    return user


def change_password(db: Session, user: User, *, old_password: str,
                    new_password: str) -> bool:
    if not verify_password(old_password, user.password_hash):
        raise AuthError("原密码错误", status_code=401)
    if len(new_password) < 8:
        raise AuthError("新密码至少 8 位")
    user.password_hash = hash_password(new_password)
    db.commit()
    return True
