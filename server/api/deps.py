"""FastAPI 依赖：认证、管理员鉴权、限流辅助。"""

from fastapi import Depends, Header, HTTPException
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


def client_ip(x_forwarded_for: str = Header(default=""),
              x_real_ip: str = Header(default="")) -> str:
    # 反向代理场景取 X-Forwarded-For 首个；直连取 X-Real-IP
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return x_real_ip or "unknown"
