"""编码管理页：查询 / 筛选 / 状态流转 / 详情 / 导出。

状态机按钮按当前状态动态启用（generated -> allocated -> used /
invalidated），非法流转在 UI 层即被拦截，与服务端规则一致。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from ...barcode.checkdigit import BARCODE_LENGTHS
from ...barcode.csvio import export_records_csv
from ..widgets import (
    Card, MutedLabel, PrimaryButton, RunLater, confirm, error, info,
    notify_main, warn,
)

COLUMNS = ["编码", "类型", "状态", "商品名称", "来源", "创建时间"]
STATUS_LABELS = {
    "generated": "待分配",
    "allocated": "已分配",
    "used": "已使用",
    "invalidated": "已作废",
}
SOURCE_LABELS = {
    "internal": "内部",
    "gs1_authorized": "GS1 授权",
}


class RecordsPage(QWidget):
    def __init__(self, ctx, main_window, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.main = main_window
        self.selected_value = None
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ---- 筛选区 ----
        filters = Card("查询筛选")
        row = QHBoxLayout()
        self.edit_value = QLineEdit()
        self.edit_value.setPlaceholderText("编码（精确匹配）")
        self.edit_product = QLineEdit()
        self.edit_product.setPlaceholderText("商品名称（模糊）")
        self.combo_type = QComboBox()
        self.combo_type.addItem("全部类型", "")
        for t in BARCODE_LENGTHS:
            self.combo_type.addItem(t, t)
        self.combo_status = QComboBox()
        self.combo_status.addItem("全部状态", "")
        for key, label in STATUS_LABELS.items():
            self.combo_status.addItem(label, key)
        self.btn_query = PrimaryButton("查询")
        self.btn_query.clicked.connect(self.reload)
        for w in (self.edit_value, self.edit_product, self.combo_type,
                  self.combo_status, self.btn_query):
            row.addWidget(w)
        filters.body.addLayout(row)
        layout.addWidget(filters)

        # ---- 表格 ----
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        layout.addWidget(self.table)

        self.summary = MutedLabel("共 0 条")
        layout.addWidget(self.summary)

        # ---- 操作按钮 ----
        ops = QHBoxLayout()
        self.btn_allocate = QPushButton("标记分配")
        self.btn_used = QPushButton("标记使用")
        self.btn_invalidate = QPushButton("作废")
        self.btn_history = QPushButton("操作历史")
        self.btn_export_csv = QPushButton("导出 CSV")
        self.btn_export_pdf = QPushButton("导出条码 PDF")
        self.btn_allocate.clicked.connect(lambda: self._transition("allocate"))
        self.btn_used.clicked.connect(lambda: self._transition("mark_used"))
        self.btn_invalidate.clicked.connect(self._invalidate)
        self.btn_history.clicked.connect(self._history)
        self.btn_export_csv.clicked.connect(self._export_csv)
        self.btn_export_pdf.clicked.connect(self._export_pdf)
        for b in (self.btn_allocate, self.btn_used, self.btn_invalidate,
                  self.btn_history, self.btn_export_csv, self.btn_export_pdf):
            ops.addWidget(b)
        ops.addStretch(1)
        layout.addLayout(ops)

        self._set_actions_enabled(False)
        self.reload()

    # ---------- 数据加载 ----------
    def reload(self):
        filters = {
            "value": self.edit_value.text().strip() or None,
            "product_name": self.edit_product.text().strip() or None,
            "barcode_type": self.combo_type.currentData() or None,
            "status": self.combo_status.currentData() or None,
        }

        def _load():
            return self.ctx.service.query_records(**filters)

        def _done(records):
            self._render(records)

        RunLater(self).run(_load, on_done=_done,
                           on_error=lambda m: error(self, "查询失败", m))

    def _render(self, records):
        self.table.setRowCount(0)
        for rec in records:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                rec["barcode_value"],
                rec["barcode_type"],
                STATUS_LABELS.get(rec["allocation_status"],
                                  rec["allocation_status"]),
                rec.get("product_name") or "",
                SOURCE_LABELS.get(rec["source_type"], rec["source_type"]),
                rec.get("created_at", ""),
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                if col == 0:
                    item.setData(Qt.UserRole, rec)
                self.table.setItem(row, col, item)
        self.summary.setText(f"共 {len(records)} 条")

    def _on_select(self):
        items = self.table.selectedItems()
        has = bool(items)
        self._set_actions_enabled(has)
        if has:
            rec = self.table.item(items[0].row(), 0).data(Qt.UserRole)
            self.selected_value = rec["barcode_value"]
            self.selected_record = rec

    def _set_actions_enabled(self, enabled):
        for b in (self.btn_allocate, self.btn_used, self.btn_invalidate,
                  self.btn_history, self.btn_export_csv, self.btn_export_pdf):
            b.setEnabled(enabled)

    # ---------- 状态流转 ----------
    def _transition(self, action):
        if not self.selected_value:
            return
        value = self.selected_value

        def _do():
            fn = getattr(self.ctx.service, action)
            return fn(value)

        RunLater(self).run(
            _do,
            on_done=lambda _r: (info(self, "完成", f"编码 {value} 状态已更新"),
                                self.reload(),
                                notify_main(self, "notify_records_changed")),
            on_error=lambda m: warn(self, "操作被拒绝", m),
        )


    def _invalidate(self):
        if not self.selected_value:
            return
        value = self.selected_value
        if not confirm(self, "确认作废",
                       f"作废编码 {value}？作废后不可恢复、不可重新分配。"):
            return

        def _do():
            return self.ctx.service.invalidate(value, reason="UI 手动作废")

        RunLater(self).run(
            _do,
            on_done=lambda _r: (info(self, "完成", f"编码 {value} 已作废"),
                                self.reload(),
                                notify_main(self, "notify_records_changed")),
            on_error=lambda m: warn(self, "操作被拒绝", m),
        )

    def _history(self):
        if not self.selected_value:
            return
        value = self.selected_value

        def _load():
            return self.ctx.service.get_history(value)

        def _done(logs):
            if not logs:
                info(self, "操作历史", f"编码 {value} 暂无操作记录")
                return
            lines = [
                f"{log.get('created_at', '')}  {log.get('operation_type', '')}"
                f"  {log.get('result', '')}" for log in logs
            ]
            info(self, f"编码 {value} 操作历史", "\n".join(lines))

        RunLater(self).run(_load, on_done=_done,
                           on_error=lambda m: error(self, "加载失败", m))

    # ---------- 导出 ----------
    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "barcodes.csv", "CSV 文件 (*.csv)")
        if not path:
            return

        def _do():
            records = self.ctx.service.query_records(
                value=self.edit_value.text().strip() or None,
                product_name=self.edit_product.text().strip() or None,
                barcode_type=self.combo_type.currentData() or None,
                status=self.combo_status.currentData() or None,
            )
            return export_records_csv(records, path)

        RunLater(self).run(
            _do, on_done=lambda _r: info(self, "导出完成", f"已导出到 {path}"),
            on_error=lambda m: error(self, "导出失败", m))

    def _export_pdf(self):
        if not self.selected_value:
            return
        value = self.selected_value
        rec = self.selected_record
        path, _ = QFileDialog.getSaveFileName(
            self, "导出条码 PDF", f"{value}.pdf", "PDF 文件 (*.pdf)")
        if not path:
            return

        def _do():
            from ...printing.pdf import render_pdf
            return render_pdf(value, rec["barcode_type"], path)

        RunLater(self).run(
            _do, on_done=lambda _r: info(self, "导出完成", f"已导出到 {path}"),
            on_error=lambda m: error(self, "导出失败", m))
