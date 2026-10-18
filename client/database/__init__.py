"""本地数据库层：SQLite schema、连接管理、DAO。

设计要点：
- 所有关键写操作走显式事务（BEGIN IMMEDIATE 串行化写入）；
- 每条线程独立连接（thread-local），支持并发生成；
- WAL 模式 + busy_timeout，避免并发写锁竞争失败；
- 升级不清空数据：通过 PRAGMA user_version 管理 schema 版本。
"""

from .db import Database, utc_now_iso
from .schema import SCHEMA_VERSION

__all__ = ["Database", "utc_now_iso", "SCHEMA_VERSION"]
