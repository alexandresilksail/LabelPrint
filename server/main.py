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
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .app import licensing
from .app.config import ConfigError, settings
from .api import account, admin, auth, license, orders
from .app.database import SessionLocal, missing_tables
from .services import outbox
from .services.auth import AuthError
from .services.license import LicenseError
from .services.orders import OrderError
from .services.subscription import SubscriptionError
from .services.trial import TrialError

logger = logging.getLogger("codelabel.http")

API_PREFIX = "/api"


def validate_runtime() -> None:
    """启动前校验：配置不满足要求就拒绝启动，而不是带病运行。

    - 生产：配置完整性 + 数据库表是否由迁移建好；
    - 全部环境：授权密钥签名/验签自检（密钥损坏时必须立刻暴露）。
    """
    if settings.is_production:
        settings.validate_for_production()
        missing = missing_tables()
        if missing:
            raise ConfigError(
                "生产数据库缺少表：" + "、".join(sorted(missing)) +
                "。请先执行 alembic upgrade head")
    licensing.self_test()
    logger.info("运行时校验通过 env=%s license_key_fp=%s key_source=%s "
                "limiter=%s workers=%s",
                settings.env, licensing.key_fingerprint(),
                licensing.key_source(), settings.rate_limit_backend,
                settings.workers)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """启动时补投上一次进程遗留的 outbox 消息（崩溃恢复）。"""
    try:
        with SessionLocal() as session:
            stats = outbox.dispatch_pending(session)
            if stats["scanned"]:
                logger.info("启动补投 outbox：%s", stats)
    except Exception:                 # noqa: BLE001 - 不应阻断启动
        logger.exception("启动补投 outbox 失败（不影响服务启动）")
    yield


def create_app() -> FastAPI:
    validate_runtime()
    app = FastAPI(
        title="CodeLabel Studio API",
        version="1.0.0",
        description="条码标签工作室在线服务：认证 / 试用 / 订阅 / 订单 / 授权 / 管理后台",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=_lifespan,
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
        pending = 0
        try:
            with SessionLocal() as s:
                s.execute(text("SELECT 1"))
                pending = outbox.pending_count(s)
        except Exception:
            logger.exception("health check database probe failed")
            db_ok = False
        payload = {
            "status": "ok" if db_ok else "degraded",
            "env": settings.env,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # 以下字段用于验证多实例配置一致性（不含任何密钥）
            "rate_limit_backend": settings.rate_limit_backend,
            "workers": settings.workers,
            "license_key_fp": licensing.key_fingerprint(),
            "outbox_pending": pending,
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

    # ---------- 管理后台前端（可选挂载） ----------
    # 生产建议由 Nginx 直接伺服 admin/ 静态目录；此处挂载仅为开发便利。
    # 注意：/admin 页面本身不含权限校验，鉴权在 /api/admin/* 强制完成。
    admin_dir = Path(__file__).resolve().parents[1] / "admin"
    if admin_dir.is_dir() and (admin_dir / "index.html").exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/admin", StaticFiles(directory=str(admin_dir),
                                        html=True), name="admin")
        logger.info("admin console mounted at /admin")
    return app


app = create_app()
