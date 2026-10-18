"""标签模板管理页：列表、创建、编辑、复制、删除、恢复预置。"""

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLineEdit, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..widgets import (
    Card, MutedLabel, PrimaryButton, RunLater, confirm, error, info, warn,
)
from ...printing.templates import LabelTemplate


class TemplatesPage(QWidget):
    def __init__(self, ctx, main_window, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.main = main_window
        self._editing_id = None

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        split = QHBoxLayout()
        # ---- 左侧列表 ----
        list_card = Card("模板列表")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["ID", "名称", "标签尺寸(mm)", "行列", "内置"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        list_card.body.addWidget(self.table)
        row = QHBoxLayout()
        btn_new = PrimaryButton("新建")
        btn_new.clicked.connect(self.new_template)
        btn_copy = QPushButton("复制")
        btn_copy.clicked.connect(self.copy_template)
        btn_delete = QPushButton("删除")
        btn_delete.setObjectName("Danger")
        btn_delete.clicked.connect(self.delete_template)
        row.addWidget(btn_new)
        row.addWidget(btn_copy)
        row.addWidget(btn_delete)
        list_card.body.addLayout(row)
        btn_restore = QPushButton("恢复预置模板")
        btn_restore.clicked.connect(self.restore_presets)
        list_card.body.addWidget(btn_restore)
        split.addWidget(list_card, 3)

        # ---- 右侧编辑 ----
        edit_card = Card("编辑模板")
        form = QFormLayout()
        self.f_name = QLineEdit()
        form.addRow("模板名称", self.f_name)
        self.f_paper_w = self._dspin(210.0, 20.0, 600.0)
        self.f_paper_h = self._dspin(297.0, 20.0, 600.0)
        form.addRow("纸张宽(mm)", self.f_paper_w)
        form.addRow("纸张高(mm)", self.f_paper_h)
        self.f_label_w = self._dspin(40.0, 5.0, 300.0)
        self.f_label_h = self._dspin(25.0, 5.0, 300.0)
        form.addRow("标签宽(mm)", self.f_label_w)
        form.addRow("标签高(mm)", self.f_label_h)
        self.f_rows = self._ispin(10, 1, 100)
        self.f_cols = self._ispin(5, 1, 50)
        form.addRow("行数", self.f_rows)
        form.addRow("列数", self.f_cols)
        self.f_gap_x = self._dspin(3.0, 0.0, 50.0)
        self.f_gap_y = self._dspin(3.0, 0.0, 50.0)
        form.addRow("水平间距(mm)", self.f_gap_x)
        form.addRow("垂直间距(mm)", self.f_gap_y)
        self.f_margin_t = self._dspin(5.0, 0.0, 50.0)
        self.f_margin_b = self._dspin(5.0, 0.0, 50.0)
        self.f_margin_l = self._dspin(5.0, 0.0, 50.0)
        self.f_margin_r = self._dspin(5.0, 0.0, 50.0)
        form.addRow("上边距(mm)", self.f_margin_t)
        form.addRow("下边距(mm)", self.f_margin_b)
        form.addRow("左边距(mm)", self.f_margin_l)
        form.addRow("右边距(mm)", self.f_margin_r)
        self.f_bar_h = self._dspin(15.0, 5.0, 100.0)
        form.addRow("条码高度(mm)", self.f_bar_h)
        self.f_orientation = QComboBox()
        self.f_orientation.addItems(["portrait", "landscape"])
        form.addRow("纸张方向", self.f_orientation)
        self.f_copies = self._ispin(1, 1, 99)
        form.addRow("每标签份数", self.f_copies)
        self.f_show_text = QCheckBox("显示可读数字")
        self.f_show_text.setChecked(True)
        form.addRow("", self.f_show_text)
        edit_card.body.addLayout(form)
        self.f_hint = MutedLabel("")
        edit_card.body.addWidget(self.f_hint)
        row2 = QHBoxLayout()
        self.btn_save = PrimaryButton("保存")
        self.btn_save.clicked.connect(self.save_template)
        self.btn_save.setEnabled(False)
        row2.addWidget(self.btn_save)
        row2.addStretch(1)
        edit_card.body.addLayout(row2)
        split.addWidget(edit_card, 2)
        layout.addLayout(split)
        layout.addStretch(1)
        self.reload()

    # ---------- 列表 ----------
    @staticmethod
    def _dspin(value, minimum, maximum):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setDecimals(1)
        return spin

    @staticmethod
    def _ispin(value, minimum, maximum):
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def reload(self):
        def _load():
            return self.ctx.templates.list_templates()

        def _done(templates):
            self.table.setRowCount(len(templates))
            for i, t in enumerate(templates):
                self.table.setItem(i, 0, QTableWidgetItem(str(t["id"])))
                self.table.setItem(i, 1, QTableWidgetItem(t["template_name"]))
                self.table.setItem(
                    i, 2, QTableWidgetItem(
                        f"{t['label_width']:g} x {t['label_height']:g}"))
                self.table.setItem(
                    i, 3, QTableWidgetItem(f"{t['rows']} x {t['columns']}"))
                self.table.setItem(
                    i, 4, QTableWidgetItem("是" if t["builtin"] else ""))

        RunLater(self).run(_load, on_done=_done,
                           on_error=lambda m: error(self, "加载失败", m))

    def _on_select(self):
        row = self.table.currentRow()
        if row < 0:
            return
        template_id = int(self.table.item(row, 0).text())

        def _load():
            return self.ctx.templates.get_template(template_id)

        def _done(data):
            if not data:
                return
            self._editing_id = template_id
            t = LabelTemplate.from_dict(data)
            self.f_name.setText(t.template_name)
            self.f_paper_w.setValue(t.paper_width)
            self.f_paper_h.setValue(t.paper_height)
            self.f_label_w.setValue(t.label_width)
            self.f_label_h.setValue(t.label_height)
            self.f_rows.setValue(t.rows)
            self.f_cols.setValue(t.columns)
            self.f_gap_x.setValue(t.horizontal_gap)
            self.f_gap_y.setValue(t.vertical_gap)
            self.f_margin_t.setValue(t.margins.get("top", 5.0))
            self.f_margin_b.setValue(t.margins.get("bottom", 5.0))
            self.f_margin_l.setValue(t.margins.get("left", 5.0))
            self.f_margin_r.setValue(t.margins.get("right", 5.0))
            self.f_bar_h.setValue(t.barcode_height)
            self.f_orientation.setCurrentText(t.orientation)
            self.f_copies.setValue(t.copies)
            self.f_show_text.setChecked(t.show_text)
            self.btn_save.setEnabled(not t.builtin)
            self.f_hint.setText(
                "内置模板不可修改，可复制后自定义" if t.builtin else "")

        RunLater(self).run(_load, on_done=_done,
                           on_error=lambda m: error(self, "加载失败", m))

    # ---------- 操作 ----------
    def new_template(self):
        self._editing_id = None
        self.f_name.setText("自定义模板")
        self.f_paper_w.setValue(210.0)
        self.f_paper_h.setValue(297.0)
        self.f_label_w.setValue(40.0)
        self.f_label_h.setValue(25.0)
        self.f_rows.setValue(10)
        self.f_cols.setValue(5)
        self.f_gap_x.setValue(3.0)
        self.f_gap_y.setValue(3.0)
        self.f_margin_t.setValue(5.0)
        self.f_margin_b.setValue(5.0)
        self.f_margin_l.setValue(5.0)
        self.f_margin_r.setValue(5.0)
        self.f_bar_h.setValue(15.0)
        self.f_orientation.setCurrentText("portrait")
        self.f_copies.setValue(1)
        self.f_show_text.setChecked(True)
        self.btn_save.setEnabled(True)
        self.f_hint.setText("")
        self.table.clearSelection()

    def _collect(self):
        return LabelTemplate(
            template_name=self.f_name.text().strip(),
            paper_width=self.f_paper_w.value(),
            paper_height=self.f_paper_h.value(),
            label_width=self.f_label_w.value(),
            label_height=self.f_label_h.value(),
            rows=self.f_rows.value(),
            columns=self.f_cols.value(),
            margins={
                "top": self.f_margin_t.value(),
                "bottom": self.f_margin_b.value(),
                "left": self.f_margin_l.value(),
                "right": self.f_margin_r.value(),
            },
            horizontal_gap=self.f_gap_x.value(),
            vertical_gap=self.f_gap_y.value(),
            orientation=self.f_orientation.currentText(),
            barcode_height=self.f_bar_h.value(),
            show_text=self.f_show_text.isChecked(),
            copies=self.f_copies.value(),
        )

    def save_template(self):
        t = self._collect()
        errors, _warnings = t.validate()
        if errors:
            warn(self, "参数非法", "；".join(errors))
            return
        template_id = self._editing_id

        def _do():
            if template_id is None:
                return self.ctx.templates.create_template(t)
            return self.ctx.templates.update_template(template_id, t)

        def _done(_result):
            info(self, "已保存", f"模板「{t.template_name}」已保存")
            self._editing_id = None
            self.btn_save.setEnabled(False)
            self.reload()
            print_page = getattr(self.main, "print_page", None)
            if print_page is not None:
                print_page.reload_templates()

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "保存失败", m))

    def copy_template(self):
        row = self.table.currentRow()
        if row < 0:
            warn(self, "未选择", "请先选择要复制的模板")
            return
        template_id = int(self.table.item(row, 0).text())
        name, ok = QInputDialog.getText(self, "复制模板", "新模板名称：")
        if not ok or not name.strip():
            return

        def _do():
            return self.ctx.templates.copy_template(template_id, name.strip())

        def _done(_result):
            info(self, "已复制", f"模板「{name.strip()}」已创建")
            self.reload()

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "复制失败", m))

    def delete_template(self):
        row = self.table.currentRow()
        if row < 0:
            warn(self, "未选择", "请先选择要删除的模板")
            return
        template_id = int(self.table.item(row, 0).text())
        name = self.table.item(row, 1).text()
        if self.table.item(row, 4).text() == "是":
            warn(self, "内置模板", "内置模板不可删除")
            return
        if not confirm(self, "确认删除", f"确定删除模板「{name}」？"):
            return

        def _do():
            return self.ctx.templates.delete_template(template_id)

        def _done(_result):
            info(self, "已删除", f"模板「{name}」已删除")
            self._editing_id = None
            self.btn_save.setEnabled(False)
            self.reload()

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "删除失败", m))

    def restore_presets(self):
        if not confirm(self, "恢复预置", "将重新写入 4 套预置模板（不影响自定义模板）"):
            return

        def _do():
            return self.ctx.templates.restore_presets()

        def _done(count):
            info(self, "已恢复", f"恢复 {count} 套预置模板")
            self.reload()

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "操作失败", m))

    def refresh(self):
        self.reload()
