"""CodeLabel Studio 服务端应用入口。

运行方式：
    uvicorn server.main:app --host 0.0.0.0 --port 8000

职责：
- 应用工厂 create_app()（便于测试注入不同配置）；
- CORS 白名单、安全响应头、访问日志中间件；
- /health 健康检查（不暴露内部细节）；
- 领域异常 -> HTTP 状态码统一映射；
- 路由聚合：auth / account / license / orders / admin。
"""

import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .app.config import settings
from .api import account, admin, auth, license, orders
from .app.database import SessionLocal
from .services.auth import AuthError
from .services.license import LicenseError
from .services.orders import OrderError
from .services.subscription import SubscriptionError
from .services.trial import TrialError

logger = logging.getLogger("codelabel.http")

API_PREFIX = "/api"


def create_app() -> FastAPI:
    app = FastAPI(
        title="CodeLabel Studio API",
        version="1.0.0",
        description="条码标签工作室在线服务：认证 / 试用 / 订阅 / 订单 / 授权 / 管理后台",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
    )

    # ---------- CORS（白名单，禁止 * + 凭据） ----------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
        max_age=600,
    )

    # ---------- 安全响应头 + 访问日志 ----------
    @app.middleware("http")
    async def security_and_log(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed rid=%s %s %s",
                request_id, request.method, request.url.path,
            )
            raise
        elapsed_ms = (time.monotonic() - started) * 1000
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Request-ID"] = request_id
        # 不记录查询串（可能含令牌），仅记录方法与路径
        logger.info(
            "rid=%s %s %s -> %s %.1fms",
            request_id, request.method, request.url.path,
            response.status_code, elapsed_ms,
        )
        return response

    # ---------- 领域异常统一映射 ----------
    @app.exception_handler(AuthError)
    async def _auth_err(_request, exc: AuthError):
        return JSONResponse(status_code=exc.status_code,
                            content={"detail": str(exc)})

    @app.exception_handler(OrderError)
    async def _order_err(_request, exc: OrderError):
        return JSONResponse(status_code=exc.status_code,
                            content={"detail": str(exc)})

    @app.exception_handler(LicenseError)
    async def _license_err(_request, exc: LicenseError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(TrialError)
    async def _trial_err(_request, exc: TrialError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(SubscriptionError)
    async def _sub_err(_request, exc: SubscriptionError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # ---------- 健康检查 ----------
    @app.get("/health", tags=["ops"])
    def health():
        db_ok = True
        try:
            with SessionLocal() as s:
                s.execute(text("SELECT 1"))
        except Exception:
            logger.exception("health check database probe failed")
            db_ok = False
        payload = {
            "status": "ok" if db_ok else "degraded",
            "env": settings.env,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        return JSONResponse(payload, status_code=200 if db_ok else 503)

    @app.get("/", tags=["ops"])
    def root():
        return {"service": "CodeLabel Studio API", "version": "1.0.0",
                "docs": "/docs" if not settings.is_production else None}

    # ---------- 路由 ----------
    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(license.router)
    app.include_router(orders.router)
    app.include_router(admin.router)
    return app


app = create_app()
