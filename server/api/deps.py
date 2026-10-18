"""FastAPI 依赖：认证、管理员鉴权、限流辅助。"""

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from ..app.database import get_db
from ..app.models import User
from ..services.auth import get_user_by_token


def get_current_user(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization[len("Bearer "):].strip()
    user = get_user_by_token(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="会话无效或已过期")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def request_client_ip(request: Request) -> str:
    """提取客户端 IP：优先 X-Forwarded-For 首个（反向代理场景），
    其次 X-Real-IP，最后 request.client.host。"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    real = request.headers.get("x-real-ip", "")
    if real:
        return real.strip()
    return request.client.host if request.client else "unknown"
