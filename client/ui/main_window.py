"""主窗口：侧边导航 + 页面栈，跨页面刷新协调。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .pages import (
    AccountPage, DashboardPage, GeneratePage, PrintPage, RecordsPage,
    SettingsPage, TemplatesPage,
)
from .theme import STYLESHEET

NAV_ITEMS = [
    ("dashboard", "仪表盘"),
    ("generate", "编码生成"),
    ("records", "编码管理"),
    ("templates", "标签模板"),
    ("print", "打印中心"),
    ("account", "账户与授权"),
    ("settings", "设置"),
]


class MainWindow(QMainWindow):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("CodeLabelStudio — 条码管理与标签打印")
        self.resize(1180, 760)
        self.setStyleSheet(STYLESHEET)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # ---- 侧边导航 ----
        side = QVBoxLayout()
        side.setSpacing(8)
        brand = QLabel("CodeLabelStudio")
        brand.setObjectName("Title")
        side.addWidget(brand)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFixedWidth(180)
        for key, label in NAV_ITEMS:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        side.addWidget(self.nav)
        side.addStretch(1)
        layout.addLayout(side)

        # ---- 页面栈 ----
        self.stack = QStackedWidget()
        self.pages = {}
        self.print_page = None
        for key, _label in NAV_ITEMS:
            self.pages[key] = self._create_page(key)
            self.stack.addWidget(self.pages[key])
        layout.addWidget(self.stack, 1)

        self.setCentralWidget(central)
        self.nav.setCurrentRow(0)

    def _create_page(self, key):
        if key == "dashboard":
            return DashboardPage(self.ctx, self)
        if key == "generate":
            return GeneratePage(self.ctx, self)
        if key == "records":
            return RecordsPage(self.ctx, self)
        if key == "templates":
            return TemplatesPage(self.ctx, self)
        if key == "print":
            self.print_page = PrintPage(self.ctx, self)
            return self.print_page
        if key == "account":
            return AccountPage(self.ctx, self)
        if key == "settings":
            return SettingsPage(self.ctx, self)
        raise KeyError(key)

    def _on_nav_changed(self, row):
        if row < 0:
            return
        key = NAV_ITEMS[row][0]
        self.stack.setCurrentWidget(self.pages[key])
        page = self.pages[key]
        if hasattr(page, "refresh"):
            page.refresh()

    # ---------- 跨页面协调 ----------
    def navigate(self, key):
        for row, (k, _label) in enumerate(NAV_ITEMS):
            if k == key:
                self.nav.setCurrentRow(row)
                return

    def notify_records_changed(self):
        """编码数据变化：刷新统计与管理页。"""
        dashboard = self.pages.get("dashboard")
        if dashboard and hasattr(dashboard, "refresh_stats"):
            dashboard.refresh_stats()
        records = self.pages.get("records")
        if records and hasattr(records, "reload"):
            records.reload()
        print_page = self.pages.get("print")
        if print_page and hasattr(print_page, "reload_records"):
            print_page.reload_records()
        generate = self.pages.get("generate")
        if generate and hasattr(generate, "reload_ranges"):
            generate.reload_ranges()

    def notify_license_changed(self):
        dashboard = self.pages.get("dashboard")
        if dashboard and hasattr(dashboard, "refresh_license"):
            dashboard.refresh_license()

    def closeEvent(self, event):
        # 关闭前做一次安全检查备份之外不做其他持久化
        event.accept()
