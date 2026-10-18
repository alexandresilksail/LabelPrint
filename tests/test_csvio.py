"""CSV 导入导出测试：公式注入防护、往返、大小/行数限制（阶段 2 / 阶段 10）。"""

import os
import tempfile
import unittest

from client.barcode.csvio import (
    MAX_IMPORT_BYTES,
    MAX_IMPORT_ROWS,
    export_records_csv,
    read_import_csv,
    sanitize_cell,
)


class TestSanitizeCell(unittest.TestCase):
    def test_formula_prefixes_escaped(self):
        for prefix in ("=", "+", "-", "@", "\t", "\r"):
            self.assertTrue(sanitize_cell(prefix + "1+1").startswith("'"))
        self.assertEqual(sanitize_cell("=cmd|'/c calc'!A1"), "'=cmd|'/c calc'!A1")

    def test_safe_values_unchanged(self):
        for v in ("5901234123457", "商品 A", "", None, "0.5"):
            expected = "" if v is None else v
            self.assertEqual(sanitize_cell(v), expected)

    def test_negative_number_escaped(self):
        # 表格软件会把 -5 当公式/负数公式处理，统一转义
        self.assertEqual(sanitize_cell("-5"), "'-5")


class CsvRoundTripTestCase(unittest.TestCase):
    def make_records(self):
        return [
            {"barcode_value": "5901234123457", "barcode_type": "EAN-13",
             "source_type": "internal", "authorization_reference": "",
             "allocation_status": "generated", "product_name": "=危险公式",
             "notes": "备注,A", "created_at": "2026-01-01T00:00:00+00:00"},
            {"barcode_value": "96385074", "barcode_type": "EAN-8",
             "source_type": "gs1_authorized",
             "authorization_reference": "GS1-CN-2026-0001",
             "allocation_status": "allocated", "product_name": "@攻击",
             "notes": None, "created_at": "2026-01-02T00:00:00+00:00"},
        ]

    def test_export_import_roundtrip(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = td.name + "/export.csv"
        count = export_records_csv(self.make_records(), path)
        self.assertEqual(count, 2)
        values = read_import_csv(path)
        self.assertEqual(values, ["5901234123457", "96385074"])

    def test_export_escapes_formula_cells(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = td.name + "/export.csv"
        export_records_csv(self.make_records(), path)
        with open(path, encoding="utf-8-sig") as fh:
            content = fh.read()
        self.assertIn("'=危险公式", content)
        self.assertIn("'@攻击", content)
        # 首列编码不受影响
        self.assertIn("5901234123457", content)


class TestImportLimits(unittest.TestCase):
    def test_missing_file(self):
        with self.assertRaises(ValueError):
            read_import_csv("/nonexistent/path/x.csv")

    def test_header_and_blank_skipped(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = td.name + "/in.csv"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("barcode_value\n96385074\n\n12345670\n")
        self.assertEqual(read_import_csv(path), ["96385074", "12345670"])

    def test_too_large_rejected(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = td.name + "/big.csv"
        with open(path, "wb") as fh:
            fh.write(b"96385074\n" * (MAX_IMPORT_BYTES // 9 + 10))
        with self.assertRaises(ValueError) as ctx:
            read_import_csv(path)
        self.assertIn("过大", str(ctx.exception))

    def test_too_many_rows_rejected(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = td.name + "/many.csv"
        with open(path, "w", encoding="utf-8") as fh:
            for i in range(MAX_IMPORT_ROWS + 5):
                fh.write(f"9638507{i % 10}\n")
        with self.assertRaises(ValueError) as ctx:
            read_import_csv(path)
        self.assertIn("行数", str(ctx.exception))

    def test_gbk_encoding_supported(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = td.name + "/gbk.csv"
        with open(path, "w", encoding="gbk") as fh:
            fh.write("96385074\n12345670\n")
        self.assertEqual(read_import_csv(path), ["96385074", "12345670"])

    def test_undecodable_rejected(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = td.name + "/bin.csv"
        with open(path, "wb") as fh:
            fh.write(b"\xff\xfe\x00\x01\x80\x81" * 100)
        with self.assertRaises(ValueError):
            read_import_csv(path)


if __name__ == "__main__":
    unittest.main()
