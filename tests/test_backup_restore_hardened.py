"""备份恢复加固测试（审计发现 F-01）。

验证：恢复前自动备份；拒绝不存在/非数据库/错误 schema/损坏/自恢复；
恢复后完整性检查；恢复操作日志；v1 备份恢复后自动迁移；
所有失败场景下原数据库保持完好。
"""

import json
import os
import sqlite3
import tempfile
import unittest

from client.barcode.service import BarcodeService
from client.database.db import Database, RestoreError
from client.database.schema import LEGACY_SCHEMA_SQL_V1


class RestoreTestCase(unittest.TestCase):
    def make_db(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        db = Database(td.name + "/codelabel.db")
        self.addCleanup(db.close)
        return db, BarcodeService(db)

    def make_v1_backup(self, db):
        """在临时目录构造一个带数据的 v1 备份文件。"""
        path = db.path + ".v1src"
        conn = sqlite3.connect(path)
        conn.executescript(LEGACY_SCHEMA_SQL_V1)
        conn.execute("PRAGMA user_version = 1")
        conn.execute(
            "INSERT INTO barcode_records (barcode_value, barcode_type, source_type,"
            " allocation_status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("5901234123457", "EAN-13", "internal", "generated",
             "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path


class TestRestoreRejections(RestoreTestCase):
    def test_missing_file_rejected(self):
        db, svc = self.make_db()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        with self.assertRaises(RestoreError):
            db.restore(db.path + ".does_not_exist")
        # 原数据库完好
        self.assertIsNotNone(svc.get_record("5901234123457"))

    def test_non_database_file_rejected(self):
        db, svc = self.make_db()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        bogus = db.path + ".bogus"
        with open(bogus, "w", encoding="utf-8") as fh:
            fh.write("这不是数据库，只是文本文件。")
        self.addCleanup(lambda: os.path.exists(bogus) and os.remove(bogus))
        with self.assertRaises(RestoreError):
            db.restore(bogus)
        self.assertIsNotNone(svc.get_record("5901234123457"))

    def test_unrelated_schema_rejected(self):
        db, svc = self.make_db()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        other = db.path + ".other"
        conn = sqlite3.connect(other)
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO unrelated (name) VALUES ('x')")
        conn.commit()
        conn.close()
        self.addCleanup(lambda: os.path.exists(other) and os.remove(other))
        with self.assertRaises(RestoreError):
            db.restore(other)
        self.assertIsNotNone(svc.get_record("5901234123457"))

    def test_corrupted_database_rejected(self):
        db, svc = self.make_db()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        corrupt = db.path + ".corrupt"
        db.backup(corrupt)
        # 从文件中部开始覆盖整页数据，制造确定性损坏
        size = os.path.getsize(corrupt)
        with open(corrupt, "r+b") as fh:
            fh.seek(max(0, (size // 2 // 4096) * 4096))
            fh.write(b"\xa5" * 4096)
        self.addCleanup(lambda: os.path.exists(corrupt) and os.remove(corrupt))
        with self.assertRaises(RestoreError):
            db.restore(corrupt)
        self.assertIsNotNone(svc.get_record("5901234123457"))

    def test_self_restore_rejected(self):
        db, svc = self.make_db()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        with self.assertRaises(RestoreError):
            db.restore(db.path)
        self.assertIsNotNone(svc.get_record("5901234123457"))

    def test_incompatible_version_rejected(self):
        db, svc = self.make_db()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        future = db.path + ".future"
        conn = sqlite3.connect(future)
        conn.executescript(LEGACY_SCHEMA_SQL_V1)
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
        conn.close()
        self.addCleanup(lambda: os.path.exists(future) and os.remove(future))
        with self.assertRaises(RestoreError):
            db.restore(future)
        self.assertIsNotNone(svc.get_record("5901234123457"))


class TestRestoreSuccess(RestoreTestCase):
    def test_restore_creates_pre_backup_and_log(self):
        db, svc = self.make_db()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        backup = db.path + ".backup"
        db.backup(backup)
        svc.generate_single("EAN-8", explicit_value="96385074")
        self.assertEqual(len(svc.query_records()), 2)

        pre = db.restore(backup)
        # 恢复前自动备份存在
        self.assertTrue(os.path.exists(pre))
        self.assertTrue(pre.startswith(db.path + ".pre_restore_"))
        # 恢复结果
        self.assertEqual(len(svc.query_records()), 1)
        self.assertIsNone(svc.get_record("96385074"))
        # 恢复日志
        log_path = db.path + ".restore_log.jsonl"
        self.assertTrue(os.path.exists(log_path))
        with open(log_path, encoding="utf-8") as fh:
            entry = json.loads(fh.readline())
        self.assertEqual(entry["operation"], "restore")
        self.assertEqual(entry["result"], "success")
        # 恢复后数据库仍可用且约束有效
        self.assertTrue(db.verify_integrity())
        svc.generate_single("EAN-8", explicit_value="96385074")
        self.assertEqual(len(svc.query_records()), 2)
        self.addCleanup(lambda: os.path.exists(backup) and os.remove(backup))
        self.addCleanup(lambda: os.path.exists(pre) and os.remove(pre))

    def test_restore_v1_source_auto_migrates(self):
        db, svc = self.make_db()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        v1 = self.make_v1_backup(db)

        db.restore(v1)
        # v1 备份中的记录恢复出来，并自动迁移到 v2
        rec = svc.get_record("5901234123457")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["source_type"], "internal")
        self.assertEqual(len(svc.query_records()), 1)
        self.assertEqual(
            db.conn.execute("PRAGMA user_version").fetchone()[0], 2
        )
        # 迁移后约束生效
        with self.assertRaises(sqlite3.IntegrityError):
            db.conn.execute(
                "INSERT INTO barcode_records (barcode_value, barcode_type,"
                " source_type, created_at, updated_at) VALUES (?,?,?,?,?)",
                ("1111111111117", "UPC-A", "import",
                 "2026-01-03T00:00:00+00:00", "2026-01-03T00:00:00+00:00"),
            )

    def test_backup_verifies_destination(self):
        db, svc = self.make_db()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        dest = db.path + ".verified_backup"
        self.addCleanup(lambda: os.path.exists(dest) and os.remove(dest))
        db.backup(dest)
        self.assertTrue(os.path.exists(dest))
        conn = sqlite3.connect(dest)
        try:
            self.assertEqual(
                conn.execute("PRAGMA quick_check").fetchone()[0], "ok"
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
