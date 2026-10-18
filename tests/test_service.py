"""条码业务服务测试：单个/批量生成、导入校验、状态机（规格 3 / 19-A）。"""

import tempfile
import unittest

from client.barcode.service import BarcodeError, BarcodeService
from client.database.db import Database


class ServiceTestCase(unittest.TestCase):
    def make_service(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        db = Database(td.name + "/codelabel.db")
        self.addCleanup(db.close)
        return db, BarcodeService(db)


class TestGenerateSingle(ServiceTestCase):
    def test_explicit_partial_autofill(self):
        _, svc = self.make_service()
        rec = svc.generate_single("UPC-A", explicit_value="03600029145")
        self.assertEqual(rec["barcode_value"], "036000291452")
        self.assertEqual(rec["allocation_status"], "generated")
        self.assertEqual(rec["source_type"], "internal")

    def test_invalid_rejected_and_not_saved(self):
        _, svc = self.make_service()
        with self.assertRaises(BarcodeError):
            svc.generate_single("UPC-A", explicit_value="036000291453")
        with self.assertRaises(BarcodeError):
            svc.generate_single("EAN-13", explicit_value="123")
        self.assertEqual(svc.query_records(), [])

    def test_duplicate_rejected(self):
        _, svc = self.make_service()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        with self.assertRaises(BarcodeError):
            svc.generate_single("EAN-13", explicit_value="5901234123457")

    def test_range_type_mismatch_rejected(self):
        _, svc = self.make_service()
        rid = svc.create_range("范围", "EAN-13", "950", 100000000, 100000009)
        with self.assertRaises(BarcodeError):
            svc.generate_single("EAN-8", range_id=rid)

    def test_exactly_one_source_required(self):
        _, svc = self.make_service()
        with self.assertRaises(BarcodeError):
            svc.generate_single("EAN-13")
        rid = svc.create_range("范围", "EAN-13", "950", 100000000, 100000009)
        with self.assertRaises(BarcodeError):
            svc.generate_single("EAN-13", explicit_value="5901234123457", range_id=rid)

    def test_gs1_range_requires_authorization(self):
        _, svc = self.make_service()
        with self.assertRaises(BarcodeError):
            svc.create_range("GS1 范围", "EAN-13", "690", 1000000000, 1000000009,
                             source_type="gs1_authorized")
        rid = svc.create_range("GS1 范围", "EAN-13", "690", 100000000, 100000009,
                               source_type="gs1_authorized",
                               authorization_reference="GS1-CN-2026-0001")
        rec = svc.generate_single("EAN-13", range_id=rid)
        self.assertEqual(rec["source_type"], "gs1_authorized")


class TestGenerateBatch(ServiceTestCase):
    def test_batch_from_range(self):
        _, svc = self.make_service()
        rid = svc.create_range("批量范围", "EAN-13", "950", 100000000, 100000049)
        result = svc.generate_batch("EAN-13", count=30, range_id=rid)
        self.assertEqual(len(result["created"]), 30)
        self.assertFalse(result["exhausted"])
        values = [r["barcode_value"] for r in result["created"]]
        self.assertEqual(len(set(values)), 30)
        for v in values:
            rec = svc.get_record(v)
            self.assertEqual(rec["allocation_status"], "generated")
            self.assertEqual(rec["range_id"], rid)
        self.assertEqual(len(svc.get_history(values[0])), 1)

    def test_batch_count_must_be_positive(self):
        _, svc = self.make_service()
        rid = svc.create_range("批量范围", "EAN-13", "950", 100000000, 100000049)
        with self.assertRaises(BarcodeError):
            svc.generate_batch("EAN-13", count=0, range_id=rid)


class TestImport(ServiceTestCase):
    def test_validate_import_categories(self):
        _, svc = self.make_service()
        svc.generate_single("EAN-8", explicit_value="12345670")
        values = [
            "12345670",  # 库内已存在
            "96385074",  # 合法 EAN-8
            "96385075",  # 校验位错误
            "123",       # 长度错误
            "9638507X",  # 非数字
            "96385074",  # 清单内重复
        ]
        report = svc.validate_import(values, "EAN-8")
        self.assertEqual(report["total"], 6)
        self.assertEqual(report["valid"], ["96385074"])
        self.assertEqual(len(report["invalid"]), 3)
        self.assertEqual(len(report["duplicates_in_file"]), 1)
        self.assertEqual(len(report["already_exists"]), 1)

    def test_import_records_persists_valid_only(self):
        _, svc = self.make_service()
        result = svc.import_records(["96385074", "96385075", "12345670"], "EAN-8")
        self.assertEqual(result["imported"], 2)
        self.assertIsNotNone(svc.get_record("96385074"))
        self.assertIsNotNone(svc.get_record("12345670"))
        self.assertIsNone(svc.get_record("96385075"))
        # v2 起导入记录同样走白名单来源校验，默认 internal（F-03/F-06）
        self.assertEqual(svc.get_record("96385074")["source_type"], "internal")


class TestStatusFlow(ServiceTestCase):
    def test_full_lifecycle(self):
        _, svc = self.make_service()
        rec = svc.generate_single("EAN-13", explicit_value="5901234123457")
        self.assertEqual(rec["allocation_status"], "generated")

        rec = svc.allocate("5901234123457")
        self.assertEqual(rec["allocation_status"], "allocated")
        with self.assertRaises(BarcodeError):
            svc.allocate("5901234123457")

        rec = svc.mark_used("5901234123457")
        self.assertEqual(rec["allocation_status"], "used")

    def test_invalidate_keeps_history_and_blocks_reallocation(self):
        _, svc = self.make_service()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        rec = svc.invalidate("5901234123457", reason="打印错误")
        self.assertEqual(rec["allocation_status"], "invalidated")
        self.assertIsNotNone(rec["invalidated_at"])
        self.assertIsNotNone(svc.get_record("5901234123457"))  # 历史保留
        with self.assertRaises(BarcodeError):
            svc.generate_single("EAN-13", explicit_value="5901234123457")
        with self.assertRaises(BarcodeError):
            svc.invalidate("5901234123457")

    def test_update_details_only_when_generated(self):
        _, svc = self.make_service()
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        rec = svc.update_details("5901234123457", product_name="测试商品", notes="首批")
        self.assertEqual(rec["product_name"], "测试商品")
        svc.allocate("5901234123457")
        with self.assertRaises(BarcodeError):
            svc.update_details("5901234123457", product_name="改不了")


class TestQuery(ServiceTestCase):
    def test_query_filters(self):
        _, svc = self.make_service()
        svc.generate_single("EAN-13", explicit_value="5901234123457", product_name="商品A")
        svc.generate_single("UPC-A", explicit_value="036000291452", product_name="商品B")
        svc.generate_single("EAN-8", explicit_value="96385074", product_name="商品C")
        self.assertEqual(len(svc.query_records(barcode_type="EAN-13")), 1)
        self.assertEqual(len(svc.query_records(product_name="商品")), 3)
        self.assertEqual(len(svc.query_records(value="590")), 1)
        self.assertEqual(len(svc.query_records(status="generated")), 3)
        svc.allocate("96385074")
        self.assertEqual(len(svc.query_records(status="allocated")), 1)

    def test_stats(self):
        _, svc = self.make_service()
        rid = svc.create_range("统计范围", "EAN-13", "950", 100000000, 100000004)
        svc.generate_batch("EAN-13", count=5, range_id=rid)
        svc.generate_single("EAN-8", explicit_value="96385074")
        svc.invalidate("96385074")
        stats = svc.get_stats()
        self.assertEqual(stats["total"], 6)
        self.assertEqual(stats["by_status"]["generated"], 5)
        self.assertEqual(stats["by_status"]["invalidated"], 1)
        self.assertEqual(stats["ranges_exhausted"], 1)


if __name__ == "__main__":
    unittest.main()
