"""本地数据库测试：schema、唯一约束、范围分配、并发、事务回滚、备份恢复（规格 7 / 19-A/C）。"""

import os
import sqlite3
import tempfile
import threading
import unittest

from client.barcode.checkdigit import is_valid
from client.barcode.service import BarcodeService
from client.database.dao import BarcodeRecordDAO
from client.database.db import Database, utc_now_iso


class DbTestCase(unittest.TestCase):
    """提供带自动清理的临时数据库。"""

    def make_service(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        db = Database(td.name + "/codelabel.db")
        self.addCleanup(db.close)
        return db, BarcodeService(db)


class TestSchema(DbTestCase):
    def test_schema_and_presets(self):
        db, _ = self.make_service()
        version = db.conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 1)
        tables = {
            r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        self.assertTrue(
            {"barcode_records", "numbering_ranges", "print_templates", "operation_logs"} <= tables
        )
        templates = db.conn.execute("SELECT template_name FROM print_templates").fetchall()
        names = {t[0] for t in templates}
        self.assertEqual(
            names,
            {"标签 30x20 mm", "标签 40x25 mm", "标签 50x30 mm", "标签 60x40 mm"},
        )

    def test_reopen_preserves_data_and_does_not_duplicate_presets(self):
        db, svc = self.make_service()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        db.close()
        db2 = Database(db.path)
        self.addCleanup(db2.close)
        self.assertEqual(
            db2.conn.execute("SELECT COUNT(*) FROM barcode_records").fetchone()[0], 1
        )
        self.assertEqual(
            db2.conn.execute("SELECT COUNT(*) FROM print_templates").fetchone()[0], 4
        )


class TestUniqueConstraint(DbTestCase):
    def test_duplicate_value_rejected(self):
        db, svc = self.make_service()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        with self.assertRaises(sqlite3.IntegrityError):
            db.conn.execute(
                "INSERT INTO barcode_records (barcode_value, barcode_type, created_at, updated_at)"
                " VALUES (?,?,?,?)",
                ("5901234123457", "EAN-13", utc_now_iso(), utc_now_iso()),
            )


class TestRangeAllocation(DbTestCase):
    def test_exhaustion_safe_stop(self):
        _, svc = self.make_service()
        rid = svc.create_range("测试范围", "EAN-13", "950", 100000000, 100000019)
        result = svc.generate_batch("EAN-13", count=25, range_id=rid)
        self.assertEqual(len(result["created"]), 20)
        self.assertTrue(result["exhausted"])
        self.assertEqual(result["requested"], 25)
        rng = svc.get_range(rid)
        self.assertEqual(rng["range_status"], "exhausted")
        self.assertEqual(rng["next_number"], 100000019)
        self.assertEqual(svc.get_stats()["total"], 20)

    def test_range_value_format(self):
        _, svc = self.make_service()
        rid = svc.create_range("格式范围", "EAN-13", "950", 100000000, 100000004)
        result = svc.generate_batch("EAN-13", count=5, range_id=rid)
        values = [r["barcode_value"] for r in result["created"]]
        self.assertEqual(len(values), 5)
        self.assertEqual(len(set(values)), 5)
        for v in values:
            self.assertTrue(v.startswith("950"))
            self.assertEqual(len(v), 13)
            self.assertTrue(is_valid(v, "EAN-13"))
        bases = [int(v[3:-1]) for v in values]
        self.assertEqual(bases, [100000000, 100000001, 100000002, 100000003, 100000004])


class TestConcurrency(DbTestCase):
    def test_concurrent_allocation_no_duplicates(self):
        db, svc = self.make_service()
        rid = svc.create_range("并发范围", "EAN-13", "970", 200000000, 200000099)
        results, errors = [], []
        lock = threading.Lock()

        def worker():
            try:
                s = BarcodeService(db)  # 共享 Database，每线程独立连接
                r = s.generate_batch("EAN-13", count=10, range_id=rid)
                with lock:
                    results.extend(r["created"])
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        values = [r["barcode_value"] for r in results]
        self.assertEqual(len(values), 80)
        self.assertEqual(len(set(values)), 80, "并发分配产生了重复编码")
        rng = svc.get_range(rid)
        self.assertEqual(rng["next_number"], 200000080)
        self.assertEqual(svc.get_stats()["total"], 80)


class TestTransactionRollback(DbTestCase):
    def test_rollback_leaves_no_partial_write(self):
        db, _ = self.make_service()
        dao = BarcodeRecordDAO(db)
        with self.assertRaises(RuntimeError):
            with db.transaction(immediate=True) as conn:
                dao.insert(
                    barcode_value="1111111111117",
                    barcode_type="UPC-A",
                    conn=conn,
                )
                raise RuntimeError("模拟写入故障")
        self.assertFalse(dao.exists("1111111111117"))


class TestBackupRestore(DbTestCase):
    def test_backup_and_restore(self):
        db, svc = self.make_service()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        backup = db.path + ".backup"
        if os.path.exists(backup):
            os.remove(backup)
        self.addCleanup(lambda: os.path.exists(backup) and os.remove(backup))
        db.backup(backup)
        svc.generate_single("EAN-8", explicit_value="96385074")
        self.assertEqual(len(svc.query_records()), 2)
        db.restore(backup)
        self.assertEqual(len(svc.query_records()), 1)
        self.assertIsNotNone(svc.get_record("5901234123457"))
        self.assertIsNone(svc.get_record("96385074"))


class TestOperationLogs(DbTestCase):
    def test_generate_logged(self):
        _, svc = self.make_service()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        history = svc.get_history("5901234123457")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["operation_type"], "generate")
        self.assertEqual(history[0]["operation_result"], "success")


if __name__ == "__main__":
    unittest.main()
