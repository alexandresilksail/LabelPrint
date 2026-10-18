"""打印中心：选择编码、套用模板、版面预览、导出 PDF/SVG/PNG/CSV。"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..widgets import (
    Card, MutedLabel, PrimaryButton, RunLater, error, info, warn,
)
from ...barcode.encoder import encode_with_quiet_zone
from ...printing.preview import compute_layout, fit_barcode
from ...printing.templates import LabelTemplate

STATUS_LABELS = {
    "generated": "待分配",
    "allocated": "已分配",
    "used": "已使用",
    "invalidated": "已作废",
}


class SheetPreviewWidget(QWidget):
    """按模板几何绘制整页版面预览（含条码与静区，等比缩放）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.template = None
        self.items = []
        self.setMinimumSize(320, 420)

    def set_data(self, template, items):
        self.template = template
        self.items = list(items)
        self.update()

    def paintEvent(self, event):
        if not self.template:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        t = self.template
        # 页面等比缩放到控件
        margin_px = 12
        avail_w = self.width() - 2 * margin_px
        avail_h = self.height() - 2 * margin_px
        scale = min(avail_w / t.paper_width, avail_h / t.paper_height)
        page_w = t.paper_width * scale
        page_h = t.paper_height * scale
        x0 = (self.width() - page_w) / 2
        y0 = (self.height() - page_h) / 2
        painter.fillRect(QRectF(x0, y0, page_w, page_h), QColor("white"))
        painter.setPen(QPen(QColor("#D1D5DB"), 1))
        painter.drawRect(QRectF(x0, y0, page_w, page_h))

        layout = compute_layout(t)
        filled = 0
        for slot in layout["slots"]:
            if filled >= len(self.items):
                break
            value, barcode_type = self.items[filled]
            filled += 1
            sx = x0 + slot["x"] * scale
            sy = y0 + slot["y"] * scale
            sw = slot["width"] * scale
            sh = slot["height"] * scale
            painter.setPen(QPen(QColor("#E5E7EB"), 1))
            painter.drawRect(QRectF(sx, sy, sw, sh))
            fit = fit_barcode(t.label_width, t.label_height, barcode_type,
                              bar_height_mm=t.barcode_height)
            module_px = fit["module_mm"] * scale
            if module_px <= 0:
                continue
            try:
                modules = encode_with_quiet_zone(value, barcode_type)
            except Exception:  # nosec B112 - 预览中跳过单个非法编码，其余继续绘制
                continue
            bw = len(modules) * module_px
            bh = t.barcode_height * scale
            bx = sx + (sw - bw) / 2
            by = sy + 4
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("black"))
            for i, m in enumerate(modules):
                if m == "1":
                    painter.drawRect(QRectF(bx + i * module_px, by,
                                            module_px, bh))
            if t.show_text:
                painter.setPen(QColor("#374151"))
                font = painter.font()
                font.setPixelSize(max(7, int(3.0 * scale * 1.2)))
                painter.setFont(font)
                painter.drawText(
                    QRectF(sx, by + bh + 2, sw, 16), Qt.AlignCenter, value)
        painter.end()


class PrintPage(QWidget):
    def __init__(self, ctx, main_window, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.main = main_window
        self.records = []
        self.templates = []

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        top = QHBoxLayout()

        # ---- 左：编码选择 ----
        left = Card("选择编码（可多选）")
        self.filter_status = QComboBox()
        self.filter_status.addItem("全部状态", "")
        for code, label in STATUS_LABELS.items():
            self.filter_status.addItem(label, code)
        self.filter_status.currentIndexChanged.connect(self.reload_records)
        left.body.addWidget(self.filter_status)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["编码", "类型", "状态", "产品"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        left.body.addWidget(self.table)
        top.addWidget(left, 5)

        # ---- 中：预览 ----
        mid = Card("版面预览")
        self.preview = SheetPreviewWidget()
        mid.body.addWidget(self.preview)
        top.addWidget(mid, 3)

        # ---- 右：模板与导出 ----
        right = Card("模板与导出")
        self.template_combo = QComboBox()
        right.body.addWidget(QLabel("标签模板"))
        right.body.addWidget(self.template_combo)
        btn_apply = PrimaryButton("应用模板")
        btn_apply.clicked.connect(self.apply_template)
        right.body.addWidget(btn_apply)
        self.preview_info = MutedLabel("")
        right.body.addWidget(self.preview_info)
        right.body.addWidget(QLabel("导出"))
        for text, handler in [
            ("导出 PDF 标签页", self.export_pdf),
            ("导出 SVG（单个）", self.export_svg),
            ("导出 PNG（单个）", self.export_png),
            ("导出 CSV", self.export_csv),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            right.body.addWidget(btn)
        right.body.addStretch(1)
        top.addWidget(right, 2)
        layout.addLayout(top)
        layout.addStretch(1)

        self.reload_records()
        self.reload_templates()

    # ================== 数据 ==================
    def reload_records(self):
        status = self.filter_status.currentData()

        def _load():
            return self.ctx.service.query_records(status=status or None)

        def _done(records):
            self.records = records
            self.table.setRowCount(len(records))
            for i, r in enumerate(records):
                self.table.setItem(
                    i, 0, QTableWidgetItem(r["barcode_value"]))
                self.table.setItem(
                    i, 1, QTableWidgetItem(r["barcode_type"]))
                self.table.setItem(
                    i, 2, QTableWidgetItem(
                        STATUS_LABELS.get(r["allocation_status"],
                                           r["allocation_status"])))
                self.table.setItem(
                    i, 3, QTableWidgetItem(r.get("product_name") or ""))

        RunLater(self).run(_load, on_done=_done,
                           on_error=lambda m: error(self, "加载失败", m))

    def reload_templates(self):
        def _load():
            return self.ctx.templates.list_templates()

        def _done(templates):
            self.templates = templates
            current = self.template_combo.currentData()
            self.template_combo.clear()
            for t in templates:
                self.template_combo.addItem(
                    f"#{t['id']} {t['template_name']}", t["id"])
            if current is not None:
                idx = self.template_combo.findData(current)
                if idx >= 0:
                    self.template_combo.setCurrentIndex(idx)
            if templates:
                self.apply_template()

        RunLater(self).run(_load, on_done=_done,
                           on_error=lambda m: error(self, "加载失败", m))

    def current_template(self):
        template_id = self.template_combo.currentData()
        if template_id is None:
            return None
        data = next((t for t in self.templates if t["id"] == template_id), None)
        return LabelTemplate.from_dict(data) if data else None

    def selected_items(self):
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        items = []
        for row in sorted(rows):
            r = self.records[row]
            items.append((r["barcode_value"], r["barcode_type"]))
        return items

    def apply_template(self):
        t = self.current_template()
        if not t:
            return
        items = self.selected_items()
        expanded = [it for it in items for _ in range(t.copies)]
        self.preview.set_data(t, expanded)
        slots = compute_layout(t)["slots"]
        self.preview_info.setText(
            f"每页 {len(slots)} 个标签位；已选 {len(items)} 个编码"
            f"（含份数共 {len(expanded)} 个）")

    # ================== 导出 ==================
    def _require_selection(self):
        items = self.selected_items()
        if not items:
            warn(self, "未选择", "请先在左侧表格选择要导出的编码")
            return None
        return items

    def export_pdf(self):
        items = self._require_selection()
        if not items:
            return
        t = self.current_template()
        if not t:
            warn(self, "未选择模板", "请先选择标签模板")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 PDF", "labels.pdf", "PDF (*.pdf)")
        if not path:
            return

        def _do():
            from ...printing.pdf import render_label_sheet_pdf
            expanded = [it for it in items for _ in range(t.copies)]
            return render_label_sheet_pdf(expanded, t, path)

        def _done(meta):
            info(self, "导出完成",
                 f"已生成 {meta['pages']} 页 PDF（{meta['labels_total']} 个标签）")

        RunLater(self).run(_do, on_done=_done,
                           on_error=lambda m: error(self, "导出失败", m))

    def export_svg(self):
        items = self._require_selection()
        if not items:
            return
        if len(items) > 1:
            warn(self, "多个编码", "SVG 导出请只选择一个编码（或改用 PDF）")
            return
        value, barcode_type = items[0]
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 SVG", f"{value}.svg", "SVG (*.svg)")
        if not path:
            return

        def _do():
            from ...barcode.svg import render_svg
            svg, _meta = render_svg(value, barcode_type)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(svg)
            return path

        RunLater(self).run(_do, on_done=lambda _p: info(self, "导出完成", path),
                           on_error=lambda m: error(self, "导出失败", m))

    def export_png(self):
        items = self._require_selection()
        if not items:
            return
        if len(items) > 1:
            warn(self, "多个编码", "PNG 导出请只选择一个编码（或改用 PDF）")
            return
        value, barcode_type = items[0]
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 PNG", f"{value}.png", "PNG (*.png)")
        if not path:
            return

        def _do():
            from ...barcode.renderer import render_png
            return render_png(value, barcode_type, path)

        RunLater(self).run(
            _do, on_done=lambda _m: info(self, "导出完成", path),
            on_error=lambda m: error(self, "导出失败", m))

    def export_csv(self):
        items = self._require_selection()
        if not items:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "records.csv", "CSV (*.csv)")
        if not path:
            return

        def _do():
            from ...barcode.csvio import export_records_csv
            rows = [self.records[i.row()]
                    for i in sorted({i.row() for i in self.table.selectedIndexes()})]
            return export_records_csv(rows, path)

        RunLater(self).run(_do, on_done=lambda _p: info(self, "导出完成", path),
                           on_error=lambda m: error(self, "导出失败", m))

    def refresh(self):
        self.reload_records()
