"""SVG 导出测试：真实矢量结构（阶段 2）。"""

import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

from client.barcode.svg import render_svg

NS = "{http://www.w3.org/2000/svg}"


class SvgTestCase(unittest.TestCase):
    def render(self, value, btype, **kw):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = td.name + "/barcode.svg"
        svg, meta = render_svg(value, btype, path, **kw)
        with open(path, encoding="utf-8") as fh:
            self._written = fh.read()
        return svg, meta, path


class TestSvgStructure(SvgTestCase):
    def test_valid_xml_and_root(self):
        svg, meta, _ = self.render("5901234123457", "EAN-13")
        root = ET.fromstring(svg)
        self.assertEqual(root.tag, NS + "svg")
        self.assertIn("viewBox", root.attrib)
        self.assertTrue(root.attrib["width"].endswith("mm"))

    def test_rect_count_matches_black_modules(self):
        from client.barcode.encoder import encode_with_quiet_zone
        svg, meta, _ = self.render("5901234123457", "EAN-13")
        modules = encode_with_quiet_zone("5901234123457", "EAN-13", 11)
        black = modules.count("1")
        root = ET.fromstring(svg)
        rects = [r for r in root.iter(NS + "rect")]
        # 1 个白色背景 + black 个黑色条
        self.assertEqual(len(rects), black + 1)
        fills = {r.get("fill") for r in rects}
        self.assertEqual(fills, {"#ffffff", "#000000"})

    def test_dimensions_match_meta(self):
        svg, meta, _ = self.render("5901234123457", "EAN-13",
                                   module_width=0.33, bar_height=15.0,
                                   quiet_zone=11)
        total_modules = 95 + 2 * 11
        self.assertAlmostEqual(meta["width_mm"], total_modules * 0.33, places=6)
        self.assertAlmostEqual(meta["height_mm"], 15.0 + 3.0 + 1.0, places=6)
        root = ET.fromstring(svg)
        vb = [float(v) for v in root.attrib["viewBox"].split()]
        self.assertEqual(len(vb), 4)
        self.assertAlmostEqual(vb[2], meta["width_mm"], places=6)

    def test_show_text_false(self):
        svg, meta, _ = self.render("5901234123457", "EAN-13", show_text=False)
        root = ET.fromstring(svg)
        texts = list(root.iter(NS + "text"))
        self.assertEqual(len(texts), 0)

    def test_show_text_true_groups(self):
        svg, meta, _ = self.render("5901234123457", "EAN-13", show_text=True)
        root = ET.fromstring(svg)
        texts = [t.text for t in root.iter(NS + "text")]
        # EAN-13 分三组：首位、左半、右半
        self.assertEqual(len(texts), 3)
        self.assertEqual("".join(texts), "5901234123457")

    def test_all_formats(self):
        for value, btype in [
            ("036000291452", "UPC-A"),
            ("5901234123457", "EAN-13"),
            ("96385074", "EAN-8"),
        ]:
            svg, meta, _ = self.render(value, btype)
            root = ET.fromstring(svg)
            self.assertGreater(len(list(root.iter(NS + "rect"))), 20)

    def test_invalid_rejected(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        from client.barcode.checkdigit import BarcodeValidationError
        with self.assertRaises(BarcodeValidationError):
            render_svg("123", "EAN-13", td.name + "/x.svg")

    def test_file_written(self):
        _, _, path = self.render("96385074", "EAN-8")
        self.assertTrue(self._written.startswith("<?xml"))
        self.assertTrue(self._written.strip().endswith("</svg>"))


if __name__ == "__main__":
    unittest.main()
