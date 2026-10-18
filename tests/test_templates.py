"""标签模板与预览几何测试（阶段 2）。"""

import json
import tempfile
import unittest

from client.printing.preview import (
    assign_items_to_slots,
    compute_layout,
    fit_barcode,
    total_modules,
)
from client.printing.templates import (
    GS1_MIN_MODULE_MM,
    LabelTemplate,
    preset_templates,
)


class TestPresets(unittest.TestCase):
    def test_four_presets(self):
        presets = preset_templates()
        self.assertEqual(len(presets), 4)
        names = [p.template_name for p in presets]
        self.assertEqual(
            names,
            ["标签 30x20 mm", "标签 40x25 mm", "标签 50x30 mm", "标签 60x40 mm"],
        )
        for p in presets:
            self.assertTrue(p.builtin)
            errors, _ = p.validate()
            self.assertEqual(errors, [])
            # 预置模板网格必须放得进 A4
            gw, gh = p.grid_size_mm()
            self.assertLessEqual(gw, p.usable_width() + 1e-6)
            self.assertLessEqual(gh, p.usable_height() + 1e-6)

    def test_preset_grid_counts(self):
        by_name = {p.template_name: p for p in preset_templates()}
        t30 = by_name["标签 30x20 mm"]
        # A4 可用 200x287：30mm 标签 -> 6 列（6*30+5*3=195<=200）
        self.assertEqual(t30.columns, 6)
        # 20mm 标签 -> 12 行（12*20+11*3=273<=287；13 行=296>287）
        self.assertEqual(t30.rows, 12)


class TestLabelTemplate(unittest.TestCase):
    def test_validate_errors(self):
        cases = [
            (LabelTemplate(template_name=""), "名称"),
            (LabelTemplate(label_width=0), "标签宽度"),
            (LabelTemplate(label_width=300), "超出"),
            (LabelTemplate(label_height=300), "超出"),
            (LabelTemplate(rows=0), "行数"),
            (LabelTemplate(copies=0), "份数"),
            (LabelTemplate(orientation="diagonal"), "方向"),
        ]
        for tmpl, keyword in cases:
            errors, _ = tmpl.validate()
            self.assertTrue(errors, f"{keyword} 应产生错误")
            self.assertIn(keyword, errors[0])

    def test_validate_warnings_overflow(self):
        t = LabelTemplate(columns=20, rows=30)  # 必然越界
        errors, warnings = t.validate()
        self.assertEqual(errors, [])
        self.assertTrue(any("越界" in w for w in warnings))

    def test_from_to_dict_roundtrip(self):
        t = LabelTemplate(
            template_name="测试", paper_width=100.0, paper_height=150.0,
            label_width=50.0, label_height=30.0, rows=4, columns=2,
            margins={"top": 10, "right": 8, "bottom": 6, "left": 4},
            horizontal_gap=2.0, vertical_gap=2.5, orientation="landscape",
            barcode_height=12.0, show_text=False, copies=3,
            offset_x=1.0, offset_y=0.5, builtin=False,
        )
        d = t.to_dict()
        self.assertIsInstance(d["margins"], str)
        self.assertEqual(d["show_text"], 0)
        t2 = LabelTemplate.from_dict(d)
        self.assertEqual(t.to_dict(), t2.to_dict())

    def test_positions_within_page(self):
        t = LabelTemplate(rows=2, columns=3, label_width=40, label_height=25)
        pos = t.label_positions()
        self.assertEqual(len(pos), 6)
        for row, col, x, y in pos:
            self.assertLessEqual(x + t.label_width, t.paper_width + 1e-6)
            self.assertLessEqual(y + t.label_height, t.paper_height + 1e-6)

    def test_offset_applied(self):
        t = LabelTemplate(rows=1, columns=1, label_width=40, label_height=25,
                          offset_x=5, offset_y=7)
        _, _, x, y = t.label_positions()[0]
        self.assertEqual(x, t.margins["left"] + 5)
        self.assertEqual(y, t.margins["top"] + 7)


class TestFitBarcode(unittest.TestCase):
    def test_total_modules(self):
        self.assertEqual(total_modules("EAN-13"), 95 + 22)
        self.assertEqual(total_modules("EAN-8"), 67 + 22)
        self.assertEqual(total_modules("UPC-A"), 95 + 22)

    def test_fit_on_wide_label(self):
        fit = fit_barcode(60, 40, "EAN-13")
        # (60-4)/117 = 0.479mm > 0.33 标称 -> 采用 0.33 会超宽？
        # fit_barcode 默认取标签允许的最大值（保证不超出标签）
        self.assertAlmostEqual(fit["module_mm"], 56 / 117, places=6)
        self.assertTrue(fit["fits"])
        self.assertEqual(fit["warnings"], [])

    def test_fit_warns_below_gs1_minimum(self):
        fit = fit_barcode(30, 20, "EAN-13")
        # (30-4)/117 = 0.222mm < 0.264 -> 警告
        self.assertLess(fit["module_mm"], GS1_MIN_MODULE_MM)
        self.assertTrue(any("GS1" in w for w in fit["warnings"]))

    def test_ean8_fits_small_label(self):
        fit = fit_barcode(30, 20, "EAN-8")
        # (30-4)/(67+22) = 0.291mm > 0.264 -> 无 GS1 警告
        self.assertGreaterEqual(fit["module_mm"], GS1_MIN_MODULE_MM)
        self.assertTrue(fit["fits"])

    def test_no_text_more_room(self):
        with_text = fit_barcode(40, 20, "EAN-8", show_text=True)
        without_text = fit_barcode(40, 20, "EAN-8", show_text=False)
        self.assertEqual(with_text["text_h_mm"], 3.5)
        self.assertEqual(without_text["text_h_mm"], 0.0)


class TestComputeLayout(unittest.TestCase):
    def test_layout_slots_and_overflow(self):
        t = LabelTemplate(rows=2, columns=3, label_width=40, label_height=25)
        layout = compute_layout(t)
        self.assertEqual(len(layout["slots"]), 6)
        self.assertFalse(layout["overflow"])
        self.assertEqual(layout["warnings"], [])

    def test_overflow_detected(self):
        t = LabelTemplate(rows=30, columns=20, label_width=40, label_height=25)
        layout = compute_layout(t)
        self.assertTrue(layout["overflow"])
        self.assertTrue(any("越界" in w for w in layout["warnings"]))
        self.assertTrue(any(not s["fits"] for s in layout["slots"]))


class TestAssignItems(unittest.TestCase):
    def test_assign_with_copies(self):
        t = LabelTemplate(rows=1, columns=3, label_width=40, label_height=25)
        layout = compute_layout(t)
        filled, dropped = assign_items_to_slots(
            layout, [("1111111111117", "UPC-A"), ("222222222226", "EAN-13")],
            copies=1,
        )
        self.assertEqual(len(filled), 2)
        self.assertEqual(dropped, 0)

    def test_overflow_dropped_counted(self):
        t = LabelTemplate(rows=1, columns=1, label_width=40, label_height=25)
        layout = compute_layout(t)
        filled, dropped = assign_items_to_slots(
            layout, [("a", "UPC-A"), ("b", "EAN-13"), ("c", "EAN-8")],
            copies=1,
        )
        self.assertEqual(len(filled), 1)
        self.assertEqual(dropped, 2)

    def test_copies_fill(self):
        t = LabelTemplate(rows=1, columns=4, label_width=40, label_height=25)
        layout = compute_layout(t)
        filled, dropped = assign_items_to_slots(
            layout, [("a", "UPC-A"), ("b", "EAN-13")], copies=2,
        )
        self.assertEqual(len(filled), 4)
        self.assertEqual(dropped, 0)


if __name__ == "__main__":
    unittest.main()
