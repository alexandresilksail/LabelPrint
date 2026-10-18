"""仪表盘页：统计卡片 + 授权状态 + 快捷操作。"""

from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget,
)

from ..widgets import Card, MutedLabel, PrimaryButton, RunLater, StatCard, error


class DashboardPage(QWidget):
    def __init__(self, ctx, main_window, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.main = main_window
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # ---- 授权状态横幅 ----
        self.license_card = Card("授权状态")
        self.license_label = MutedLabel("检测中…")
        self.license_detail = MutedLabel("")
        self.license_card.body.addWidget(self.license_label)
        self.license_card.body.addWidget(self.license_detail)
        row = QHBoxLayout()
        self.btn_refresh_license = PrimaryButton("刷新状态")
        self.btn_refresh_license.clicked.connect(self.refresh_license)
        self.btn_go_account = QPushButton("账户与授权")
        self.btn_go_account.clicked.connect(
            lambda: self.main.navigate("account"))
        row.addWidget(self.btn_refresh_license)
        row.addWidget(self.btn_go_account)
        row.addStretch(1)
        self.license_card.body.addLayout(row)
        layout.addWidget(self.license_card)

        # ---- 统计卡片 ----
        grid = QGridLayout()
        grid.setSpacing(12)
        self.stat_total = StatCard("编码总数")
        self.stat_generated = StatCard("待分配")
        self.stat_allocated = StatCard("已分配")
        self.stat_used = StatCard("已使用")
        self.stat_invalidated = StatCard("已作废")
        self.stat_ranges = StatCard("活跃编号范围")
        for i, card in enumerate([self.stat_total, self.stat_generated,
                                  self.stat_allocated, self.stat_used,
                                  self.stat_invalidated, self.stat_ranges]):
            grid.addWidget(card, i // 3, i % 3)
        layout.addLayout(grid)

        # ---- 快捷操作 ----
        quick = Card("快捷操作")
        row = QHBoxLayout()
        btn_generate = PrimaryButton("生成编码")
        btn_generate.clicked.connect(lambda: self.main.navigate("generate"))
        btn_records = QPushButton("编码管理")
        btn_records.clicked.connect(lambda: self.main.navigate("records"))
        btn_print = QPushButton("打印中心")
        btn_print.clicked.connect(lambda: self.main.navigate("print"))
        row.addWidget(btn_generate)
        row.addWidget(btn_records)
        row.addWidget(btn_print)
        row.addStretch(1)
        quick.body.addLayout(row)
        layout.addWidget(quick)

        layout.addStretch(1)
        self.refresh()

    # ---------- 数据 ----------
    def refresh(self):
        self.refresh_license()
        self.refresh_stats()

    def refresh_stats(self):
        def _load():
            return self.ctx.service.get_stats()

        def _done(stats):
            by_status = stats["by_status"]
            self.stat_total.set_value(stats["total"])
            self.stat_generated.set_value(by_status.get("generated", 0))
            self.stat_allocated.set_value(by_status.get("allocated", 0))
            self.stat_used.set_value(by_status.get("used", 0))
            self.stat_invalidated.set_value(by_status.get("invalidated", 0))
            self.stat_ranges.set_value(stats["ranges_active"])

        RunLater(self).run(_load, on_done=_done,
                           on_error=lambda m: error(self, "加载失败", m))

    def refresh_license(self):
        from ..licensing.client_license import offline_status
        cached = self.ctx.license_store.load()
        status = offline_status(cached)
        reason_text = {
            "ok": "授权有效",
            "grace": "离线宽限期内可用",
            "no_credential": "尚未激活授权",
            "invalid_signature": "凭证验签失败",
            "revoked": "授权已被撤销",
            "expired": "授权已到期",
            "grace_expired": "离线宽限已用尽，请联网验证",
            "not_yet_valid": "凭证尚未生效",
        }.get(status["reason"], status["reason"])
        if status["entitled"]:
            self.license_label.setText(f"✅ {reason_text}")
            self.license_label.setStyleSheet(
                "color: #1F9D55; font-weight: 600;")
        else:
            self.license_label.setText(f"⛔ {reason_text}")
            self.license_label.setStyleSheet(
                "color: #DC2626; font-weight: 600;")
        detail = []
        if status.get("expires_at"):
            detail.append(f"到期：{status['expires_at']}")
        if status.get("grace_deadline"):
            detail.append(f"宽限至：{status['grace_deadline']}")
        self.license_detail.setText("　".join(detail) or "联网激活后可离线使用")
