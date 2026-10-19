"""服务端数据库：SQLAlchemy 引擎与会话管理。"""

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


def _make_engine(url):
    if url.startswith("sqlite"):
        # 确保目录存在
        if url.startswith("sqlite:///"):
            db_path = url.replace("sqlite:///", "", 1)
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            url, connect_args={"check_same_thread": False}, future=True
        )

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _conn_record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True, future=True)


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False,
                            expire_on_commit=False, future=True)


def get_db():
    """FastAPI 依赖：请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all():
    """开发/测试用：直接建表。生产使用 Alembic 迁移。"""
    from . import models  # noqa: F401  （确保模型已注册）
    Base.metadata.create_all(engine)


REQUIRED_TABLES = frozenset({
    "users", "sessions", "plans", "subscriptions", "trials", "orders",
    "payment_events", "licenses", "devices", "audit_logs",
    "email_verifications", "password_reset_tokens", "outbox_messages",
})


def missing_tables() -> set[str]:
    """返回缺失的业务表（生产启动前校验，避免"能起来但一用就 500"）。"""
    from sqlalchemy import inspect
    existing = set(inspect(engine).get_table_names())
    return set(REQUIRED_TABLES) - existing
