"""SQLite 连接管理：线程本地连接、显式事务、编号迁移、安全备份恢复。

备份恢复安全设计（审计发现 F-01）：
- restore() 恢复前自动生成 `.pre_restore_<UTC>` 备份；
- 源文件必须通过 integrity_check、必需表结构与 schema 版本兼容性校验；
- 经临时文件 + os.replace 原子替换，替换前清理过期 WAL/SHM；
- 恢复后执行 quick_check 并写 JSONL 操作日志；
- 全过程拒绝无关数据库文件与自恢复。
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .schema import (
    LEGACY_SCHEMA_SQL_V1,
    SCHEMA_SQL,
    SCHEMA_VERSION,
    _default_templates,
    migrate_1_to_2,
)

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

_REQUIRED_TABLES = (
    "barcode_records",
    "numbering_ranges",
    "print_templates",
    "operation_logs",
)


class DatabaseError(Exception):
    """数据库操作错误。"""


class RestoreError(DatabaseError):
    """恢复操作被拒绝或失败（原始数据库保持完好）。"""


class BackupError(DatabaseError):
    """备份失败。"""


def utc_now_iso():
    """当前 UTC 时间（ISO 8601，毫秒精度）。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Database:
    """本地业务数据库。

    - 每条线程持有独立 sqlite3 连接（线程本地存储 + 代次校验）；
    - transaction() 提供显式事务；immediate=True 使用 BEGIN IMMEDIATE
      预先获取写锁，配合 busy_timeout 实现跨线程/进程的安全串行写入；
    - backup()/restore() 使用 sqlite3 在线备份 API，恢复前自动备份。
    """

    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._connections = []           # [(conn, generation)]
        self._registry_lock = threading.Lock()
        self._generation = 0
        self._closed = False
        try:
            self._initialize()
        except BaseException:
            # 初始化失败：释放全部连接，避免文件锁泄漏导致临时目录无法清理
            self._invalidate_connections()
            raise

    # ---------- 连接 ----------
    @property
    def conn(self):
        if self._closed:
            raise RuntimeError("Database 已关闭")
        entry = getattr(self._local, "entry", None)
        if entry is not None and entry[1] == self._generation:
            return entry[0]
        # 代次不匹配：旧连接已失效（restore 替换文件或 close 后重开）
        if entry is not None:
            try:
                entry[0].close()
            except sqlite3.Error:
                pass
        # check_same_thread=False：连接由唯一线程使用，但允许跨线程回收
        c = sqlite3.connect(
            self.path, timeout=30.0, isolation_level=None, check_same_thread=False
        )
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA journal_mode=WAL")
        self._local.entry = (c, self._generation)
        with self._registry_lock:
            self._connections.append((c, self._generation))
        return c

    def _invalidate_connections(self):
        """关闭全部已注册连接并提升代次；后续 conn 访问按需重连。"""
        with self._registry_lock:
            conns = [c for c, _ in self._connections]
            self._connections.clear()
        for c in conns:
            try:
                c.close()
            except sqlite3.Error:
                pass
        self._generation += 1
        self._local.entry = None

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
        if version == 0:
            # 全新库：executescript 隐式提交，此处无既有数据，安全。
            self.conn.executescript(SCHEMA_SQL)
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version < SCHEMA_VERSION:
            self._run_migrations(version)
        self._ensure_default_templates()

    def _run_migrations(self, from_version):
        """编号迁移。失败时事务回滚，旧 schema 与数据保持完好。"""
        conns = []
        with self._registry_lock:
            conns = [c for c, _ in self._connections]
        # PRAGMA foreign_keys 在事务内无效，必须先于 BEGIN 关闭
        for c in conns:
            c.execute("PRAGMA foreign_keys=OFF")
        try:
            conn = self.conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                if from_version < 2:
                    migrate_1_to_2(conn, utc_now_iso())
                if from_version < SCHEMA_VERSION:
                    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise DatabaseError(
                    "数据库迁移失败：旧数据不满足新版本约束（常见原因："
                    "gs1_authorized 编号范围缺少授权来源编号）。"
                    "已回滚，原始数据完好，请修正后重试。原始错误: " + str(exc)
                ) from exc
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            for c in conns:
                try:
                    c.execute("PRAGMA foreign_keys=ON")
                except sqlite3.Error:
                    pass

    def _ensure_default_templates(self):
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

    # ---------- 完整性 ----------
    def verify_integrity(self):
        """quick_check：'ok' 返回 True。"""
        row = self.conn.execute("PRAGMA quick_check").fetchone()
        return bool(row) and row[0] == "ok"

    # ---------- 备份 / 恢复 ----------
    def backup(self, dest):
        """在线备份到 dest，并校验目标完整性。"""
        dest = str(dest)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(dest)
        try:
            with target:
                self.conn.backup(target)
            row = target.execute("PRAGMA quick_check").fetchone()
            if not row or row[0] != "ok":
                raise BackupError(f"备份目标完整性检查失败: {dest}")
        except sqlite3.Error as exc:
            raise BackupError(f"备份失败: {exc}") from exc
        finally:
            target.close()

    def _validate_restore_source(self, src_path):
        """恢复前源校验：存在性、完整性、必需表、schema 版本兼容性。"""
        if not src_path.is_file():
            raise RestoreError(f"恢复源不存在或不是文件: {src_path}")
        if src_path.resolve() == Path(self.path).resolve():
            raise RestoreError("不能将数据库恢复为其自身")
        try:
            ro = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise RestoreError(f"无法打开恢复源: {exc}") from exc
        try:
            row = ro.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise RestoreError(f"恢复源完整性检查失败: {row[0] if row else '无结果'}")
            tables = {
                r[0] for r in ro.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing = [t for t in _REQUIRED_TABLES if t not in tables]
            if missing:
                raise RestoreError(
                    f"恢复源不是本软件的数据库（缺少表: {', '.join(missing)}）"
                )
            version = ro.execute("PRAGMA user_version").fetchone()[0]
            if not (1 <= version <= SCHEMA_VERSION):
                raise RestoreError(
                    f"恢复源 schema 版本 {version} 不兼容"
                    f"（支持 1..{SCHEMA_VERSION}）"
                )
        except sqlite3.DatabaseError as exc:
            raise RestoreError(f"恢复源不是有效的 SQLite 数据库: {exc}") from exc
        finally:
            ro.close()

    def restore(self, src):
        """安全恢复（F-01 加固版）。

        步骤：源校验 → 恢复前自动备份 → 临时文件重建 → 临时文件校验 →
        关闭连接并原子替换（清理过期 WAL/SHM）→ 重新初始化（必要时迁移）→
        恢复后完整性检查 → JSONL 日志。任一步骤失败均保留原数据库。
        """
        src_path = Path(str(src))
        self._validate_restore_source(src_path)

        # 恢复前自动备份当前数据库
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
        pre_backup = f"{self.path}.pre_restore_{stamp}"
        self.backup(pre_backup)

        # 经临时文件重建，避免直接写入活动数据库
        tmp_path = f"{self.path}.restore_tmp"
        Path(tmp_path).unlink(missing_ok=True)
        src_conn = sqlite3.connect(str(src_path))
        dst_conn = sqlite3.connect(tmp_path)
        try:
            src_conn.backup(dst_conn)
        except sqlite3.Error as exc:
            raise RestoreError(f"恢复复制失败: {exc}") from exc
        finally:
            src_conn.close()
            dst_conn.close()

        try:
            chk = sqlite3.connect(tmp_path)
            try:
                row = chk.execute("PRAGMA quick_check").fetchone()
            finally:
                chk.close()
            if not row or row[0] != "ok":
                raise RestoreError("恢复临时文件完整性检查失败")

            # 原子替换：先失效全部连接，清理过期 WAL/SHM，再 replace
            self._invalidate_connections()
            for suffix in ("-wal", "-shm"):
                stale = Path(self.path + suffix)
                if stale.exists():
                    stale.unlink()
            os.replace(tmp_path, self.path)

            # 重新打开（v1 源将自动迁移到 v2）
            self._closed = False
            self._initialize()

            if not self.verify_integrity():
                raise RestoreError(
                    "恢复后完整性检查失败；恢复前备份保留在: " + pre_backup
                )
        except RestoreError:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise

        self._log_restore(src_path, pre_backup)
        return pre_backup

    def _log_restore(self, src_path, pre_backup):
        entry = {
            "time": utc_now_iso(),
            "operation": "restore",
            "source": str(src_path),
            "pre_restore_backup": pre_backup,
            "result": "success",
        }
        log_path = f"{self.path}.restore_log.jsonl"
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # 日志失败不影响恢复结果

    def close(self):
        """关闭所有线程的连接（可在任意线程调用）。"""
        self._closed = True
        self._invalidate_connections()
