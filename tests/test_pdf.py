"""PDF 导出测试：真实 PDF 文档、物理尺寸精确、标签排版（阶段 2）。

验证方式：解析 PDF 字节流的 %PDF 头与 /MediaBox，
不依赖任何图像识别——尺寸即物理真相。
"""

import re
import tempfile
import unittest

from client.printing.pdf import (
    MAX_SHEET_PAGES,
    render_label_sheet_pdf,
    render_pdf,
)
from client.printing.templates import LabelTemplate


def mediaboxes(pdf_bytes):
    return [
        tuple(float(v) for v in m.split())
        for m in re.findall(rb"/MediaBox\s*\[([^\]]+)\]", pdf_bytes)
    ]


class PdfTestCase(unittest.TestCase):
    def out(self, name="b.pdf"):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return td.name + "/" + name


class TestSinglePdf(PdfTestCase):
    def test_valid_pdf_header(self):
        path = self.out()
        render_pdf("5901234123457", "EAN-13", path)
        data = open(path, "rb").read()
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertIn(b"%%EOF", data[-1024:])
        self.assertGreater(len(data), 500)

    def test_page_size_matches_mm(self):
        path = self.out()
        meta = render_pdf("5901234123457", "EAN-13", path,
                          module_mm=0.33, bar_height_mm=15.0,
                          show_text=True, margin_mm=5.0)
        boxes = mediaboxes(open(path, "rb").read())
        self.assertEqual(len(boxes), 1)
        w_pt, h_pt = boxes[0][2], boxes[0][3]
        # 1mm = 2.834645pt
        self.assertAlmostEqual(w_pt / 2.834645669, meta["page_width_mm"], places=1)
        self.assertAlmostEqual(h_pt / 2.834645669, meta["page_height_mm"], places=1)
        # 期望：95+22 模块 * 0.33 = 38.61mm + 10mm 边距
        self.assertAlmostEqual(meta["page_width_mm"], 38.61 + 10, places=1)

    def test_invalid_barcode_rejected(self):
        path = self.out()
        from client.barcode.checkdigit import BarcodeValidationError
        with self.assertRaises(BarcodeValidationError):
            render_pdf("123", "EAN-13", path)

    def test_no_text_variant(self):
        path = self.out()
        meta = render_pdf("96385074", "EAN-8", path, show_text=False)
        self.assertAlmostEqual(meta["page_height_mm"], 15.0 + 10.0, places=1)


class TestLabelSheetPdf(PdfTestCase):
    def make_template(self, **kw):
        base = dict(
            template_name="测试标签", paper_width=210, paper_height=297,
            label_width=60, label_height=40, rows=3, columns=3,
        )
        base.update(kw)
        return LabelTemplate(**base)

    def test_single_page_sheet(self):
        path = self.out()
        items = [("5901234123457", "EAN-13"), ("96385074", "EAN-8")]
        meta = render_label_sheet_pdf(items, self.make_template(), path)
        data = open(path, "rb").read()
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertEqual(meta["pages"], 1)
        self.assertEqual(meta["labels_total"], 2)
        self.assertEqual(meta["dropped"], 0)
        boxes = mediaboxes(data)
        self.assertEqual(len(boxes), 1)
        self.assertAlmostEqual(boxes[0][2] / 2.834645669, 210, places=1)
        self.assertAlmostEqual(boxes[0][3] / 2.834645669, 297, places=1)

    def test_multi_page_when_overflow(self):
        from client.barcode.checkdigit import calculate_check_digit
        path = self.out()
        items = []
        for i in range(20):
            base = f"5901234123{i:02d}"[:12]
            items.append((base + calculate_check_digit(base, "EAN-13"), "EAN-13"))
        meta = render_label_sheet_pdf(items, self.make_template(), path)
        self.assertEqual(meta["pages"], 3)  # 9/页 -> 20 项需 3 页
        self.assertEqual(meta["labels_total"], 20)
        boxes = mediaboxes(open(path, "rb").read())
        self.assertEqual(len(boxes), 3)

    def test_small_template_paginates_without_drop(self):
        path = self.out()
        items = [("5901234123457", "EAN-13")] * 10
        meta = render_label_sheet_pdf(
            items, self.make_template(rows=1, columns=2), path)
        self.assertEqual(meta["pages"], 5)  # 2 slot/页 -> 10 项需 5 页
        self.assertEqual(meta["labels_total"], 10)
        self.assertEqual(meta["dropped"], 0)
        boxes = mediaboxes(open(path, "rb").read())
        self.assertEqual(len(boxes), 5)

    def test_dropped_reported(self):
        path = self.out()
        items = [("5901234123457", "EAN-13")] * (MAX_SHEET_PAGES * 2 + 50)
        meta = render_label_sheet_pdf(
            items, self.make_template(rows=1, columns=2), path)
        self.assertEqual(meta["pages"], MAX_SHEET_PAGES)
        self.assertEqual(meta["labels_total"], MAX_SHEET_PAGES * 2)
        self.assertEqual(meta["dropped"], 50)
        self.assertTrue(any("超出" in w for w in meta["warnings"]))

    def test_invalid_template_rejected(self):
        path = self.out()
        bad = LabelTemplate(template_name="x", label_width=0)
        with self.assertRaises(ValueError):
            render_label_sheet_pdf([("5901234123457", "EAN-13")], bad, path)

    def test_copies_multiplier(self):
        path = self.out()
        items = [("5901234123457", "EAN-13")]
        meta = render_label_sheet_pdf(
            items, self.make_template(rows=1, columns=4, copies=3), path)
        self.assertEqual(meta["labels_total"], 3)
        self.assertEqual(meta["dropped"], 0)


if __name__ == "__main__":
    unittest.main()
