"""来源校验统一性测试（审计发现 F-03）：所有入口 + DAO 绕过防护。

规格要求：所有生成、导入、编辑和编号分配入口统一使用同一套业务验证；
对来源类型执行白名单验证；GS1 授权来源必须具备授权编号；
禁止仅通过客户端参数将编码标记为已授权。
"""

import sqlite3
import tempfile
import unittest

from client.barcode.service import BarcodeError, BarcodeService, validate_source_type
from client.database.dao import BarcodeRecordDAO, NumberingRangeDAO
from client.database.db import Database, utc_now_iso


class SourceValidationTestCase(unittest.TestCase):
    def make_service(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        db = Database(td.name + "/codelabel.db")
        self.addCleanup(db.close)
        return db, BarcodeService(db)


class TestValidateSourceType(SourceValidationTestCase):
    def test_whitelist(self):
        self.assertEqual(validate_source_type("internal"), "internal")
        self.assertEqual(
            validate_source_type("gs1_authorized", "GS1-CN-2026-0001"),
            "gs1_authorized",
        )

    def test_invalid_source_type_rejected(self):
        for bad in ("import", "gs1", "GS1_AUTHORIZED", "", None, "internal ", 123):
            with self.assertRaises(BarcodeError, msg=f"来源类型 {bad!r} 应被拒绝"):
                validate_source_type(bad)

    def test_gs1_requires_reference(self):
        for bad in (None, "", "   "):
            with self.assertRaises(BarcodeError, msg=f"授权编号 {bad!r} 应被拒绝"):
                validate_source_type("gs1_authorized", bad)

    def test_internal_ignores_reference(self):
        # internal 不需要授权编号，提供了也不记录
        self.assertEqual(validate_source_type("internal", "X"), "internal")


class TestGenerateSingleSourceValidation(SourceValidationTestCase):
    def test_explicit_invalid_source_rejected(self):
        _, svc = self.make_service()
        with self.assertRaises(BarcodeError):
            svc.generate_single("EAN-13", explicit_value="5901234123457",
                                source_type="import")
        with self.assertRaises(BarcodeError):
            svc.generate_single("EAN-13", explicit_value="5901234123457",
                                source_type="bogus")
        self.assertEqual(svc.query_records(), [])

    def test_explicit_gs1_without_reference_rejected(self):
        _, svc = self.make_service()
        with self.assertRaises(BarcodeError):
            svc.generate_single("EAN-13", explicit_value="5901234123457",
                                source_type="gs1_authorized")
        self.assertEqual(svc.query_records(), [])

    def test_explicit_gs1_with_reference_stored(self):
        _, svc = self.make_service()
        rec = svc.generate_single("EAN-13", explicit_value="5901234123457",
                                  source_type="gs1_authorized",
                                  authorization_reference="GS1-CN-2026-0001")
        self.assertEqual(rec["source_type"], "gs1_authorized")
        self.assertEqual(rec["authorization_reference"], "GS1-CN-2026-0001")


class TestImportSourceValidation(SourceValidationTestCase):
    def test_import_invalid_source_rejected(self):
        _, svc = self.make_service()
        with self.assertRaises(BarcodeError):
            svc.import_records(["96385074"], "EAN-8", source_type="import")
        with self.assertRaises(BarcodeError):
            svc.import_records(["96385074"], "EAN-8", source_type="bogus")
        self.assertEqual(svc.query_records(), [])

    def test_import_gs1_without_reference_rejected(self):
        _, svc = self.make_service()
        with self.assertRaises(BarcodeError):
            svc.import_records(["96385074"], "EAN-8",
                               source_type="gs1_authorized")
        self.assertEqual(svc.query_records(), [])

    def test_import_gs1_with_reference_ok(self):
        _, svc = self.make_service()
        result = svc.import_records(["96385074"], "EAN-8",
                                    source_type="gs1_authorized",
                                    authorization_reference="GS1-CN-2026-0002")
        self.assertEqual(result["imported"], 1)
        rec = svc.get_record("96385074")
        self.assertEqual(rec["source_type"], "gs1_authorized")
        self.assertEqual(rec["authorization_reference"], "GS1-CN-2026-0002")


class TestCreateRangeSourceValidation(SourceValidationTestCase):
    def test_invalid_source_rejected(self):
        _, svc = self.make_service()
        with self.assertRaises(BarcodeError):
            svc.create_range("R", "EAN-13", "950", 100000000, 100000009,
                             source_type="import")

    def test_empty_name_rejected(self):
        _, svc = self.make_service()
        with self.assertRaises(BarcodeError):
            svc.create_range("  ", "EAN-13", "950", 100000000, 100000009)


class TestRangePropagatesAuthorization(SourceValidationTestCase):
    def test_batch_from_gs1_range_stores_reference(self):
        _, svc = self.make_service()
        rid = svc.create_range("GS1 范围", "EAN-13", "690", 100000000, 100000009,
                               source_type="gs1_authorized",
                               authorization_reference="GS1-CN-2026-0003")
        result = svc.generate_batch("EAN-13", count=3, range_id=rid)
        self.assertEqual(len(result["created"]), 3)
        for rec in result["created"]:
            self.assertEqual(rec["source_type"], "gs1_authorized")
            self.assertEqual(rec["authorization_reference"], "GS1-CN-2026-0003")


class TestDAOBypassProtection(SourceValidationTestCase):
    """数据库层 CHECK 约束兜底：DAO 直写非法值必须触发 IntegrityError。"""

    def raw_insert_record(self, db, **overrides):
        params = {
            "barcode_value": "5901234123457",
            "barcode_type": "EAN-13",
            "source_type": "internal",
            "authorization_reference": None,
            "allocation_status": "generated",
            "product_name": None,
            "notes": None,
            "range_id": None,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
        params.update(overrides)
        cols = ", ".join(params)
        marks = ", ".join("?" for _ in params)
        db.conn.execute(
            f"INSERT INTO barcode_records ({cols}) VALUES ({marks})",
            tuple(params.values()),
        )

    def test_dao_invalid_source_type_rejected(self):
        db, _ = self.make_service()
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_insert_record(db, source_type="import")

    def test_dao_gs1_without_reference_rejected(self):
        db, _ = self.make_service()
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_insert_record(db, source_type="gs1_authorized")
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_insert_record(db, source_type="gs1_authorized",
                                   authorization_reference="   ")

    def test_dao_invalid_status_rejected(self):
        db, _ = self.make_service()
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_insert_record(db, allocation_status="bogus")

    def test_dao_invalid_barcode_type_rejected(self):
        db, _ = self.make_service()
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_insert_record(db, barcode_type="CODE128")

    def test_dao_non_digit_value_rejected(self):
        db, _ = self.make_service()
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_insert_record(db, barcode_value="59012341234X7")

    def test_dao_bad_length_rejected(self):
        db, _ = self.make_service()
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_insert_record(db, barcode_value="123456789")  # 9 位不合法

    def test_dao_unknown_range_fk_rejected(self):
        db, _ = self.make_service()
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_insert_record(db, range_id=99999)

    def test_range_dao_constraints(self):
        db, _ = self.make_service()
        dao = NumberingRangeDAO(db)
        base = dict(barcode_type="EAN-13", source_type="internal")
        ok = dict(prefix="950", start_number=100, end_number=109, next_number=100)
        with self.assertRaises(sqlite3.IntegrityError):
            dao.insert(range_name="R", **base, **{**ok, "next_number": 999})  # 越界
        with self.assertRaises(sqlite3.IntegrityError):
            dao.insert(range_name="R", **base, **{**ok, "end_number": 50})  # end < start
        with self.assertRaises(sqlite3.IntegrityError):
            dao.insert(range_name="R", **base, **{**ok, "prefix": "9X0"})  # 非数字前缀
        with self.assertRaises(sqlite3.IntegrityError):
            dao.insert(range_name="R", **base, **{**ok, "range_status": "bogus"})
        with self.assertRaises(sqlite3.IntegrityError):
            dao.insert(range_name="  ", **base, **ok)  # 空名称
        with self.assertRaises(sqlite3.IntegrityError):
            # gs1_authorized 范围缺少授权编号
            dao.insert(range_name="R", barcode_type="EAN-13", **ok,
                       source_type="gs1_authorized")


if __name__ == "__main__":
    unittest.main()
