"""桌面客户端 UI 冒烟测试。

PySide6 未安装时整体跳过；安装后以 offscreen 平台真实实例化全部页面，
执行刷新与关键操作路径（不弹模态框，避免阻塞）。
"""

import tempfile
import unittest

PYSIDE6_AVAILABLE = True
try:
    import PySide6  # noqa: F401
except ImportError:
    PYSIDE6_AVAILABLE = False


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 未安装")
class UiSmokeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from client.ui import widgets
        # 离屏环境：对话框改为记录而非弹窗（避免模态框阻塞测试）
        widgets.SILENT_DIALOGS = True
        cls.app = QApplication.instance() or QApplication([])
        cls._tmp = tempfile.mkdtemp(prefix="cls_ui_test_")

    def _ctx(self):
        from client.ui.context import AppContext
        return AppContext(data_dir=tempfile.mkdtemp(
            prefix="cls_ui_ctx_", dir=self._tmp))

    def _seed_records(self, ctx, count=5):
        """创建编号范围并生成 count 条记录。

        范围预留余量（count + 10），避免批量生成恰好耗尽范围
        （耗尽后范围状态为 exhausted，不再出现在"活跃范围"下拉框）。
        """
        rid = ctx.service.create_range(
            "UI 测试范围", "EAN-13", prefix="6901234",
            start_number=0, end_number=count + 10)
        result = ctx.service.generate_batch(
            "EAN-13", count=count, range_id=rid)
        return result["created"]

    # ---------- 页面实例化与刷新 ----------
    def test_all_pages_instantiate_and_refresh(self):
        from client.ui.main_window import MainWindow
        window = MainWindow(self._ctx())
        self.assertGreater(len(window.pages), 0)
        for key, page in window.pages.items():
            if hasattr(page, "refresh"):
                page.refresh()
        # 导航到每一页
        for key in window.pages:
            window.navigate(key)
        window.close()

    def test_generate_page_flow(self):
        from client.ui.pages.generate import GeneratePage
        ctx = self._ctx()
        page = GeneratePage(ctx, main_window=None)
        self._seed_records(ctx, count=3)
        page.reload_ranges()
        self.assertTrue(self._wait_until(
            lambda: page.single_range.count() >= 1))

    def test_templates_page_crud(self):
        from client.ui.pages.templates import TemplatesPage
        ctx = self._ctx()
        page = TemplatesPage(ctx, main_window=None)
        page.reload()
        # 等待 RunLater 线程完成后列表应包含预置模板
        self.assertTrue(self._wait_until(
            lambda: len(ctx.templates.list_templates()) >= 4))
        # 新建模板
        page.new_template()
        page.f_name.setText("UI 测试模板")
        page.save_template()
        self.assertTrue(self._wait_until(lambda: any(
            t.template_name == "UI 测试模板"
            for t in ctx.templates.list_templates())))

    def test_print_page_layout(self):
        from client.ui.pages.print_export import PrintPage, SheetPreviewWidget
        ctx = self._ctx()
        created = self._seed_records(ctx, count=4)
        page = PrintPage(ctx, main_window=None)
        page.reload_records()
        self.assertTrue(self._wait_until(lambda: len(page.records) == 4))
        self.assertTrue(self._wait_until(lambda: len(page.templates) >= 4))
        page.apply_template()
        widget = SheetPreviewWidget()
        widget.set_data(page.current_template(),
                        [(r["barcode_value"], r["barcode_type"])
                         for r in created])
        # 离屏渲染整个页面（触发 paintEvent）
        page.grab()
        widget.grab()

    def test_records_page_query(self):
        from client.ui.pages.records import RecordsPage
        ctx = self._ctx()
        self._seed_records(ctx, count=2)
        page = RecordsPage(ctx, main_window=None)
        page.reload()
        self.assertTrue(self._wait_until(
            lambda: page.table.rowCount() == 2))

    def test_settings_integrity(self):
        from client.ui.pages.settings import SettingsPage
        ctx = self._ctx()
        self._seed_records(ctx, count=1)
        page = SettingsPage(ctx, main_window=None)
        self.assertTrue(ctx.db.verify_integrity())

    def test_account_page_offline_state(self):
        from client.ui.pages.account import AccountPage
        ctx = self._ctx()
        page = AccountPage(ctx, main_window=None)
        page.refresh_license_status()
        self.assertIn("授权", page.license_status.text())

    def test_barcode_widget_paint(self):
        from client.ui.widgets import BarcodeWidget
        ctx = self._ctx()
        created = self._seed_records(ctx, count=1)
        widget = BarcodeWidget(created[0]["barcode_value"], "EAN-13")
        widget.resize(400, 180)
        widget.grab()

    # ---------- 工具 ----------
    def _wait_until(self, predicate, timeout_s=10.0):
        """轮询直到条件满足或超时（RunLater 在裸 QThread 中执行）。"""
        import time
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def _drain(self, timeout_s=3.0):
        """处理一段时间事件循环，让已发出的后台任务回调完成。"""
        import time
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.02)


if __name__ == "__main__":
    unittest.main()
