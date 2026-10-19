"""「打印机设置向导」对话框（``--printer-setup`` 启动入口）。

用途：在独立向导里完成打印机初次接入，分三页——

1. **检测页**：枚举本机/网络队列（win32print + Qt 两路合并），展示
   驱动/端口/默认/状态与驱动状态指引，可打开官方驱动安装指引对话框；
2. **参数页**：纸张 / 方向 / 份数 / 质量 / 偏移，按打印机名独立保存到
   :class:`PrinterConfigStore`；
3. **校准页**：嵌入 :class:`CalibrationDialog`，渲染标准测试页、调整
   偏移并保存/恢复配置。

诚信约束（与打印中心一致）：
- 只提示“已提交到队列”，不声称物理打印完成（本向导不直接提交打印）；
- 未实测设备加注“未实测”；
- 驱动只给官方来源指引，不自动下载/安装、不禁用签名检查。

本模块依赖 PySide6；无 Qt 的环境由 ``client.ui.pages`` 包容错跳过导入。
"""

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from ..printing.config_store import PrinterConfigStore
from ..printing.driver_installer import (
    detect_driver_state, install_guidance_for_state,
)
from ..printing.errors import PrintError
from .pages.print_export import (
    CalibrationDialog, DriverGuidanceDialog,
)
from .pages import print_export_logic as L
from .widgets import MutedLabel, PrimaryButton, RunLater, error, info, warn


class PrinterSetup(QDialog):
    """打印机设置向导：检测 → 参数 → 校准，三页串行完成初次接入。"""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._store = PrinterConfigStore(ctx.data_dir / "printer_configs")
        self._printers = []
        self._current_caps = None
        self._calib_host = None
        self._calib_dlg = None

        self.setWindowTitle("打印机设置向导")
        self.setMinimumSize(620, 560)
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        self.tab_detect = self._build_detect_page(tabs)
        self.tab_params = self._build_params_page(tabs)
        self.tab_calib = self._build_calib_page(tabs)
        layout.addWidget(tabs)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        layout.addLayout(row)

        self.refresh_printers()

    # ---------------- 检测页 ----------------
    def _build_detect_page(self, tabs: QTabWidget) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)

        row = QHBoxLayout()
        self.detect_combo = QComboBox()
        self.detect_combo.currentIndexChanged.connect(self._on_printer_changed)
        row.addWidget(self.detect_combo, 1)
        btn_refresh = QPushButton("刷新打印机")
        btn_refresh.clicked.connect(self.refresh_printers)
        row.addWidget(btn_refresh)
        v.addLayout(row)

        self.detect_status = MutedLabel("尚未枚举打印机")
        v.addWidget(self.detect_status)
        self.detect_driver_state = MutedLabel("")
        v.addWidget(self.detect_driver_state)
        self.empty_hint = MutedLabel(
            "未检测到打印机：本向导需 Windows + 打印服务；请连接设备、"
            "从厂商官方渠道安装驱动后点击“刷新打印机”。")
        v.addWidget(self.empty_hint)

        self.btn_guidance = QPushButton("驱动安装指引")
        self.btn_guidance.clicked.connect(self.open_driver_guidance)
        v.addWidget(self.btn_guidance)
        v.addStretch(1)

        tabs.addTab(page, "检测")
        return page

    # ---------------- 参数页 ----------------
    def _build_params_page(self, tabs: QTabWidget) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.p_paper = QComboBox()
        form.addRow("纸张:", self.p_paper)
        self.p_orientation = QComboBox()
        self.p_orientation.addItem("纵向 portrait", "portrait")
        self.p_orientation.addItem("横向 landscape", "landscape")
        form.addRow("方向:", self.p_orientation)
        self.p_copies = QSpinBox()
        self.p_copies.setRange(1, 999)
        self.p_copies.setValue(1)
        form.addRow("份数:", self.p_copies)
        self.p_quality = QComboBox()
        self.p_quality.addItem("草稿 draft", "draft")
        self.p_quality.addItem("标准 normal", "normal")
        self.p_quality.addItem("高清 high", "high")
        form.addRow("质量:", self.p_quality)
        self.p_offset_x = QDoubleSpinBox()
        self.p_offset_x.setRange(-500.0, 500.0)
        self.p_offset_x.setDecimals(2)
        self.p_offset_x.setSingleStep(0.1)
        self.p_offset_x.setSuffix(" mm")
        form.addRow("水平偏移:", self.p_offset_x)
        self.p_offset_y = QDoubleSpinBox()
        self.p_offset_y.setRange(-500.0, 500.0)
        self.p_offset_y.setDecimals(2)
        self.p_offset_y.setSingleStep(0.1)
        self.p_offset_y.setSuffix(" mm")
        form.addRow("垂直偏移:", self.p_offset_y)

        btn_save = PrimaryButton("保存参数到该打印机配置")
        btn_save.clicked.connect(self._save_params)
        form.addRow(btn_save)
        self.params_hint = MutedLabel("参数按打印机名独立保存，切换打印机自动加载。")
        form.addRow(self.params_hint)

        tabs.addTab(page, "参数")
        return page

    # ---------------- 校准页 ----------------
    def _build_calib_page(self, tabs: QTabWidget) -> QWidget:
        page = QWidget()
        self._calib_host = QVBoxLayout(page)
        self._calib_host.addWidget(MutedLabel(
            "请先在“检测”页选择打印机，再在此页渲染测试页并校准偏移。"))
        tabs.addTab(page, "校准")
        return page

    # ---------------- 枚举 ----------------
    def refresh_printers(self):
        """合并 win32print 与 Qt 两路枚举（后台线程，防阻塞）。"""
        def _do():
            enum = []
            try:
                from ..printing.windows_print import (
                    list_printers, windows_print_available)
                if windows_print_available():
                    enum.extend(list_printers())
            except PrintError:
                pass
            try:
                from ..printing.qt_print import (
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
        self._printers = list(printers or [])
        self.detect_combo.blockSignals(True)
        self.detect_combo.clear()
        if not self._printers:
            self.detect_combo.addItem(
                "未检测到打印机（需 Windows + 打印服务）", None)
            self._current_caps = None
        else:
            for caps in self._printers:
                label = caps.printer_name + ("（默认）" if caps.is_default else "")
                self.detect_combo.addItem(label, caps)
            idx = next((i for i, c in enumerate(self._printers)
                        if c.is_default), 0)
            self.detect_combo.setCurrentIndex(idx)
        self.detect_combo.blockSignals(False)
        self._on_printer_changed()

    def _on_printer_changed(self):
        caps = self.detect_combo.currentData()
        self._current_caps = caps
        has = caps is not None

        # 检测页
        if not has:
            self.detect_status.setText(
                "未检测到打印机（需 Windows + 打印服务）")
            self.detect_driver_state.setText("")
            self.empty_hint.show()
            self.btn_guidance.setEnabled(False)
            self._set_params_enabled(False)
            self._rebuild_calib(None)
            return

        self.empty_hint.hide()
        self.btn_guidance.setEnabled(True)
        summary = L.summarize_capabilities(caps)
        if not caps.verified_with_hardware:
            summary += "（未实测：未经真实设备验证，请先以测试页复核）"
        self.detect_status.setText(summary)
        state = detect_driver_state(
            caps.printer_name, has_queue=True,
            driver_name=caps.driver.driver_name if caps.driver else None,
            is_offline=bool(caps.status and caps.status.offline),
        )
        self.detect_driver_state.setText(install_guidance_for_state(state))

        self._populate_papers()
        self._load_params(caps.printer_name)
        self._set_params_enabled(True)
        self._rebuild_calib(caps)

    def _populate_papers(self):
        caps = self._current_caps
        self.p_paper.blockSignals(True)
        self.p_paper.clear()
        if caps and caps.supported_papers:
            for p in caps.supported_papers:
                self.p_paper.addItem(
                    f"{p.name} {p.width_mm:g}x{p.height_mm:g}mm", p)
        # 自定义回退（A4 尺寸）
        custom = L.pick_paper(caps, 210.0, 297.0)
        self.p_paper.addItem(
            f"{custom.name}（{L.paper_choice_note(caps, custom)}）", custom)
        self.p_paper.blockSignals(False)

    def _load_params(self, name: str):
        cfg = self._store.load(name)
        if cfg is None:
            return
        self.p_offset_x.setValue(cfg.offset_x_mm)
        self.p_offset_y.setValue(cfg.offset_y_mm)
        self.p_copies.setValue(cfg.default_copies)
        q_idx = self.p_quality.findData(cfg.quality.value)
        if q_idx >= 0:
            self.p_quality.setCurrentIndex(q_idx)
        if cfg.paper is not None:
            for i in range(self.p_paper.count()):
                p = self.p_paper.itemData(i)
                if hasattr(p, "width_mm") and \
                        abs(p.width_mm - cfg.paper.width_mm) < 0.5 and \
                        abs(p.height_mm - cfg.paper.height_mm) < 0.5:
                    self.p_paper.setCurrentIndex(i)
                    break

    def _set_params_enabled(self, enabled: bool):
        for w in (self.p_paper, self.p_orientation, self.p_copies,
                  self.p_quality, self.p_offset_x, self.p_offset_y):
            w.setEnabled(enabled)

    def _save_params(self):
        caps = self._current_caps
        if caps is None:
            warn(self, "无可用打印机", "请先在检测页选择打印机")
            return
        cfg = self._store.load(caps.printer_name) or \
            self._store.defaults(caps.printer_name)
        cfg.paper = self.p_paper.currentData()
        cfg.offset_x_mm = self.p_offset_x.value()
        cfg.offset_y_mm = self.p_offset_y.value()
        cfg.default_copies = self.p_copies.value()
        from ..printing.capabilities import PrintQuality
        try:
            cfg.quality = PrintQuality(self.p_quality.currentData())
        except ValueError:
            cfg.quality = PrintQuality.NORMAL
        self._store.save(cfg)
        info(self, "参数已保存",
             f"已按打印机「{caps.printer_name}」独立保存参数。")

    def _rebuild_calib(self, caps):
        # 清空旧校准页内容
        while self._calib_host.count():
            item = self._calib_host.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._calib_dlg = None
        if caps is None:
            self._calib_host.addWidget(MutedLabel(
                "请先在“检测”页选择打印机，再在此页渲染测试页并校准偏移。"))
            return
        paper = self.p_paper.currentData()
        self._calib_dlg = CalibrationDialog(
            caps, self._store, paper=paper, parent=self)
        # 嵌入标签页（不 exec 模态）
        self._calib_host.addWidget(self._calib_dlg)

    # ---------------- 对话框入口 ----------------
    def open_driver_guidance(self):
        if self._current_caps is None:
            warn(self, "无可用打印机", "请先选择打印机")
            return
        DriverGuidanceDialog(self._current_caps, self).exec()
