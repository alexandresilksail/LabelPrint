"""编码生成页：单码生成、批量生成、编号范围管理。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLineEdit, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from ..widgets import (
    Card, MutedLabel, PrimaryButton, RunLater, confirm, error, info,
    notify_main, warn,
)

BARCODE_TYPES = ["EAN-13", "EAN-8", "UPC-A", "UPC-E", "Code128", "Code39"]
SOURCE_TYPES = [("internal", "内部自有编码"), ("gs1_authorized", "GS1 授权编码")]

STATUS_LABELS = {
    "active": "活跃",
    "exhausted": "已耗尽",
    "closed": "已关闭",
}


class GeneratePage(QWidget):
    def __init__(self, ctx, main_window, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.main = main_window
        self._cancel_batch = False

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        tabs = QTabWidget()
        tabs.addTab(self._build_single_tab(), "单个生成")
        tabs.addTab(self._build_batch_tab(), "批量生成")
        tabs.addTab(self._build_range_tab(), "编号范围")
        tabs.addTab(self._build_import_tab(), "批量导入")
        layout.addWidget(tabs)
        layout.addStretch(1)

    # ================== 单个生成 ==================
    def _build_single_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        card = Card("生成单个编码")
        form = QFormLayout()
        self.single_type = QComboBox()
        self.single_type.addItems(BARCODE_TYPES)
        self.single_source = QComboBox()
        for code, label in SOURCE_TYPES:
            self.single_source.addItem(label, code)
        self.single_source.currentIndexChanged.connect(
            self._on_single_source_changed)
        self.single_value = QLineEdit()
        self.single_value.setPlaceholderText("留空则从下方编号范围自动分配")
        self.single_range = QComboBox()
        self.single_auth_ref = QLineEdit()
        self.single_auth_ref.setPlaceholderText("GS1 授权编号（如 6912345）")
        self.single_product = QLineEdit()
        self.single_notes = QLineEdit()
        form.addRow("条码类型", self.single_type)
        form.addRow("来源类型", self.single_source)
        form.addRow("指定编码", self.single_value)
        form.addRow("编号范围", self.single_range)
        form.addRow("授权编号", self.single_auth_ref)
        form.addRow("产品名称", self.single_product)
        form.addRow("备注", self.single_notes)
        card.body.addLayout(form)
        row = QHBoxLayout()
        self.btn_single_generate = PrimaryButton("生成")
        self.btn_single_generate.clicked.connect(self.do_generate_single)
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(self._clear_single_form)
        row.addWidget(self.btn_single_generate)
        row.addWidget(btn_clear)
        row.addStretch(1)
        card.body.addLayout(row)
        self.single_result = MutedLabel("")
        card.body.addWidget(self.single_result)
        layout.addWidget(card)
        layout.addStretch(1)
        self.reload_ranges()
        return page

    def _on_single_source_changed(self):
        is_gs1 = self.single_source.currentData() == "gs1_authorized"
        self.single_auth_ref.setEnabled(is_gs1)
        if not is_gs1:
            self.single_auth_ref.clear()

    def _clear_single_form(self):
        self.single_value.clear()
        self.single_auth_ref.clear()
        self.single_product.clear()
        self.single_notes.clear()
        self.single_result.setText("")

    def do_generate_single(self):
        barcode_type = self.single_type.currentText()
        source_type = self.single_source.currentData()
        value = self.single_value.text().strip() or None
        range_id = self.single_range.currentData()
        auth_ref = self.single_auth_ref.text().strip() or None
        product = self.single_product.text().strip() or None
        notes = self.single_notes.text().strip() or None
        if value and range_id:
            warn(self, "参数冲突", "指定编码与编号范围只能二选一")
            return

        def _do():
            return self.ctx.service.generate_single(
                barcode_type, explicit_value=value, range_id=range_id,
                source_type=source_type, authorization_reference=auth_ref,
                product_name=product, notes=notes)

        def _done(record):
            self.single_result.setText(
                f"✅ 已生成 {record['barcode_value']}（{record['barcode_type']}）")
            notify_main(self, "notify_records_changed")

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "生成失败", m))

    # ================== 批量生成 ==================
    def _build_batch_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        card = Card("从编号范围批量生成")
        form = QFormLayout()
        self.batch_type = QComboBox()
        self.batch_type.addItems(BARCODE_TYPES)
        self.batch_range = QComboBox()
        self.batch_count = QSpinBox()
        self.batch_count.setRange(1, 10000)
        self.batch_count.setValue(100)
        self.batch_product = QLineEdit()
        self.batch_notes = QLineEdit()
        form.addRow("条码类型", self.batch_type)
        form.addRow("编号范围", self.batch_range)
        form.addRow("生成数量", self.batch_count)
        form.addRow("产品名称", self.batch_product)
        form.addRow("备注", self.batch_notes)
        card.body.addLayout(form)
        self.batch_progress = QProgressBar()
        self.batch_progress.setVisible(False)
        card.body.addWidget(self.batch_progress)
        row = QHBoxLayout()
        self.btn_batch_start = PrimaryButton("开始生成")
        self.btn_batch_start.clicked.connect(self.do_generate_batch)
        self.btn_batch_cancel = QPushButton("取消")
        self.btn_batch_cancel.setEnabled(False)
        self.btn_batch_cancel.clicked.connect(self._cancel_batch_clicked)
        row.addWidget(self.btn_batch_start)
        row.addWidget(self.btn_batch_cancel)
        row.addStretch(1)
        card.body.addLayout(row)
        self.batch_result = MutedLabel("")
        card.body.addWidget(self.batch_result)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _cancel_batch_clicked(self):
        self._cancel_batch = True
        self.btn_batch_cancel.setEnabled(False)

    def do_generate_batch(self):
        range_id = self.batch_range.currentData()
        if range_id is None:
            warn(self, "未选择范围", "请先创建编号范围")
            return
        count = self.batch_count.value()
        product = self.batch_product.text().strip() or None
        notes = self.batch_notes.text().strip() or None
        self._cancel_batch = False
        self.batch_progress.setVisible(True)
        self.batch_progress.setRange(0, count)
        self.batch_progress.setValue(0)
        self.btn_batch_start.setEnabled(False)
        self.btn_batch_cancel.setEnabled(True)

        def _progress(done, total):
            self.batch_progress.setValue(done)

        def _do():
            return self.ctx.service.generate_batch(
                self.batch_type.currentText(), count=count, range_id=range_id,
                product_name=product, notes=notes,
                progress_callback=_progress,
                should_cancel=lambda: self._cancel_batch)

        def _done(result):
            self.btn_batch_start.setEnabled(True)
            self.btn_batch_cancel.setEnabled(False)
            self.batch_progress.setVisible(False)
            created = len(result["created"])
            if result["canceled"]:
                self.batch_result.setText(
                    f"⏹ 已取消：完成 {created}/{result['requested']} 个")
            elif result["exhausted"]:
                self.batch_result.setText(
                    f"⚠ 范围耗尽：完成 {created}/{result['requested']} 个")
            else:
                self.batch_result.setText(f"✅ 已生成 {created} 个编码")
            self.reload_ranges()
            notify_main(self, "notify_records_changed")

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: (
                               self._reset_batch_ui(),
                               error(self, "批量生成失败", m)))

    def _reset_batch_ui(self):
        self.btn_batch_start.setEnabled(True)
        self.btn_batch_cancel.setEnabled(False)
        self.batch_progress.setVisible(False)

    # ================== 编号范围 ==================
    def _build_range_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        card = Card("编号范围")
        form = QFormLayout()
        self.range_name = QLineEdit()
        self.range_type = QComboBox()
        self.range_type.addItems(BARCODE_TYPES)
        self.range_source = QComboBox()
        for code, label in SOURCE_TYPES:
            self.range_source.addItem(label, code)
        self.range_prefix = QLineEdit()
        self.range_prefix.setPlaceholderText("如 6901234（不含校验位）")
        self.range_start = QSpinBox()
        self.range_start.setRange(0, 99999999)
        self.range_end = QSpinBox()
        self.range_end.setRange(0, 99999999)
        self.range_end.setValue(999)
        form.addRow("范围名称", self.range_name)
        form.addRow("条码类型", self.range_type)
        form.addRow("来源类型", self.range_source)
        form.addRow("号码前缀", self.range_prefix)
        form.addRow("起始序号", self.range_start)
        form.addRow("结束序号", self.range_end)
        card.body.addLayout(form)
        row = QHBoxLayout()
        btn_create = PrimaryButton("创建范围")
        btn_create.clicked.connect(self.do_create_range)
        row.addWidget(btn_create)
        row.addStretch(1)
        card.body.addLayout(row)
        layout.addWidget(card)

        table_card = Card("已有范围")
        self.range_table = QTableWidget(0, 6)
        self.range_table.setHorizontalHeaderLabels(
            ["ID", "名称", "类型", "下一个号码", "结束", "状态"])
        self.range_table.verticalHeader().setVisible(False)
        self.range_table.setSelectionBehavior(
            QTableWidget.SelectRows)
        self.range_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.range_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        table_card.body.addWidget(self.range_table)
        row2 = QHBoxLayout()
        btn_close = QPushButton("关闭所选范围")
        btn_close.clicked.connect(self.do_close_range)
        row2.addWidget(btn_close)
        row2.addStretch(1)
        table_card.body.addLayout(row2)
        layout.addWidget(table_card)
        layout.addStretch(1)
        return page

    def do_create_range(self):
        name = self.range_name.text().strip()
        if not name:
            warn(self, "缺少名称", "请填写编号范围名称")
            return
        barcode_type = self.range_type.currentText()
        source_type = self.range_source.currentData()
        prefix = self.range_prefix.text().strip() or None
        start = self.range_start.value()
        end = self.range_end.value()
        auth_ref = None
        if source_type == "gs1_authorized":
            auth_ref, ok = QInputDialog.getText(
                self, "GS1 授权编号", "请输入 GS1 授权编号：")
            if not ok or not auth_ref.strip():
                return
            auth_ref = auth_ref.strip()

        def _do():
            return self.ctx.service.create_range(
                name, barcode_type, prefix=prefix, start_number=start,
                end_number=end, source_type=source_type,
                authorization_reference=auth_ref)

        def _done(_rng):
            info(self, "创建成功", f"编号范围「{name}」已创建")
            self.range_name.clear()
            self.reload_ranges()

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "创建失败", m))

    def do_close_range(self):
        row = self.range_table.currentRow()
        if row < 0:
            warn(self, "未选择", "请先在表格中选择要关闭的范围")
            return
        range_id = int(self.range_table.item(row, 0).text())
        if not confirm(self, "确认关闭", f"确定关闭编号范围 #{range_id}？"):
            return

        def _do():
            from client.database.dao import NumberingRangeDAO
            with self.ctx.db.transaction() as conn:
                NumberingRangeDAO(self.ctx.db).set_status(
                    range_id, "closed", conn=conn)

        def _done():
            info(self, "已关闭", f"编号范围 #{range_id} 已关闭")
            self.reload_ranges()

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "操作失败", m))

    # ================== 批量导入 ==================
    def _build_import_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        card = Card("从 CSV / 文本批量导入")
        form = QFormLayout()
        self.import_type = QComboBox()
        self.import_type.addItems(BARCODE_TYPES)
        self.import_source = QComboBox()
        for code, label in SOURCE_TYPES:
            self.import_source.addItem(label, code)
        form.addRow("条码类型", self.import_type)
        form.addRow("来源类型", self.import_source)
        card.body.addLayout(form)
        row = QHBoxLayout()
        btn_file = QPushButton("选择文件…")
        btn_file.clicked.connect(self._choose_import_file)
        row.addWidget(btn_file)
        row.addStretch(1)
        card.body.addLayout(row)
        self.import_text = QPlainTextEdit()
        self.import_text.setPlaceholderText("每行一个编码，或粘贴 CSV 内容…")
        self.import_text.setMinimumHeight(160)
        card.body.addWidget(self.import_text)
        row2 = QHBoxLayout()
        self.btn_import = PrimaryButton("导入")
        self.btn_import.clicked.connect(self.do_import)
        row2.addWidget(self.btn_import)
        row2.addStretch(1)
        card.body.addLayout(row2)
        self.import_result = MutedLabel("")
        card.body.addWidget(self.import_result)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _choose_import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择导入文件", "", "CSV/文本 (*.csv *.txt);;所有文件 (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                self.import_text.setPlainText(fh.read())
        except UnicodeDecodeError:
            with open(path, "r", encoding="gbk") as fh:
                self.import_text.setPlainText(fh.read())

    def do_import(self):
        text = self.import_text.toPlainText()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            warn(self, "无内容", "请先选择文件或粘贴编码")
            return
        barcode_type = self.import_type.currentText()
        source_type = self.import_source.currentData()

        def _do():
            return self.ctx.service.import_records(
                lines, barcode_type, source_type=source_type)

        def _done(result):
            report = result["report"]
            self.import_result.setText(
                f"✅ 成功导入 {result['imported']} 条；"
                f"无效 {len(report['invalid'])} 条；"
                f"清单内重复 {len(report['duplicates_in_file'])} 条；"
                f"库内已存在 {len(report['already_exists'])} 条")
            notify_main(self, "notify_records_changed")

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "导入失败", m))

    # ================== 公共 ==================
    def reload_ranges(self):
        # 序号守卫：并发刷新时只接受最新一次响应，避免旧数据清空新数据
        self._range_seq = getattr(self, "_range_seq", 0) + 1
        seq = self._range_seq

        def _load():
            return self.ctx.service.list_ranges()

        def _done(ranges):
            if seq != getattr(self, "_range_seq", 0):
                return  # 过期响应
            active = [r for r in ranges if r["range_status"] == "active"]
            for combo in (self.single_range, self.batch_range):
                current = combo.currentData()
                combo.clear()
                for rng in active:
                    combo.addItem(
                        f"#{rng['id']} {rng['range_name']}"
                        f"（下一个 {rng['next_number']}）", rng["id"])
                if current is not None:
                    idx = combo.findData(current)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
            self.range_table.setRowCount(len(ranges))
            for i, rng in enumerate(ranges):
                self.range_table.setItem(
                    i, 0, QTableWidgetItem(str(rng["id"])))
                self.range_table.setItem(
                    i, 1, QTableWidgetItem(rng["range_name"]))
                self.range_table.setItem(
                    i, 2, QTableWidgetItem(rng["barcode_type"]))
                self.range_table.setItem(
                    i, 3, QTableWidgetItem(str(rng["next_number"])))
                self.range_table.setItem(
                    i, 4, QTableWidgetItem(str(rng["end_number"])))
                self.range_table.setItem(
                    i, 5, QTableWidgetItem(
                        STATUS_LABELS.get(rng["range_status"],
                                           rng["range_status"])))

        RunLater(self).run(_load, on_done=_done,
                           on_error=lambda m: error(self, "加载失败", m))

    def refresh(self):
        self.reload_ranges()
