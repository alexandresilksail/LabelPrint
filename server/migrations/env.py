"""Alembic 迁移环境。

- 数据库连接来自 server.app.config.settings（环境变量 CLS_DATABASE_URL）；
- target_metadata 来自 server.app.models.Base（单一模型来源）；
- SQLite 使用 render_as_batch（ALTER TABLE 需要表重建语义）。
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 确保 server/ 在 sys.path（alembic 从任意目录启动都能 import app.*）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
import app.models  # noqa: E402,F401  （注册全部模型，保证 autogenerate 完整）

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 连接串以环境变量为准（覆盖 ini 中的占位）
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
