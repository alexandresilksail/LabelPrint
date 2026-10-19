"""FastAPI 依赖：认证、管理员鉴权、客户端地址解析。"""

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from ..app.config import settings
from ..app.database import get_db
from ..app.models import User
from ..app.netutil import resolve_client_ip
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
    """客户端 IP：默认取连接对端，只有可信代理的转发头才被采纳。

    审计修复点：旧实现无条件信任 `X-Forwarded-For` 首个值，任意客户端
    只要改一个请求头就能绕过注册/登录限流。现在代理解析集中在
    `app.netutil.resolve_client_ip`，可信代理来自 `CLS_TRUSTED_PROXIES`。
    """
    peer = request.client.host if request.client else None
    return resolve_client_ip(
        peer,
        request.headers.get("x-forwarded-for"),
        request.headers.get("x-real-ip"),
        settings.trusted_networks,
    )
