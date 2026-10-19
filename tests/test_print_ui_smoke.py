"""打印中心 / 设置向导 UI 冒烟测试（离屏，不真正提交打印）。

PySide6 未安装时整体跳过；安装后以 offscreen 平台实例化 PrintPage 与
PrinterSetup，验证打印区块控件存在、空能力列表禁用打印按钮、注入构造的
未实测能力后下拉填充且 build_job_from_form 产出合法 LabelJobSpec。
全程不调用 adapter.submit，不提交任何打印。
"""

import tempfile
import unittest

PYSIDE6_AVAILABLE = True
try:
    import PySide6  # noqa: F401
except ImportError:
    PYSIDE6_AVAILABLE = False


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 未安装")
class PrintUiSmokeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from client.ui import widgets
        # 离屏环境：对话框改为记录而非弹窗（避免模态框阻塞测试）
        widgets.SILENT_DIALOGS = True
        cls.app = QApplication.instance() or QApplication([])
        cls._tmp = tempfile.mkdtemp(prefix="cls_print_ui_test_")

    def _ctx(self):
        from client.ui.context import AppContext
        return AppContext(data_dir=tempfile.mkdtemp(
            prefix="cls_print_ctx_", dir=self._tmp))

    def _seed_records(self, ctx, count=4):
        rid = ctx.service.create_range(
            "打印冒烟范围", "EAN-13", prefix="6901234",
            start_number=0, end_number=count + 10)
        ctx.service.generate_batch("EAN-13", count=count, range_id=rid)

    def _unverified_caps(self):
        from client.printing.capabilities import (
            DriverInfo, PaperSize, PrinterCapabilities, PrinterStatus,
        )
        return PrinterCapabilities(
            printer_name="Test Label Printer",
            driver=DriverInfo(driver_name="Generic / Text Only"),
            port_name="USB001",
            is_default=True,
            status=PrinterStatus(available=True),
            dpi=203,
            supported_papers=(PaperSize("A4", 210.0, 297.0),),
            supports_custom_paper=True,
            verified_with_hardware=False,  # 未实测，禁止伪造 True
        )

    # ---------- PrintPage 打印区块 ----------
    def test_print_section_controls_exist(self):
        from client.ui.pages.print_export import PrintPage
        ctx = self._ctx()
        self._seed_records(ctx)
        page = PrintPage(ctx, main_window=None)
        for attr in (
            "printer_combo", "btn_refresh_printers", "printer_status_label",
            "driver_state_label", "btn_driver_guidance", "paper_combo",
            "orientation_combo", "copies_spin", "quality_combo",
            "offset_x_spin", "offset_y_spin", "btn_preview", "btn_print",
            "btn_cancel", "btn_calibrate",
        ):
            self.assertTrue(hasattr(page, attr), f"缺少控件 {attr}")
        page.grab()

    def test_empty_capabilities_disables_print(self):
        from client.ui.pages.print_export import PrintPage
        ctx = self._ctx()
        page = PrintPage(ctx, main_window=None)
        page.set_printers([])
        self.assertIsNone(page._current_caps)
        self.assertFalse(page.btn_print.isEnabled())
        self.assertFalse(page.btn_preview.isEnabled())
        self.assertIn("未检测到打印机", page.printer_status_label.text())

    def test_inject_unverified_caps_fills_combo_and_builds_spec(self):
        from client.ui.pages import print_export_logic as L
        from client.ui.pages.print_export import PrintPage
        from client.printing.capabilities import PrintQuality
        ctx = self._ctx()
        page = PrintPage(ctx, main_window=None)
        caps = self._unverified_caps()
        page.set_printers([caps])

        self.assertIs(page._current_caps, caps)
        self.assertEqual(page.printer_combo.count(), 1)
        self.assertTrue(page.btn_print.isEnabled())
        # 未实测必须加注
        self.assertIn("未实测", page.printer_status_label.text())

        # 直接由表单构造合法任务规格（不提交、不调用 submit）
        spec = L.build_job_from_form(None, caps, {
            "paper": caps.supported_papers[0],
            "copies": 2, "quality": "high", "orientation": "landscape",
            "offset_x_mm": 0.5, "offset_y_mm": -0.5,
        })
        self.assertEqual(spec.copies, 2)
        self.assertEqual(spec.quality, PrintQuality.HIGH)
        self.assertEqual(spec.orientation.value, "landscape")
        self.assertEqual(spec.offset_x_mm, 0.5)
        self.assertEqual(spec.offset_y_mm, -0.5)
        self.assertIsNotNone(spec.paper)

    def test_export_controls_still_present(self):
        """现有导出功能控件必须原样保留。"""
        from client.ui.pages.print_export import PrintPage
        ctx = self._ctx()
        page = PrintPage(ctx, main_window=None)
        for attr in ("template_combo", "table", "preview"):
            self.assertTrue(hasattr(page, attr))

    # ---------- PrinterSetup 向导 ----------
    def test_wizard_empty_state_and_inject(self):
        from client.ui.printer_setup import PrinterSetup
        ctx = self._ctx()
        wizard = PrinterSetup(ctx, parent=None)
        wizard.set_printers([])
        self.assertIsNone(wizard._current_caps)
        self.assertFalse(wizard.btn_guidance.isEnabled())

        caps = self._unverified_caps()
        wizard.set_printers([caps])
        self.assertIs(wizard._current_caps, caps)
        self.assertTrue(wizard.p_copies.isEnabled())
        self.assertIn("未实测", wizard.detect_status.text())
        wizard.grab()

    # ---------- 诚信红线：源码不得出现“打印成功” ----------
    def test_no_print_success_wording(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        for rel in (
            "client/ui/pages/print_export.py",
            "client/ui/printer_setup.py",
            "client/ui/pages/print_export_logic.py",
        ):
            text = (root / rel).read_text(encoding="utf-8")
            self.assertNotIn("打印成功", text, f"{rel} 出现禁用文案“打印成功”")

    # ---------- 工具 ----------
    def _wait_until(self, predicate, timeout_s=10.0):
        import time
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return False


if __name__ == "__main__":
    unittest.main()
