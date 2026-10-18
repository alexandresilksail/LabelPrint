"""迁移一致性测试：alembic upgrade head 与 create_all 产出一致。

以子进程运行真实 alembic 命令（与文档给出的工作流一致），
验证：
1. upgrade head 建出与模型元数据完全一致的表集合；
2. downgrade base 可完全回滚；
3. 迁移文件本身可重复执行（幂等的版本链）。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = PROJECT_ROOT / "server"


def run_alembic(db_url: str, *args) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CLS_DATABASE_URL"] = db_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=SERVER_DIR, env=env, capture_output=True, text=True, timeout=180,
    )


def sqlite_tables(db_path: str) -> set:
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


class TestAlembicMigrations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cls_migration_test_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.db_path = os.path.join(self.tmp, "migration.sqlite3")
        self.db_url = f"sqlite:///{self.db_path}"

    def test_upgrade_head_matches_models(self):
        # 1) alembic upgrade head
        r = run_alembic(self.db_url, "upgrade", "head")
        self.assertEqual(r.returncode, 0, r.stderr)
        migrated = sqlite_tables(self.db_path)
        self.assertIn("users", migrated)
        self.assertIn("alembic_version", migrated)

        # 2) create_all 的模型元数据表集合
        import server.app.models  # noqa: F401  （注册模型，元数据才完整）
        from server.app.database import Base
        expected = set(Base.metadata.tables.keys())

        self.assertEqual(migrated - {"alembic_version"}, expected)
        # 版本表记录当前 head
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        versions = conn.execute(
            "SELECT version_num FROM alembic_version").fetchall()
        conn.close()
        self.assertEqual(len(versions), 1)

    def test_upgrade_idempotent(self):
        """重复 upgrade 不报错（已在 head）。"""
        r1 = run_alembic(self.db_url, "upgrade", "head")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        r2 = run_alembic(self.db_url, "upgrade", "head")
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_downgrade_base(self):
        r = run_alembic(self.db_url, "upgrade", "head")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run_alembic(self.db_url, "downgrade", "base")
        self.assertEqual(r.returncode, 0, r.stderr)
        remaining = sqlite_tables(self.db_path)
        self.assertEqual(remaining, {"alembic_version"})

    def test_history_lists_initial(self):
        r = run_alembic(self.db_url, "history")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("initial schema", r.stdout)


if __name__ == "__main__":
    unittest.main()
