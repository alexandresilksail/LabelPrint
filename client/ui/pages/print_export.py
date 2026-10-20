"""打印中心：选择编码、套用模板、版面预览、导出 PDF/SVG/PNG/CSV，
以及真实打印接入（打印机枚举、驱动指引、参数、预览、提交、取消、校准）。

诚信约定：
- 提交成功只提示“已提交到打印队列”，绝不声称物理打印完成；
- ``verified_with_hardware=False`` 的设备在状态行加注“未实测”；
- 驱动只提供官方来源与指引，不自动下载/安装、不禁用签名检查。
"""

import os
import tempfile

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from ..widgets import (
    Card, MutedLabel, PrimaryButton, RunLater, confirm, error, info, warn,
)
from ...barcode.encoder import encode_with_quiet_zone
from ...printing.adapters.registry import build_adapter
from ...printing.calibration import (
    DEFAULT_QUIET_MODULES, apply_offset, check_label_geometry,
    check_module_width, check_quiet_zone, render_test_page_pdf, verify_scale,
)
from ...printing.config_store import (
    PrinterConfigStore, PrinterPrintConfig,
)
from ...printing.driver_installer import (
    ElevationResult, build_driver_guidance, detect_driver_state,
    elevate_process, install_guidance_for_state,
)
from ...printing.errors import PrintError
from ...printing.job import ValidationIssue
from ...printing.pdf import render_label_sheet_pdf
from ...printing.preview import compute_layout, fit_barcode
from ...printing.print_session import PrintJobSession
from . import print_export_logic as L

STATUS_LABELS = {
    "generated": "待分配",
    "allocated": "已分配",
    "used": "已使用",
    "invalidated": "已作废",
}


# ---------------------------------------------------------------------------
# 驱动安装指引对话框（guidance only：不自动安装、不禁用签名检查）
# ---------------------------------------------------------------------------
class DriverGuidanceDialog(QDialog):
    """展示厂商官方驱动来源与兼容性说明，并允许提权运行用户自备安装程序。

    红线：本对话框不下载、不释放任何驱动；``elevate_process`` 仅启动用户
    自己从官方渠道下载好的安装程序，由 Windows UAC 完成授权；不提供
    “跳过签名校验”的旁路。
    """

    def __init__(self, caps, parent=None):
        super().__init__(parent)
        self.caps = caps
        self.setWindowTitle("驱动安装指引")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        manufacturer = caps.driver.driver_name if caps.driver else None
        self.guidance = build_driver_guidance(
            manufacturer or caps.printer_name, caps.printer_name)

        head = QLabel("驱动安装指引（仅指引，本程序不自动安装驱动）")
        head.setObjectName("Title")
        layout.addWidget(head)

        form = QFormLayout()
        form.addRow("设备/队列:", QLabel(caps.printer_name))
        form.addRow("识别厂商:", QLabel(self.guidance.manufacturer or "未知"))
        form.addRow("设备型号:", QLabel(self.guidance.model or "未知"))
        layout.addLayout(form)

        if self.guidance.matched:
            url = QLabel(f"官方下载页：{self.guidance.official_url}")
            url.setWordWrap(True)
            url.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(url)
            notes = QLabel(f"兼容性说明：{self.guidance.notes}")
            notes.setWordWrap(True)
            layout.addWidget(notes)
            signed = self.guidance.verified_signed
            signed_text = {True: "已知提供数字签名驱动",
                            False: "已知未核实签名状态",
                            None: "签名状态未核实，请在官网页面二次确认"}[signed]
            layout.addWidget(MutedLabel(f"签名状态：{signed_text}"))
        else:
            layout.addWidget(MutedLabel(self.guidance.generic_hint))

        layout.addWidget(MutedLabel(
            "安全说明：本程序不会自动下载或安装驱动，不会关闭/绕过驱动签名"
            "检查，也不修改任何 Windows 安全设置。请自行从厂商官方网站下载"
            "对应型号的 Windows 驱动安装程序。"))

        # ---- 选择用户自备的官方安装程序并提权运行 ----
        self.path_label = MutedLabel("尚未选择安装程序")
        btn_pick = QPushButton("选择已下载的官方驱动安装程序…")
        btn_pick.clicked.connect(self._pick_installer)
        layout.addWidget(btn_pick)
        layout.addWidget(self.path_label)

        self.btn_run = QPushButton("以管理员身份运行所选安装程序（UAC 授权）")
        self.btn_run.setEnabled(False)
        self.btn_run.clicked.connect(self._run_elevated)
        layout.addWidget(self.btn_run)

        self.result_label = MutedLabel("")
        layout.addWidget(self.result_label)

        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    def _pick_installer(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择官方驱动安装程序", "",
            "安装程序 (*.exe *.msi);;所有文件 (*)")
        if not path:
            return
        self.installer_path = path
        self.path_label.setText(f"已选择：{path}")
        self.btn_run.setEnabled(True)

    def _run_elevated(self):
        path = getattr(self, "installer_path", "")
        if not path:
            return
        try:
            outcome = elevate_process(path, [])
        except PrintError as exc:
            self.result_label.setText(exc.user_message)
            return
        text = {
            ElevationResult.LAUNCHED:
                "驱动安装程序已启动；启动成功不等于安装成功。请在安装完成后"
                "返回点击『刷新打印机』重新枚举，并核对队列与驱动状态。",
            ElevationResult.REQUIRES_UAC:
                "需要管理员/UAC 授权但未获得，请在弹出的系统授权框中点“是”。",
            ElevationResult.CANCELLED_BY_USER:
                "用户在 UAC 授权框点了“否”，未启动安装程序。",
            ElevationResult.FAILED:
                f"启动失败（系统错误码 {outcome.error_code}），请确认文件完整。",
        }[outcome.status]
        self.result_label.setText(text)


# ---------------------------------------------------------------------------
# 纸张不一致确认对话框：确认转换 / 改用模板纸张 / 中止（三选一，禁止静默处理）
# ---------------------------------------------------------------------------
class PaperConversionDialog(QDialog):
    """模板纸张与所选纸张不一致时的明确确认。

    返回选择：``use_selected``（按所选纸张重新排版，允许转换）、
    ``use_template``（改用模板纸张）、``abort``（中止打印）。
    """

    def __init__(self, parent=None, message: str = ""):
        super().__init__(parent)
        self._choice = "abort"
        self.setWindowTitle("纸张不一致，请确认")
        self.setMinimumWidth(500)
        v = QVBoxLayout(self)
        lbl = QLabel(message)
        lbl.setWordWrap(True)
        v.addWidget(lbl)
        row = QHBoxLayout()
        b_confirm = QPushButton("确认按所选纸张重新排版")
        b_confirm.clicked.connect(self._choose_selected)
        b_template = QPushButton("改用模板纸张")
        b_template.clicked.connect(self._choose_template)
        b_abort = QPushButton("取消（中止打印）")
        b_abort.clicked.connect(self.reject)
        row.addWidget(b_confirm)
        row.addWidget(b_template)
        row.addStretch(1)
        row.addWidget(b_abort)
        v.addLayout(row)

    def _choose_selected(self):
        self._choice = "use_selected"
        self.accept()

    def _choose_template(self):
        self._choice = "use_template"
        self.accept()

    @classmethod
    def ask(cls, parent, message: str) -> str:
        dlg = cls(parent, message)
        dlg.exec()
        return dlg._choice


# ---------------------------------------------------------------------------
# 校准对话框：测试页预览、偏移/间隙/静区/模块宽度、检查结果、配置保存/恢复
# ---------------------------------------------------------------------------
class CalibrationDialog(QDialog):
    """单台打印机的校准与设置对话框。

    - 渲染标准测试页 PDF 并预览；
    - 偏移/标签间隙/静区/模块宽度输入；
    - 实时展示 check_quiet_zone / check_module_width /
      check_label_geometry 结果；
    - 配置按打印机名独立保存到 PrinterConfigStore，可恢复默认。
    """

    def __init__(self, caps, config_store, paper=None, parent=None):
        super().__init__(parent)
        self.caps = caps
        self._store = config_store
        self.setWindowTitle(f"校准与设置 — {caps.printer_name}")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.existing = config_store.load(caps.printer_name)
        paper = paper or (self.existing.paper if self.existing else None)
        if paper is None:
            # 无可用纸张时给一个占位（调用方应保证纸张合法）
            paper = L.pick_paper(caps, 100.0, 100.0)
        self.paper = paper

        head = QLabel("校准与设置")
        head.setObjectName("Title")
        layout.addWidget(head)

        # ---- 测试页预览 ----
        self.preview_label = MutedLabel("正在生成测试页…")
        layout.addWidget(self.preview_label)
        self._render_test_page()

        # ---- 参数 ----
        form = QFormLayout()
        self.offset_x = QDoubleSpinBox()
        self.offset_x.setRange(-500.0, 500.0)
        self.offset_x.setDecimals(2)
        self.offset_x.setSingleStep(0.1)
        self.offset_x.setSuffix(" mm")
        self.offset_y = QDoubleSpinBox()
        self.offset_y.setRange(-500.0, 500.0)
        self.offset_y.setDecimals(2)
        self.offset_y.setSingleStep(0.1)
        self.offset_y.setSuffix(" mm")
        self.gap = QDoubleSpinBox()
        self.gap.setRange(0.0, 50.0)
        self.gap.setDecimals(2)
        self.gap.setSingleStep(0.5)
        self.gap.setSuffix(" mm")
        self.quiet = QDoubleSpinBox()
        self.quiet.setRange(0.0, 50.0)
        self.quiet.setDecimals(2)
        self.quiet.setSingleStep(0.1)
        self.quiet.setSuffix(" mm")
        self.module = QDoubleSpinBox()
        self.module.setRange(0.1, 1.0)
        self.module.setDecimals(3)
        self.module.setSingleStep(0.01)
        self.module.setSuffix(" mm")
        form.addRow("水平偏移:", self.offset_x)
        form.addRow("垂直偏移:", self.offset_y)
        form.addRow("标签间隙:", self.gap)
        form.addRow("静区宽度:", self.quiet)
        form.addRow("模块宽度:", self.module)
        layout.addLayout(form)

        # 设备能力如实提示（不夸大、不伪造实测）
        self.cap_hint = MutedLabel("")
        layout.addWidget(self.cap_hint)
        self._refresh_cap_hints()

        # 回填已保存配置
        if self.existing is not None:
            self.offset_x.setValue(self.existing.offset_x_mm)
            self.offset_y.setValue(self.existing.offset_y_mm)
            self.gap.setValue(self.existing.label_gap_mm)
            if self.existing.quiet_zone_mm is not None:
                self.quiet.setValue(self.existing.quiet_zone_mm)
            if self.existing.module_mm is not None:
                self.module.setValue(self.existing.module_mm)

        # ---- 检查结果 ----
        self.check_label = MutedLabel("")
        layout.addWidget(self.check_label)
        for w in (self.offset_x, self.offset_y, self.gap,
                  self.quiet, self.module):
            w.valueChanged.connect(self._refresh_checks)
        self._refresh_checks()

        # ---- 按钮 ----
        row = QHBoxLayout()
        btn_save = PrimaryButton("保存配置")
        btn_save.clicked.connect(self._save)
        btn_reset = QPushButton("恢复默认")
        btn_reset.clicked.connect(self._reset)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_save)
        row.addWidget(btn_reset)
        row.addStretch(1)
        row.addWidget(btn_close)
        layout.addLayout(row)

    # ---------------- 能力如实提示 ----------------
    def _refresh_cap_hints(self):
        hints = L.capability_hints(self.caps)
        # 间隙控件旁的专项提示
        if self.caps is not None and not self.caps.supports_gap_sensor:
            hints.insert(0, "该设备不支持间隙传感，间隙仅用于专用协议打印/不生效")
        self.cap_hint.setText("\n".join(f"· {h}" for h in hints))

    # ---------------- 测试页 ----------------
    def _render_test_page(self):
        try:
            fd, path = tempfile.mkstemp(suffix=".pdf", prefix="calib_test_")
            os.close(fd)
            render_test_page_pdf(path, self.paper)
            try:
                from ...printing.pdf_render import rasterize_preview
                paths = rasterize_preview(path, dpi=150, max_pages=1)
            except PrintError as exc:
                self.preview_label.setText(f"测试页已生成，预览不可用："
                                           f"{exc.user_message}")
                return
            if paths:
                pix = QPixmap(paths[0])
                if not pix.isNull():
                    self.preview_label.setPixmap(
                        pix.scaledToWidth(360, Qt.SmoothTransformation))
                    return
            self.preview_label.setText("测试页已生成（无预览图）")
        except PrintError as exc:
            self.preview_label.setText(f"测试页生成失败：{exc.user_message}")

    # ---------------- 检查 ----------------
    def _refresh_checks(self):
        issues = []
        q = check_quiet_zone(self.module.value(), 11)
        if q:
            issues.append(q)
        mw = check_module_width(self.module.value())
        if mw:
            issues.append(mw)
        issues.extend(check_label_geometry(
            self.paper, self.caps, self.gap.value()))
        if not issues:
            self.check_label.setText("检查结果：全部通过")
            return
        lines = [f"[{i.severity}] {i.message}" for i in issues]
        self.check_label.setText("检查结果：\n" + "\n".join(lines))

    # ---------------- 保存 / 恢复 ----------------
    def _save(self):
        base = self.existing or self._store.defaults(self.caps.printer_name)
        cfg = PrinterPrintConfig(
            printer_name=self.caps.printer_name,
            offset_x_mm=self.offset_x.value(),
            offset_y_mm=self.offset_y.value(),
            paper=self.paper,
            dpi=base.dpi,
            quality=base.quality,
            default_copies=base.default_copies,
            label_gap_mm=self.gap.value(),
            quiet_zone_mm=self.quiet.value(),
            module_mm=self.module.value(),
            use_calibration=True,
        )
        self._store.save(cfg)
        info(self, "配置已保存",
             f"已按打印机「{self.caps.printer_name}」独立保存校准配置。")

    def _reset(self):
        ok = self._store.reset(self.caps.printer_name)
        self.offset_x.setValue(0.0)
        self.offset_y.setValue(0.0)
        self.gap.setValue(0.0)
        self.quiet.setValue(0.0)
        self.module.setValue(0.33)
        self._refresh_checks()
        info(self, "恢复默认",
             "已删除该打印机的本地配置，恢复默认值。" if ok
             else "该打印机没有已保存的配置。")


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
            sx = x0 + slot["x_mm"] * scale
            sy = y0 + slot["y_mm"] * scale
            sw = t.label_width * scale
            sh = t.label_height * scale
            painter.setPen(QPen(QColor("#E5E7EB"), 1))
            painter.drawRect(QRectF(sx, sy, sw, sh))
            fit = fit_barcode(t.label_width, t.label_height, barcode_type,
                              show_text=t.show_text)
            module_px = fit["module_mm"] * scale
            if module_px <= 0:
                continue
            try:
                modules = encode_with_quiet_zone(value, barcode_type)
            except Exception:  # nosec B112 - 预览中跳过单个非法编码，其余继续绘制
                continue
            bw = len(modules) * module_px
            bh = fit["barcode_h_mm"] * scale
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
        self._printers = []
        self._current_caps = None
        # 任务会话：并发锁 / 任务句柄 / 临时文件生命周期（替换单例 _job_id）
        self._session = PrintJobSession(keep_recent=2)
        self._submitting = False
        self._config_store = PrinterConfigStore(ctx.data_dir / "printer_configs")

        # 应用退出时一次性清理全部临时文件与任务句柄
        app = self._qapp()
        if app is not None:
            app.aboutToQuit.connect(self._session.cleanup_all)

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

        # ---- 右：模板与导出 + 打印机与打印 ----
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

        self._build_print_section(right.body)
        right.body.addStretch(1)
        top.addWidget(right, 2)
        layout.addLayout(top)
        layout.addStretch(1)

        self.reload_records()
        self.reload_templates()
        self.refresh_printers()

    @staticmethod
    def _qapp():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance()

    def closeEvent(self, event):
        """页面关闭：清理全部临时文件与任务句柄（幂等）。"""
        try:
            self._session.cleanup_all()
        finally:
            super().closeEvent(event)

    # ================== 打印机与打印区块 ==================
    def _build_print_section(self, body):
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        body.addWidget(divider)
        section = QLabel("打印机与打印")
        section.setObjectName("Title")
        body.addWidget(section)

        # 打印机选择 + 刷新
        row = QHBoxLayout()
        self.printer_combo = QComboBox()
        self.printer_combo.currentIndexChanged.connect(self._on_printer_changed)
        row.addWidget(self.printer_combo, 1)
        self.btn_refresh_printers = QPushButton("刷新打印机")
        self.btn_refresh_printers.clicked.connect(self.refresh_printers)
        row.addWidget(self.btn_refresh_printers)
        body.addLayout(row)

        self.printer_status_label = MutedLabel("尚未枚举打印机")
        body.addWidget(self.printer_status_label)
        self.driver_state_label = MutedLabel("")
        body.addWidget(self.driver_state_label)
        self.btn_driver_guidance = QPushButton("驱动安装指引")
        self.btn_driver_guidance.clicked.connect(self.open_driver_guidance)
        body.addWidget(self.btn_driver_guidance)

        # ---- 打印方式（协议只能由用户显式选择，禁止按品牌推断）----
        body.addWidget(QLabel("打印方式:"))
        self.mode_combo = QComboBox()
        for key, label in L.MODE_LABELS.items():
            self.mode_combo.addItem(label, key)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        body.addWidget(self.mode_combo)

        # 网络 TCP 9100 直连子行：协议 + 地址（默认隐藏）
        self.tcp_row = QWidget()
        tcp_lay = QHBoxLayout(self.tcp_row)
        tcp_lay.setContentsMargins(0, 0, 0, 0)
        self.tcp_proto_combo = QComboBox()
        self.tcp_proto_combo.addItem("ZPL", "zpl")
        self.tcp_proto_combo.addItem("TSPL", "tspl")
        self.tcp_proto_combo.addItem("CPCL", "cpcl")
        self.tcp_proto_combo.currentIndexChanged.connect(
            self._refresh_mode_basis)
        self.tcp_host_edit = QLineEdit()
        self.tcp_host_edit.setPlaceholderText("IP:端口，如 192.168.1.20:9100")
        tcp_lay.addWidget(QLabel("协议:"))
        tcp_lay.addWidget(self.tcp_proto_combo)
        tcp_lay.addWidget(self.tcp_host_edit, 1)
        self.tcp_row.setVisible(False)
        body.addWidget(self.tcp_row)

        self.transport_label = MutedLabel("")
        body.addWidget(self.transport_label)
        self.mode_basis_label = MutedLabel("")
        body.addWidget(self.mode_basis_label)
        self.cap_hint_label = MutedLabel("")
        body.addWidget(self.cap_hint_label)

        # 参数
        form = QFormLayout()
        self.paper_combo = QComboBox()
        form.addRow("纸张:", self.paper_combo)
        self.orientation_combo = QComboBox()
        self.orientation_combo.addItem("纵向 portrait", "portrait")
        self.orientation_combo.addItem("横向 landscape", "landscape")
        form.addRow("方向:", self.orientation_combo)
        self.copies_spin = QSpinBox()
        self.copies_spin.setRange(1, 999)
        self.copies_spin.setValue(1)
        form.addRow("份数:", self.copies_spin)
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("草稿 draft", "draft")
        self.quality_combo.addItem("标准 normal", "normal")
        self.quality_combo.addItem("高清 high", "high")
        form.addRow("质量:", self.quality_combo)
        self.offset_x_spin = QDoubleSpinBox()
        self.offset_x_spin.setRange(-500.0, 500.0)
        self.offset_x_spin.setDecimals(2)
        self.offset_x_spin.setSingleStep(0.1)
        self.offset_x_spin.setSuffix(" mm")
        form.addRow("水平偏移:", self.offset_x_spin)
        self.offset_y_spin = QDoubleSpinBox()
        self.offset_y_spin.setRange(-500.0, 500.0)
        self.offset_y_spin.setDecimals(2)
        self.offset_y_spin.setSingleStep(0.1)
        self.offset_y_spin.setSuffix(" mm")
        form.addRow("垂直偏移:", self.offset_y_spin)
        body.addLayout(form)

        # 操作按钮
        self.btn_preview = QPushButton("打印预览")
        self.btn_preview.clicked.connect(lambda: self._prepare_job(False))
        self.btn_print = PrimaryButton("打印")
        self.btn_print.clicked.connect(lambda: self._prepare_job(True))
        self.btn_cancel = QPushButton("取消任务")
        self.btn_cancel.clicked.connect(self.cancel_current_job)
        self.btn_query = QPushButton("查询任务状态")
        self.btn_query.clicked.connect(self.query_current_job)
        self.btn_calibrate = QPushButton("校准与设置")
        self.btn_calibrate.clicked.connect(self.open_calibration)
        for b in (self.btn_preview, self.btn_print, self.btn_cancel,
                  self.btn_query, self.btn_calibrate,
                  self.btn_driver_guidance):
            body.addWidget(b)

        self._set_print_controls_enabled(False)

    def _set_print_controls_enabled(self, enabled: bool):
        for b in (self.btn_preview, self.btn_print, self.btn_cancel,
                  self.btn_query, self.btn_calibrate,
                  self.btn_driver_guidance,
                  self.paper_combo, self.orientation_combo,
                  self.copies_spin, self.quality_combo,
                  self.offset_x_spin, self.offset_y_spin,
                  self.mode_combo):
            b.setEnabled(enabled)
        # 提交期间仅禁用打印/预览/取消/查询，避免重复点击；其余保持
        if self._submitting:
            self.btn_print.setEnabled(False)
            self.btn_preview.setEnabled(False)

    def _on_mode_changed(self):
        mode = self.mode_combo.currentData()
        self.tcp_row.setVisible(mode == L.MODE_TCP9100)
        self._refresh_mode_basis()

    # ---------------- 枚举 ----------------
    def refresh_printers(self):
        """刷新打印机：合并 win32print 与 Qt 两路枚举（后台线程，防阻塞）。"""
        def _do():
            enum = []
            try:
                from ...printing.windows_print import (
                    list_printers, windows_print_available)
                if windows_print_available():
                    enum.extend(list_printers())
            except PrintError:
                pass
            try:
                from ...printing.qt_print import (
                    list_qt_printers, qt_print_available)
                if qt_print_available():
                    enum.extend(list_qt_printers())
            except PrintError:
                pass
            return L.merge_capabilities_list(enum)

        RunLater(self).run(
            _do, on_done=self.set_printers,
            on_error=lambda m: error(self, "刷新打印机失败", m))

    def set_printers(self, printers):
        """用枚举（或测试注入）的能力列表填充下拉并刷新状态。"""
        self._printers = list(printers or [])
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear()
        if not self._printers:
            self.printer_combo.addItem(
                "未检测到打印机（需 Windows + 打印服务）", None)
            self._current_caps = None
        else:
            for caps in self._printers:
                label = caps.printer_name + ("（默认）" if caps.is_default else "")
                self.printer_combo.addItem(label, caps)
            idx = next((i for i, c in enumerate(self._printers)
                        if c.is_default), 0)
            self.printer_combo.setCurrentIndex(idx)
        self.printer_combo.blockSignals(False)
        self._apply_current_caps()

    def _on_printer_changed(self):
        self._apply_current_caps()

    def _apply_current_caps(self):
        caps = self.printer_combo.currentData()
        self._current_caps = caps
        if caps is None:
            self.printer_status_label.setText(
                "未检测到打印机（需 Windows + 打印服务）；导出功能不受影响")
            self.driver_state_label.setText("")
            self._set_print_controls_enabled(False)
            return

        self._set_print_controls_enabled(True)
        summary = L.summarize_capabilities(caps)
        if not caps.verified_with_hardware:
            summary += "（未实测：未经真实设备验证，请先以测试页复核）"
        self.printer_status_label.setText(summary)

        state = detect_driver_state(
            caps.printer_name,
            has_queue=True,
            driver_name=caps.driver.driver_name if caps.driver else None,
            is_offline=bool(caps.status and caps.status.offline),
        )
        self.driver_state_label.setText(install_guidance_for_state(state))

        # 通道诚信提示：USB/蓝牙/LPR 未集成（不支持）并禁用专用协议选项
        channel_ok, channel_text = L.transport_honesty(caps)
        self.transport_label.setText("连接通道：" + channel_text)
        for i in range(self.mode_combo.count()):
            key = self.mode_combo.itemData(i)
            if key in L.PROTOCOL_MODES or key == L.MODE_TCP9100:
                self.mode_combo.model().item(i).setEnabled(channel_ok)
        if not channel_ok and self.mode_combo.currentData() != L.MODE_UNIVERSAL:
            self.mode_combo.setCurrentIndex(
                self.mode_combo.findData(L.MODE_UNIVERSAL))

        # 能力如实提示（间隙传感 / 1:1 / 未实测）
        hints = L.capability_hints(caps)
        if not caps.supports_gap_sensor:
            hints.insert(0, "该设备不支持间隙传感，间隙仅用于专用协议打印/不生效")
        self.cap_hint_label.setText("\n".join(f"· {h}" for h in hints))
        self._refresh_mode_basis()

        self._populate_papers()
        self._load_printer_config(caps.printer_name)

    def _refresh_mode_basis(self):
        """打印方式选择依据：只来自显式选择/能力声明，不做品牌推断。"""
        mode = self.mode_combo.currentData() if hasattr(
            self, "mode_combo") else L.MODE_UNIVERSAL
        sub = self.tcp_proto_combo.currentData() if hasattr(
            self, "tcp_proto_combo") else None
        self.mode_basis_label.setText(
            L.mode_basis_text(self._current_caps, mode or L.MODE_UNIVERSAL,
                              sub))

    def _populate_papers(self):
        caps = self._current_caps
        self.paper_combo.blockSignals(True)
        self.paper_combo.clear()
        if caps and caps.supported_papers:
            for p in caps.supported_papers:
                self.paper_combo.addItem(
                    f"{p.name} {p.width_mm:g}x{p.height_mm:g}mm", p)
        t = self.current_template()
        w = getattr(t, "paper_width", None)
        h = getattr(t, "paper_height", None)
        if w and h:
            custom = L.pick_paper(caps, w, h)
            note = L.paper_choice_note(caps, custom)
            self.paper_combo.addItem(f"{custom.name}（{note}）", custom)
        self.paper_combo.blockSignals(False)

    def _load_printer_config(self, name: str):
        cfg = self._config_store.load(name)
        if cfg is None:
            return
        self.offset_x_spin.setValue(cfg.offset_x_mm)
        self.offset_y_spin.setValue(cfg.offset_y_mm)
        self.copies_spin.setValue(cfg.default_copies)
        q_idx = self.quality_combo.findData(cfg.quality.value)
        if q_idx >= 0:
            self.quality_combo.setCurrentIndex(q_idx)
        if cfg.paper is not None:
            for i in range(self.paper_combo.count()):
                p = self.paper_combo.itemData(i)
                if hasattr(p, "width_mm") and \
                        abs(p.width_mm - cfg.paper.width_mm) < 0.5 and \
                        abs(p.height_mm - cfg.paper.height_mm) < 0.5:
                    self.paper_combo.setCurrentIndex(i)
                    break

    def _collect_form(self, template) -> dict:
        caps = self._current_caps
        cfg = self._config_store.load(caps.printer_name) if caps else None
        mode = self.mode_combo.currentData() or L.MODE_UNIVERSAL
        return {
            "paper": self.paper_combo.currentData(),
            "orientation": self.orientation_combo.currentData(),
            "copies": self.copies_spin.value(),
            "quality": self.quality_combo.currentData(),
            "dpi": cfg.dpi if cfg else None,
            "offset_x_mm": self.offset_x_spin.value(),
            "offset_y_mm": self.offset_y_spin.value(),
            "label_gap_mm": cfg.label_gap_mm if cfg else 0.0,
            "quiet_zone_mm": cfg.quiet_zone_mm if cfg else None,
            "module_mm": cfg.module_mm if cfg else None,
            "mode": mode,
            "sub_protocol": self.tcp_proto_combo.currentData(),
            "host_port": self.tcp_host_edit.text().strip(),
        }

    # ---------------- 打印准备 / 预览 / 提交 ----------------
    def _template_paper(self, t):
        from ...printing.capabilities import PaperSize
        name = getattr(t, "template_name", None) or "模板纸张"
        return PaperSize(name=name, width_mm=float(t.paper_width),
                         height_mm=float(t.paper_height))

    def _prepare_job(self, submit_after: bool):
        items = self.selected_items()
        if not items:
            warn(self, "未选择", "请先在左侧表格选择要打印的编码")
            return
        t = self.current_template()
        if not t:
            warn(self, "未选择模板", "请先选择标签模板")
            return
        caps = self._current_caps
        if caps is None:
            error(self, "无可用打印机",
                  "未检测到可用打印机（需 Windows + 打印服务），无法提交打印")
            return

        form = self._collect_form(t)
        mode = form["mode"]
        # 仅专用协议路径构建 label_content（items 未展开）；通用队列走 PDF
        build_items = items if mode != L.MODE_UNIVERSAL else None
        job = L.build_job_from_form(
            t, caps, form, items=build_items, template=t)
        # 应用校准偏移（返回副本，不改写原规格）
        job = apply_offset(job, job.offset_x_mm, job.offset_y_mm)

        # ---- 纸张一致性：不一致必须先明确确认，禁止静默裁切/缩放 ----
        tpl_paper = self._template_paper(t)
        plan = L.resolve_paper_choice(tpl_paper, job.paper, None)
        if plan["state"] == L.PAPER_NEEDS_CONFIRM:
            choice = PaperConversionDialog.ask(
                self, L.paper_conversion_prompt(tpl_paper, job.paper))
            plan = L.resolve_paper_choice(tpl_paper, job.paper, choice)
            if plan["state"] == L.PAPER_ABORT:
                return
            job.paper = plan["paper"]
            job.allow_paper_conversion = plan["allow_paper_conversion"]

        printer = caps.printer_name

        # ---- 并发锁：仅提交路径 begin_submit；忙则禁用并提示 ----
        if submit_after:
            try:
                self._session.begin_submit(printer)
            except PrintError as exc:
                warn(self, "已有任务处理中", exc.user_message)
                # 忙锁由在途任务持有；先禁用打印按钮，待其完成后由其恢复
                self._set_submitting_ui(True)
                return
            self._set_submitting_ui(True)

        # 派生提交用能力（协议/通道只来自用户显式选择，不按品牌推断）
        try:
            eff_caps = L.effective_capabilities(
                caps, mode, sub_protocol=form["sub_protocol"],
                host_port=form["host_port"])
        except PrintError as exc:
            if submit_after:
                self._release_lock(printer)
            self._set_submitting_ui(False)
            error(self, "打印方式不可用", exc.user_message)
            return

        def _do():
            adapter = build_adapter(eff_caps)
            meta = None
            if mode == L.MODE_UNIVERSAL:
                fd, pdf_path = tempfile.mkstemp(
                    suffix=".pdf", prefix="label_print_")
                os.close(fd)
                try:
                    # items 为未展开原始编码；模板份数由渲染器唯一展开
                    meta = render_label_sheet_pdf(
                        items, t, pdf_path,
                        quiet=DEFAULT_QUIET_MODULES,
                        offset_x_mm=job.offset_x_mm,
                        offset_y_mm=job.offset_y_mm,
                        target_module_mm=job.module_mm,
                        paper_override=job.paper,
                        orientation_override=job.orientation,
                    )
                except ValueError as exc:
                    # 网格超出所选纸张：明确拒绝，不提交（含所选纸张/网格尺寸）
                    raise PrintError(
                        PrintError.PAPER_OUT_OF_RANGE,
                        f"所选纸张无法容纳标签网格：{exc}",
                        resolution="请更换纸张、改用模板纸张，或减小行列数/标签尺寸",
                    ) from exc
                self._session.register_temp(pdf_path)
                job.pdf_path = pdf_path

            issues = adapter.validate_job(job)
            # 专用协议：提交前预构建 payload，把协议不支持的条码类型等
            # 错误以 error 级 issue 展示并中止（不真正发送）
            if mode != L.MODE_UNIVERSAL:
                try:
                    adapter.build_payload(job)
                except PrintError as exc:
                    issues.append(ValidationIssue.error(
                        exc.code, exc.user_message))
            errors, warnings = L.group_issues(issues)
            # 通用路径缩放预检（warning 不阻止提交）
            if job.pdf_path:
                try:
                    scale = verify_scale(job.pdf_path, job.paper,
                                         job.scale_tolerance_mm)
                    if scale is not None and not scale.ok:
                        for wmsg in scale.warnings:
                            warnings.append(
                                ValidationIssue.warning("SCALE_MISMATCH", wmsg))
                except PrintError:
                    pass
            return {
                "adapter": adapter, "job": job, "errors": errors,
                "warnings": warnings, "meta": meta,
                "submit_after": submit_after, "printer": printer,
                "mode": mode,
            }

        RunLater(self).run(
            _do, on_done=self._on_prepared,
            on_error=lambda m: self._on_prepare_error(
                printer, submit_after, m))

    def _on_prepare_error(self, printer, submit_after, message):
        if submit_after:
            self._release_lock(printer)
        self._set_submitting_ui(False)
        error(self, "打印失败", message)

    def _set_submitting_ui(self, busy: bool):
        self._submitting = busy
        self.btn_print.setEnabled(not busy)
        self.btn_preview.setEnabled(not busy)

    def _release_lock(self, printer: str):
        self._session.end_submit(printer)

    def _on_prepared(self, payload: dict):
        errors = payload["errors"]
        warnings = payload["warnings"]
        job = payload["job"]
        printer = payload["printer"]

        if errors:
            lines = []
            for e in errors:
                line = f"· [{e.code}] {e.message}"
                if getattr(e, "resolution", ""):
                    line += f"（{e.resolution}）"
                lines.append(line)
            msg = "打印校验未通过，已中止提交：\n" + "\n".join(lines)
            if warnings:
                msg += "\n\n警告（不阻止提交，供参考）：\n" + \
                       "\n".join(f"· {w.message}" for w in warnings)
            if payload["submit_after"]:
                self._release_lock(printer)
            self._set_submitting_ui(False)
            self._session.cleanup()
            error(self, "打印校验未通过", msg)
            return

        # 打印前预览：rasterize_preview 渲染 PNG 展示（专用协议无 PDF 预览）
        paths = []
        if job.pdf_path:
            try:
                from ...printing.pdf_render import rasterize_preview
                paths = rasterize_preview(job.pdf_path, dpi=150, max_pages=1)
            except PrintError as exc:
                warn(self, "预览不可用", exc.user_message)

        self._show_preview_dialog(paths, warnings, job,
                                  mode=payload["mode"])

        if not payload["submit_after"]:
            self._session.cleanup()
            return

        confirm_text = (
            f"任务：{job.describe()}\n\n"
            "确认提交？提交成功仅代表进入队列/指令已发送，"
            "不代表物理打印完成，请查看打印机实际输出。")
        if not confirm(self, "确认提交打印", confirm_text):
            self._release_lock(printer)
            self._set_submitting_ui(False)
            self._session.cleanup()
            return
        self._submit_job(payload["adapter"], job, printer)

    def _show_preview_dialog(self, paths, warnings, job, mode=None):
        dlg = QDialog(self)
        dlg.setWindowTitle("打印预览")
        dlg.setMinimumSize(420, 520)
        v = QVBoxLayout(dlg)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        hv = QVBoxLayout(host)
        for p in paths:
            pix = QPixmap(p)
            if pix.isNull():
                continue
            lbl = QLabel()
            lbl.setPixmap(pix.scaledToWidth(360, Qt.SmoothTransformation))
            lbl.setAlignment(Qt.AlignCenter)
            hv.addWidget(lbl)
        if not paths:
            if mode and mode != L.MODE_UNIVERSAL:
                hv.addWidget(MutedLabel(
                    "专用协议直连无 PDF 预览；指令将按标签尺寸直接构建发送。"))
            else:
                hv.addWidget(MutedLabel(
                    "当前环境无法渲染 PDF 预览（需 Windows + PySide6）"))
        warn_text = "\n".join(f"· {w.message}" for w in warnings)
        if warn_text:
            hv.addWidget(MutedLabel("警告：\n" + warn_text))
        hv.addWidget(MutedLabel(
            f"任务：{job.describe()}；提交到队列/指令已发送均不代表物理打印完成。"))
        scroll.setWidget(host)
        v.addWidget(scroll)
        close = QPushButton("关闭")
        close.clicked.connect(dlg.accept)
        v.addWidget(close)
        dlg.exec()

    def _submit_job(self, adapter, job, printer):
        def _do():
            return adapter.submit(job)

        def _done(result):
            # 登记活跃任务句柄（按打印机隔离）；保留最近临时文件后清理
            self._session.set_active(printer, adapter, result.job_id,
                                     job.pdf_path)
            self._session.cleanup()
            self._release_lock(printer)
            self._set_submitting_ui(False)
            # 只声明“已提交到队列/指令已发送”，不声称物理打印完成
            info(self, "打印提交", result.message)

        def _fail(message):
            self._release_lock(printer)
            self._set_submitting_ui(False)
            error(self, "打印失败", message)

        RunLater(self).run(
            _do, on_done=_done, on_error=_fail)

    # ---------------- 取消 / 查询（经会话，按打印机隔离） ----------------
    def cancel_current_job(self):
        caps = self._current_caps
        if caps is None:
            warn(self, "无可用打印机", "请先选择打印机")
            return
        printer = caps.printer_name

        def _do():
            try:
                self._session.cancel_active(printer)
                return ("ok", None)
            except PrintError as exc:
                return ("err", exc)

        def _done(pair):
            kind, exc = pair
            if kind == "ok":
                info(self, "任务取消", "打印任务已取消")
                return
            self._classify_task_error(exc)

        RunLater(self).run(
            _do, on_done=_done,
            on_error=lambda m: error(self, "取消失败", m))

    def query_current_job(self):
        caps = self._current_caps
        if caps is None:
            warn(self, "无可用打印机", "请先选择打印机")
            return
        printer = caps.printer_name

        def _do():
            try:
                state = self._session.query_active(printer)
                return ("ok", state)
            except PrintError as exc:
                return ("err", exc)

        def _done(pair):
            kind, payload = pair
            if kind == "ok":
                finished = "（已完成）" if payload.is_finished else "（排队/进行中）"
                info(self, "任务状态",
                     f"任务 #{payload.job_id}：{payload.status_text}{finished}")
                return
            self._classify_task_error(payload)

        RunLater(self).run(
            _do, on_done=_done,
            on_error=lambda m: error(self, "查询失败", m))

    def _classify_task_error(self, exc: PrintError):
        """按错误码分类展示取消/查询结果。"""
        if exc.code == PrintError.JOB_NOT_FOUND:
            warn(self, "无待处理任务",
                 "当前没有已提交且仍在队列的任务（可能已完成或不存在）")
        elif exc.code == PrintError.TRANSPORT_NOT_IMPLEMENTED:
            warn(self, "通道不支持该操作", exc.user_message)
        elif exc.code == PrintError.SPOOL_FAILED:
            error(self, "队列操作失败", exc.user_message)
        else:
            error(self, "任务操作失败", exc.user_message)

    # ---------------- 对话框入口 ----------------
    def open_driver_guidance(self):
        if self._current_caps is None:
            warn(self, "无可用打印机", "请先选择打印机")
            return
        DriverGuidanceDialog(self._current_caps, self).exec()

    def open_calibration(self):
        if self._current_caps is None:
            warn(self, "无可用打印机", "请先选择打印机")
            return
        paper = self.paper_combo.currentData()
        CalibrationDialog(self._current_caps, self._config_store,
                          paper=paper, parent=self).exec()

    # ================== 数据 ==================
    def reload_records(self):
        status = self.filter_status.currentData()
        self._records_seq = getattr(self, "_records_seq", 0) + 1
        seq = self._records_seq

        def _load():
            return self.ctx.service.query_records(status=status or None)

        def _done(records):
            if seq != getattr(self, "_records_seq", 0):
                return  # 过期响应
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
        self._templates_seq = getattr(self, "_templates_seq", 0) + 1
        seq = self._templates_seq

        def _load():
            return self.ctx.templates.list_templates()

        def _done(templates):
            if seq != getattr(self, "_templates_seq", 0):
                return  # 过期响应
            self.templates = templates
            current = self.template_combo.currentData()
            self.template_combo.clear()
            for t in templates:
                self.template_combo.addItem(
                    f"#{t.id} {t.template_name}", t.id)
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
        return next(
            (t for t in self.templates if t.id == template_id), None)

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
        # 模板纸张变化后刷新纸张下拉
        if self._current_caps is not None:
            self._populate_papers()

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
            # items 为未展开原始编码；模板份数由渲染器唯一展开
            return render_label_sheet_pdf(items, t, path)

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
            self, "导出 PNG", "PNG (*.png)")
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
