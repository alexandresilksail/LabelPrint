"""设置页：数据备份、恢复、完整性校验、关于。"""

from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from ..widgets import (
    Card, MutedLabel, PrimaryButton, RunLater, confirm, error, info,
    notify_main,
)
from ...barcode.service import BarcodeService


class SettingsPage(QWidget):
    def __init__(self, ctx, main_window, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.main = main_window

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # ---- 数据 ----
        data_card = Card("数据")
        self.data_path_label = MutedLabel(f"数据库位置：{self.ctx.db.path}")
        data_card.body.addWidget(self.data_path_label)
        row = QHBoxLayout()
        btn_backup = PrimaryButton("备份数据库…")
        btn_backup.clicked.connect(self.do_backup)
        btn_restore = QPushButton("从备份恢复…")
        btn_restore.setObjectName("Danger")
        btn_restore.clicked.connect(self.do_restore)
        btn_check = QPushButton("完整性校验")
        btn_check.clicked.connect(self.do_check)
        row.addWidget(btn_backup)
        row.addWidget(btn_restore)
        row.addWidget(btn_check)
        row.addStretch(1)
        data_card.body.addLayout(row)
        self.data_status = MutedLabel("")
        data_card.body.addWidget(self.data_status)
        layout.addWidget(data_card)

        # ---- 恢复安全说明 ----
        note_card = Card("恢复安全机制")
        note_card.body.addWidget(MutedLabel(
            "• 恢复前自动创建恢复前快照（.pre_restore_<UTC>）\n"
            "• 源文件须通过完整性 / schema / 版本三重校验\n"
            "• 临时文件 + 原子替换，失败不影响现有数据\n"
            "• 恢复后自动执行 quick_check 并记录审计日志"))
        layout.addWidget(note_card)

        # ---- 关于 ----
        about_card = Card("关于")
        about_card.body.addWidget(QLabel("CodeLabelStudio"))
        about_card.body.addWidget(MutedLabel(
            "专业 UPC/EAN 条码管理与标签打印工具\n"
            "本地优先 · GS1 来源可追溯 · 离线授权宽限"))
        layout.addWidget(about_card)

        layout.addStretch(1)

    # ================== 操作 ==================
    def do_backup(self):
        default_name = f"codelabel-backup.db"
        path, _ = QFileDialog.getSaveFileName(
            self, "备份数据库", default_name, "SQLite (*.db);;所有文件 (*)")
        if not path:
            return

        def _do():
            return self.ctx.backup(path)

        def _done(dest):
            info(self, "备份完成", f"已备份到 {dest}")
            self.data_status.setText(f"最近备份：{dest}")

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "备份失败", m))

    def do_restore(self):
        if not confirm(
                self, "确认恢复",
                "恢复将用所选备份替换当前数据库。\n"
                "系统会先自动创建恢复前快照，但仍建议先手动备份。\n\n确定继续？"):
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择备份文件", "", "SQLite (*.db);;所有文件 (*)")
        if not path:
            return

        def _do():
            return self.ctx.restore(path)

        def _done(_result):
            info(self, "恢复完成", "数据库已恢复，页面数据已刷新")
            self.data_status.setText(f"已从 {path} 恢复")
            notify_main(self, "notify_records_changed")

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "恢复失败", m))

    def do_check(self):
        def _do():
            ok = self.ctx.db.verify_integrity()
            stats = BarcodeService(self.ctx.db).get_stats()
            return ok, stats

        def _done(result):
            ok, stats = result
            if ok:
                self.data_status.setText(
                    f"✅ 完整性校验通过；共 {stats['total']} 条编码，"
                    f"活跃范围 {stats['ranges_active']} 个")
                info(self, "校验通过", "数据库完整性校验通过")
            else:
                self.data_status.setText("⛔ 完整性校验失败，请从备份恢复")
                error(self, "校验失败", "数据库完整性校验失败，建议立即从备份恢复")

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "校验失败", m))

    def refresh(self):
        pass
