"""编码器结构与"渲染 -> 扫描解码"往返测试（规格 5 / 19-A）。

码表已通过 python-barcode（GS1 标准实现）逐模块交叉验证；
若环境安装了 python-barcode，test_cross_check_with_python_barcode
会自动执行该交叉验证，否则跳过。
"""

import os
import tempfile
import unittest

from client.barcode import decoder, encoder, renderer
from client.barcode.checkdigit import is_valid

VALID = {
    "UPC-A": "036000291452",
    "EAN-13": "5901234123457",
    "EAN-8": "96385074",
}


def _ref_module_string(barcode_obj):
    """从 python-barcode 渲染的 SVG 还原模块串（用于交叉验证）。"""
    import io
    import xml.etree.ElementTree as ET

    fp = io.BytesIO()
    barcode_obj.write(fp, options={"write_text": False})
    root = ET.fromstring(fp.getvalue().decode())
    ns = "{http://www.w3.org/2000/svg}"
    bars = []
    for el in root.iter(ns + "rect"):
        if el.get("style", "").replace(" ", "") == "fill:black;":
            bars.append(
                (
                    float(el.get("x").replace("mm", "")),
                    float(el.get("width").replace("mm", "")),
                )
            )
    bars.sort()
    x0 = bars[0][0]
    mw = min(w for _, w in bars)
    positions = {}
    for x, w in bars:
        n = round(w / mw)
        start = round((x - x0) / mw)
        for i in range(n):
            positions[start + i] = "1"
    total = max(positions) + 1
    return "".join(positions.get(i, "0") for i in range(total))


class TestCodeTables(unittest.TestCase):
    def test_table_internal_relationships(self):
        """EAN 码表内在关系：R = L 按位取反；G = R 反转。"""
        for d in "0123456789":
            flipped = "".join("1" if b == "0" else "0" for b in encoder.L_CODES[d])
            self.assertEqual(encoder.R_CODES[d], flipped, f"R/{d} 应为 L/{d} 的按位取反")
            self.assertEqual(encoder.G_CODES[d], encoder.R_CODES[d][::-1], f"G/{d} 应为 R/{d} 的反转")

    def test_parity_table_complete(self):
        self.assertEqual(set(encoder.PARITY_REVERSE), set(encoder.PARITY_PATTERNS.values()))
        self.assertEqual(len(encoder.PARITY_REVERSE), 10)

    def test_cross_check_with_python_barcode(self):
        try:
            from barcode import EAN13, EAN8, UPCA
            from barcode.writer import SVGWriter
        except ImportError:
            self.skipTest("python-barcode 未安装，跳过第三方交叉验证")
        cases = [
            (EAN13, "EAN-13", "5901234123457"),
            (EAN8, "EAN-8", "96385074"),
            (UPCA, "UPC-A", "036000291452"),
        ]
        for cls, btype, value in cases:
            ref = _ref_module_string(cls(value, writer=SVGWriter()))
            mine = encoder.encode_modules(value, btype)
            self.assertEqual(ref, mine, f"{btype} {value} 与 python-barcode 编码不一致")


class TestEncode(unittest.TestCase):
    def test_module_counts(self):
        self.assertEqual(encoder.module_count("UPC-A"), 95)
        self.assertEqual(encoder.module_count("EAN-13"), 95)
        self.assertEqual(encoder.module_count("EAN-8"), 67)

    def test_guard_structure(self):
        for btype, value in VALID.items():
            modules = encoder.encode_modules(value, btype)
            self.assertTrue(modules.startswith("101"))
            self.assertTrue(modules.endswith("101"))
            self.assertEqual(len(modules), encoder.module_count(btype))

    def test_upc_a_equals_ean13_with_leading_zero(self):
        self.assertEqual(
            encoder.encode_modules("036000291452", "UPC-A"),
            encoder.encode_modules("0036000291452", "EAN-13"),
        )

    def test_quiet_zone(self):
        modules = encoder.encode_with_quiet_zone("96385074", "EAN-8", quiet=11)
        self.assertEqual(len(modules), 67 + 22)
        self.assertTrue(modules.startswith("00000000000"))
        self.assertTrue(modules.endswith("00000000000"))

    def test_invalid_value_rejected(self):
        with self.assertRaises(Exception):
            encoder.encode_modules("96385075", "EAN-8")


class TestRenderDecodeRoundtrip(unittest.TestCase):
    """渲染 PNG 后直接从像素解码，验证扫描结果与原始数字一致。"""

    def test_png_roundtrip(self):
        for btype, value in VALID.items():
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, "barcode.png")
                info = renderer.render_png(
                    value, btype, path, module_width=3, bar_height=120, show_text=False
                )
                self.assertTrue(os.path.exists(path))
                self.assertGreater(info["width"], 0)
                self.assertGreater(info["height"], 0)
                result = decoder.decode_png(path)
                # UPC-A 的模块图案等价于前置 0 的 EAN-13
                expected = "0" + value if btype == "UPC-A" else value
                self.assertEqual(result["digits"], expected, f"{btype} 解码数字不符")
                self.assertTrue(result["valid"])
                self.assertEqual(result["module_width_px"], 3)

    def test_render_with_text_still_decodable(self):
        """带可读数字渲染时，按条码区波段解码仍应成功。"""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "barcode.png")
            info = renderer.render_png(
                "5901234123457", "EAN-13", path, module_width=4, bar_height=120,
                show_text=True,
            )
            band = (info["margin"], info["margin"] + info["bar_height"])
            result = decoder.decode_png(path, band=band)
            self.assertEqual(result["digits"], "5901234123457")

    def test_render_rejects_bad_params(self):
        with self.assertRaises(ValueError):
            renderer.render_png("96385074", "EAN-8", "x.png", module_width=0)


if __name__ == "__main__":
    unittest.main()
