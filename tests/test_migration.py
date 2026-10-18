"""v1 -> v2 迁移测试（审计发现 F-02 / F-05）。

验证：迁移保留全部数据；旧 import 来源映射为 internal；gs1_authorized
授权编号不丢失；迁移后 CHECK 约束生效；迁移失败时回滚且数据完好。
"""

import sqlite3
import tempfile
import unittest

from client.database.db import Database, DatabaseError
from client.database.schema import LEGACY_SCHEMA_SQL_V1


def build_v1_db(path):
    """构造一个带数据的 v1 旧库。"""
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA_SQL_V1)
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO barcode_records (barcode_value, barcode_type, source_type,"
        " allocation_status, product_name, notes, range_id, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("5901234123457", "EAN-13", "import", "generated", "旧商品A", "v1 导入",
         1, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO barcode_records (barcode_value, barcode_type, source_type,"
        " allocation_status, product_name, notes, range_id, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("96385074", "EAN-8", "gs1_authorized", "allocated", "旧商品B", None,
         1, "2026-01-02T00:00:00+00:00", "2026-01-02T00:00:00+00:00"),
    )
    # v1 无 CHECK 约束，gs1_authorized 也可以没有授权编号（脏数据场景另测）
    conn.execute(
        "INSERT INTO numbering_ranges (range_name, barcode_type, source_type, prefix,"
        " start_number, end_number, next_number, authorization_reference, range_status,"
        " created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("旧范围", "EAN-13", "gs1_authorized", "690", 100, 109, 103,
         "GS1-CN-2025-0009", "active",
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO operation_logs (operation_type, barcode_value, operation_time,"
        " operation_result) VALUES (?,?,?,?)",
        ("generate", "5901234123457", "2026-01-01T00:00:00+00:00", "success"),
    )
    conn.commit()
    conn.close()


class MigrationTestCase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.path = self.td.name + "/legacy.db"

    def open_raw(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn


class TestMigrationPreservesData(MigrationTestCase):
    def test_full_migration(self):
        build_v1_db(self.path)
        db = Database(self.path)
        self.addCleanup(db.close)

        # 版本升级
        self.assertEqual(
            db.conn.execute("PRAGMA user_version").fetchone()[0], 2
        )
        # 数据保留：两条记录都在
        rec_a = db.conn.execute(
            "SELECT * FROM barcode_records WHERE barcode_value = '5901234123457'"
        ).fetchone()
        rec_b = db.conn.execute(
            "SELECT * FROM barcode_records WHERE barcode_value = '96385074'"
        ).fetchone()
        self.assertIsNotNone(rec_a)
        self.assertIsNotNone(rec_b)
        # import -> internal
        self.assertEqual(rec_a["source_type"], "internal")
        self.assertEqual(rec_a["product_name"], "旧商品A")
        self.assertIsNone(rec_a["authorization_reference"])
        # gs1_authorized 保留（授权编号从所属范围继承）
        self.assertEqual(rec_b["source_type"], "gs1_authorized")
        self.assertEqual(rec_b["authorization_reference"], "GS1-CN-2025-0009")
        # 编号范围保留
        rng = db.conn.execute("SELECT * FROM numbering_ranges").fetchone()
        self.assertEqual(rng["range_name"], "旧范围")
        self.assertEqual(rng["authorization_reference"], "GS1-CN-2025-0009")
        self.assertEqual(rng["next_number"], 103)
        # 操作日志保留
        self.assertEqual(
            db.conn.execute("SELECT COUNT(*) FROM operation_logs").fetchone()[0], 1
        )
        # 索引重建
        idx = {
            r[0] for r in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        self.assertIn("idx_barcode_records_type", idx)
        # 预置模板不重复播种（v1 无模板时才播种；此处 v1 无模板 -> 播种 4 个）
        self.assertEqual(
            db.conn.execute("SELECT COUNT(*) FROM print_templates").fetchone()[0], 4
        )

    def test_constraints_active_after_migration(self):
        build_v1_db(self.path)
        db = Database(self.path)
        self.addCleanup(db.close)
        with self.assertRaises(sqlite3.IntegrityError):
            db.conn.execute(
                "INSERT INTO barcode_records (barcode_value, barcode_type,"
                " source_type, created_at, updated_at) VALUES (?,?,?,?,?)",
                ("1111111111117", "UPC-A", "import",
                 "2026-01-03T00:00:00+00:00", "2026-01-03T00:00:00+00:00"),
            )

    def test_migration_idempotent_reopen(self):
        build_v1_db(self.path)
        db = Database(self.path)
        db.close()
        db2 = Database(self.path)
        self.addCleanup(db2.close)
        self.assertEqual(
            db2.conn.execute("SELECT COUNT(*) FROM barcode_records").fetchone()[0], 2
        )


class TestMigrationEdgeCases(MigrationTestCase):
    def test_dirty_gs1_record_downgraded_with_audit(self):
        """v1 脏数据：gs1_authorized 记录无范围、无授权编号。
        迁移必须保留记录、降级为 internal，并写入审计日志（不伪造授权）。"""
        build_v1_db(self.path)
        conn = self.open_raw()
        conn.execute(
            "INSERT INTO barcode_records (barcode_value, barcode_type, source_type,"
            " allocation_status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("1111111111117", "UPC-A", "gs1_authorized", "generated",
             "2026-01-03T00:00:00+00:00", "2026-01-03T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()

        db = Database(self.path)
        self.addCleanup(db.close)

        rec = db.conn.execute(
            "SELECT * FROM barcode_records WHERE barcode_value = '1111111111117'"
        ).fetchone()
        self.assertIsNotNone(rec)  # 记录保留
        self.assertEqual(rec["source_type"], "internal")  # 降级
        self.assertIsNone(rec["authorization_reference"])
        # 审计日志
        logs = db.conn.execute(
            "SELECT * FROM operation_logs WHERE barcode_value = '1111111111117'"
        ).fetchall()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["operation_type"], "migrate_source_downgrade")
        # 其余数据不受影响
        self.assertEqual(
            db.conn.execute("SELECT COUNT(*) FROM barcode_records").fetchone()[0], 3
        )

    def test_gs1_range_without_reference_aborts_migration(self):
        """v1 脏数据：gs1_authorized 编号范围缺少授权编号（仅可能来自手工篡改）。
        迁移必须整体失败并回滚：旧 schema 与数据完好，不得删库重建。"""
        build_v1_db(self.path)
        conn = self.open_raw()
        conn.execute(
            "INSERT INTO numbering_ranges (range_name, barcode_type, source_type,"
            " prefix, start_number, end_number, next_number,"
            " authorization_reference, range_status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("脏范围", "EAN-13", "gs1_authorized", "690", 200, 209, 200,
             None, "active",
             "2026-01-03T00:00:00+00:00", "2026-01-03T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()

        with self.assertRaises(DatabaseError) as ctx:
            Database(self.path)
        self.assertIn("迁移失败", str(ctx.exception))

        # 回滚验证：版本仍为 1，原始数据完好，仍是 v1 表结构
        raw = self.open_raw()
        self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(
            raw.execute("SELECT COUNT(*) FROM barcode_records").fetchone()[0], 2
        )
        self.assertEqual(
            raw.execute("SELECT COUNT(*) FROM numbering_ranges").fetchone()[0], 2
        )
        cols = {r[1] for r in raw.execute("PRAGMA table_info(barcode_records)")}
        self.assertNotIn("authorization_reference", cols)  # 仍是 v1 结构
        raw.close()


if __name__ == "__main__":
    unittest.main()
