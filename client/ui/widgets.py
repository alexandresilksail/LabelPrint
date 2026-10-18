"""通用 UI 组件：卡片、统计块、条码绘制、后台任务、对话框助手。"""

from PySide6.QtCore import QObject, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ..barcode.encoder import encode_with_quiet_zone
from .theme import PRIMARY, TEXT, TEXT_MUTED


class Card(QFrame):
    """圆角白底卡片容器。"""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        if title:
            header = QLabel(title)
            header.setObjectName("Title")
            layout.addWidget(header)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        layout.addLayout(self.body)


class StatCard(QFrame):
    """统计数字卡片。"""

    def __init__(self, label, value="0", color=PRIMARY, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        self.value_label = QLabel(str(value))
        self.value_label.setObjectName("StatValue")
        self.value_label.setStyleSheet(f"color: {color};")
        caption = QLabel(label)
        caption.setObjectName("Subtitle")
        layout.addWidget(self.value_label)
        layout.addWidget(caption)

    def set_value(self, value):
        self.value_label.setText(str(value))


class BarcodeWidget(QWidget):
    """用 QPainter 直接绘制条码（模块矩形 + 静区 + 可读数字）。

    不依赖第三方渲染库；打印几何与 PDF/SVG 导出保持同一编码来源。
    """

    def __init__(self, value="", barcode_type="EAN-13", parent=None,
                 show_text=True, module_px=3, height_px=120):
        super().__init__(parent)
        self.value = value
        self.barcode_type = barcode_type
        self.show_text = show_text
        self.module_px = module_px
        self.height_px = height_px
        self.setMinimumHeight(height_px + (28 if show_text else 8))

    def set_barcode(self, value, barcode_type):
        self.value = value
        self.barcode_type = barcode_type
        self.update()

    def paintEvent(self, event):
        if not self.value:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        try:
            modules = encode_with_quiet_zone(self.value, self.barcode_type)
        except Exception:
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter, "编码无效，无法绘制")
            painter.end()
            return
        total_w = len(modules) * self.module_px
        x0 = max(0, (self.width() - total_w) // 2)
        y0 = 4
        painter.fillRect(QRectF(x0, y0, total_w, self.height_px),
                         QColor("white"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("black"))
        x = x0
        for m in modules:
            if m == "1":
                painter.drawRect(QRectF(x, y0, self.module_px, self.height_px))
            x += self.module_px
        if self.show_text:
            painter.setPen(QColor(TEXT))
            font = QFont("Consolas", 9)
            font.setStyleStrategy(QFont.NoAntialias)
            painter.setFont(font)
            text_y = y0 + self.height_px + 18
            painter.drawText(QRectF(x0 - 10, text_y - 14,
                                    total_w + 20, 20),
                             Qt.AlignCenter, self.value)
        painter.end()


def info(parent, title, text):
    QMessageBox.information(parent, title, text)


def warn(parent, title, text):
    QMessageBox.warning(parent, title, text)


def error(parent, title, text):
    QMessageBox.critical(parent, title, text)


def confirm(parent, title, text) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    return box.exec() == QMessageBox.Yes


class MutedLabel(QLabel):
    """次要说明文字。"""

    def __init__(self, text=""):
        super().__init__(text)
        self.setStyleSheet(f"color: {TEXT_MUTED};")


class PrimaryButton(QPushButton):
    """主操作按钮。"""

    def __init__(self, text):
        super().__init__(text)
        self.setObjectName("Primary")


class _WorkerSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class RunLater:
    """把耗时调用推到独立线程执行，完成后回主线程回调。

    用法：
        RunLater(self).run(fn, on_done=..., on_error=...)
    fn 在子线程执行（禁止触碰 UI）；on_done(result)/on_error(msg)
    在主线程执行（可安全更新 UI）。
    """

    def __init__(self, parent):
        self._parent = parent
        self._threads = []

    def run(self, fn, on_done=None, on_error=None, on_progress=None):
        parent = self._parent
        thread = QThread(parent)
        signals = _WorkerSignals()
        self._threads.append(thread)

        def _work():
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                signals.failed.emit(str(exc))
                return
            signals.done.emit(result)

        def _finish():
            thread.quit()
            try:
                self._threads.remove(thread)
            except ValueError:
                pass

        signals.done.connect(lambda result: (
            on_done(result) if on_done else None))
        signals.failed.connect(lambda msg: (
            on_error(msg) if on_error else error(parent, "操作失败", msg)))
        signals.done.connect(_finish)
        signals.failed.connect(_finish)
        thread.started.connect(_work)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        return thread
