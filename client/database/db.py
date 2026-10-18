"""SQLite 连接管理：线程本地连接、显式事务、备份恢复。"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .schema import SCHEMA_SQL, SCHEMA_VERSION, _default_templates

_INSERT_TEMPLATE = """
INSERT INTO print_templates
    (template_name, paper_width, paper_height, label_width, label_height,
     rows, columns, margins, horizontal_gap, vertical_gap, orientation,
     barcode_height, show_text, copies, offset_x, offset_y, builtin,
     created_at, updated_at)
VALUES
    (:template_name, :paper_width, :paper_height, :label_width, :label_height,
     :rows, :columns, :margins, :horizontal_gap, :vertical_gap, :orientation,
     :barcode_height, :show_text, :copies, :offset_x, :offset_y, :builtin,
     :created_at, :updated_at)
"""


def utc_now_iso():
    """当前 UTC 时间（ISO 8601，毫秒精度）。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Database:
    """本地业务数据库。

    - 每条线程持有独立 sqlite3 连接（线程本地存储）；
    - transaction() 提供显式事务；immediate=True 使用 BEGIN IMMEDIATE
      预先获取写锁，配合 busy_timeout 实现跨线程/进程的安全串行写入；
    - backup()/restore() 使用 sqlite3 在线备份 API。
    """

    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._connections = []
        self._registry_lock = threading.Lock()
        self._closed = False
        self._initialize()

    # ---------- 连接 ----------
    @property
    def conn(self):
        if self._closed:
            raise RuntimeError("Database 已关闭")
        c = getattr(self._local, "conn", None)
        if c is None:
            # check_same_thread=False：连接由唯一线程使用，但允许 close() 跨线程回收
            c = sqlite3.connect(
                self.path, timeout=30.0, isolation_level=None, check_same_thread=False
            )
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA journal_mode=WAL")
            self._local.conn = c
            with self._registry_lock:
                self._connections.append(c)
        return c

    # ---------- 事务 ----------
    @contextmanager
    def transaction(self, immediate=False):
        """显式事务上下文。异常时回滚。不支持嵌套。"""
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    # ---------- 初始化 / 迁移 ----------
    def _initialize(self):
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            # executescript 会隐式提交，不能置于显式事务内；
            # schema 全部使用 IF NOT EXISTS，重复执行安全。
            self.conn.executescript(SCHEMA_SQL)
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        count = self.conn.execute("SELECT COUNT(*) FROM print_templates").fetchone()[0]
        if count == 0:
            now = utc_now_iso()
            with self.transaction(immediate=True) as conn:
                for t in _default_templates():
                    params = dict(t)
                    params.setdefault("margins", '{"top":5,"right":5,"bottom":5,"left":5}')
                    params.setdefault("horizontal_gap", 3.0)
                    params.setdefault("vertical_gap", 3.0)
                    params.setdefault("orientation", "portrait")
                    params.setdefault("barcode_height", 15.0)
                    params.setdefault("show_text", 1)
                    params.setdefault("copies", 1)
                    params.setdefault("offset_x", 0.0)
                    params.setdefault("offset_y", 0.0)
                    params["created_at"] = now
                    params["updated_at"] = now
                    conn.execute(_INSERT_TEMPLATE, params)

    # ---------- 备份 / 恢复 ----------
    def backup(self, dest):
        dest = str(dest)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(dest)
        try:
            with target:
                self.conn.backup(target)
        finally:
            target.close()

    def restore(self, src):
        src_conn = sqlite3.connect(str(src))
        try:
            with src_conn:
                src_conn.backup(self.conn)
        finally:
            src_conn.close()

    def close(self):
        """关闭所有线程的连接（可在任意线程调用）。"""
        self._closed = True
        with self._registry_lock:
            conns = list(self._connections)
            self._connections.clear()
        for c in conns:
            try:
                c.close()
            except sqlite3.Error:
                pass
        self._local.conn = None
