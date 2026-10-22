"""通用 UI 组件：卡片、统计块、条码绘制、后台任务、对话框助手。"""

import inspect
import os
import sys
import threading
import weakref

from PySide6.QtCore import QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ..barcode.encoder import encode_with_quiet_zone
from .theme import PRIMARY, TEXT, TEXT_MUTED

# 测试静音开关：置 True 时所有对话框不弹窗，仅记录到 DIALOG_LOG
# （离屏 / 自动化测试环境避免模态框阻塞）。
SILENT_DIALOGS = False
DIALOG_LOG = []


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
    if SILENT_DIALOGS:
        DIALOG_LOG.append(("info", title, text))
        return
    QMessageBox.information(parent, title, text)


def warn(parent, title, text):
    if SILENT_DIALOGS:
        DIALOG_LOG.append(("warn", title, text))
        return
    QMessageBox.warning(parent, title, text)


def error(parent, title, text):
    if SILENT_DIALOGS:
        DIALOG_LOG.append(("error", title, text))
        return
    QMessageBox.critical(parent, title, text)


def confirm(parent, title, text) -> bool:
    if SILENT_DIALOGS:
        DIALOG_LOG.append(("confirm", title, text))
        return True
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


def notify_main(page, method_name):
    """跨页面刷新助手：main_window 缺失（测试环境）时安全跳过。"""
    main = getattr(page, "main", None)
    fn = getattr(main, method_name, None) if main is not None else None
    if fn:
        fn()


class PrimaryButton(QPushButton):
    """主操作按钮。"""

    def __init__(self, text):
        super().__init__(text)
        self.setObjectName("Primary")



# ---------------------------------------------------------------------------
# RunLater 后台任务线程生命周期管理
#
# 历史问题：裸 QThread 启动后进入 exec() 事件循环，fn 执行完线程并不退出，
# 必须等主线程把排队的 done/failed 信号派发出去、再由槽调用 quit()。当主线程
# 事件循环不再被泵动（自动化测试末尾、应用退出前）时，quit() 永远不会被调用，
# 线程停留在 running 状态；解释器/QApplication 析构时 Qt 强制删除仍在运行的
# QThread，触发 “QThread: Destroyed while thread '' is still running” 并
# Aborted/Segfault（退出码 134/139）。即便 fn 已完成，未被释放的 QThread
# 对象连同排队信号进入 C++ 析构也会造成 use-after-free。
#
# 修复：
# 1. 重写 QThread.run()：fn 执行完毕（成功/异常）即发信号并返回，线程自然
#    finished，不进入 exec() 空转循环，从根上消除“fn 完了线程还活着”。
# 2. 模块级注册表（锁保护）持有每条任务：线程强引用 + 父控件弱引用 + 回调，
#    避免临时 RunLater 对象被 GC 后线程无人回收、重复调用造成线程泄漏。
# 3. 同时绑定 QApplication.aboutToQuit 与解释器 atexit：对所有活跃线程
#    requestInterruption + quit + wait(限时)，超时仅告警，不阻塞、不崩溃；
#    回收时断开信号，杜绝退出后迟到的排队回调触达已析构对象。
# 4. 回调经信号自动切回主线程；父控件已销毁（弱引用失效）或已进入退出流程
#    则不再回调，避免“信号断连顺序/触达已析构控件”。
# 5. fn 异常在 run() 的 try/finally 语义下保证发出 failed 并结束线程；回调
#    自身异常被捕获并记录，不影响线程收尾。
# ---------------------------------------------------------------------------


import atexit as _atexit

_ACTIVE_LOCK = threading.RLock()
# 尚未派发完成回调的任务（运行中 / 排队待回调）。
_ACTIVE_TASKS = set()
# 全部任务的强引用留存：进程退出 join 之前绝不丢弃线程引用，
# 避免线程被 GC 后无法 wait()，导致 QThread 在仍运行/未 join 时被析构崩溃。
_ALL_TASKS = []
_HOOKED_APPS = set()
_SHUTTING_DOWN = False


class _TaskThread(QThread):
    """执行单个后台任务的线程：重写 run()，fn 返回后线程自然结束。"""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, label="", parent=None):
        super().__init__(parent)
        self._fn = fn
        self.label = label

    def run(self):
        # 不调用 exec()：fn 执行完即返回，线程进入 finished，不留在事件循环。
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result)


class _TaskEntry:
    """注册表中的单条任务记录（线程强引用 + 父控件弱引用 + 回调）。"""

    __slots__ = ("thread", "parent_ref", "on_done", "on_error",
                 "label", "cancel_ref", "released", "cancelled")

    def __init__(self, thread, parent_ref, on_done, on_error, label):
        self.thread = thread
        self.parent_ref = parent_ref
        self.on_done = on_done
        self.on_error = on_error
        self.label = label
        self.cancel_ref = None
        self.released = False
        self.cancelled = False


def _describe_caller(fn):
    """生成任务诊断标签：fn 限定名 + 仓库内首个非 widgets.py 调用位置。"""
    qual = getattr(fn, "__qualname__", None) or getattr(
        fn, "__name__", None) or repr(fn)
    frame = inspect.currentframe()
    try:
        cur = frame.f_back  # 跳过本函数
        while cur is not None:
            fname = cur.f_code.co_filename or ""
            if os.path.basename(fname) != "widgets.py":
                rel = os.path.relpath(fname) if fname else "?"
                return f"{qual} @ {rel}:{cur.f_lineno} in {cur.f_code.co_name}"
            cur = cur.f_back
    finally:
        del frame
    return qual


def _parent_alive(entry):
    ref = entry.parent_ref
    return ref is None or ref() is not None


def _report_callback_error(label):
    import traceback
    sys.stderr.write(f"[RunLater] 回调处理异常（任务 {label}）:\n")
    traceback.print_exc()


def _disconnect(entry):
    """断开任务信号，防止退出后迟到的排队回调被派发。"""
    import warnings
    thread = entry.thread
    for sig in (thread.succeeded, thread.failed, thread.finished):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                sig.disconnect()
        except (RuntimeError, TypeError):
            pass


def _join(entry, timeout_ms=2000):
    """等待线程结束（join）；已结束的线程 wait 立即返回以回收原生资源。"""
    thread = entry.thread
    try:
        if thread.isRunning():
            thread.wait(timeout_ms)
        else:
            thread.wait(0)
    except RuntimeError:
        pass


def _release(entry):
    """回调完成后：移出活跃集合、断开信号并 join（幂等）。

    注意：不从 _ALL_TASKS 删除强引用，线程留存到进程退出统一回收。
    """
    with _ACTIVE_LOCK:
        if entry.released:
            return
        entry.released = True
        _ACTIVE_TASKS.discard(entry)
    _disconnect(entry)
    _join(entry)


def _cancel_entry(entry):
    """父控件销毁或页面关闭时：请求中断、退出、join 并释放该任务。"""
    entry.cancelled = True
    thread = entry.thread
    try:
        if thread.isRunning():
            thread.requestInterruption()
            thread.quit()
            thread.wait(500)
    except RuntimeError:
        pass
    _release(entry)


def _ensure_quit_hook(app):
    if app is None or app in _HOOKED_APPS:
        return
    _HOOKED_APPS.add(app)
    app.aboutToQuit.connect(shutdown_all)


def shutdown_all(timeout_ms=2000):
    """退出前回收全部后台线程（aboutToQuit / atexit / 测试拆卸调用）。"""
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    with _ACTIVE_LOCK:
        snapshot = list(_ALL_TASKS)
        _ACTIVE_TASKS.clear()
    for entry in snapshot:
        entry.cancelled = True
        thread = entry.thread
        try:
            if thread.isRunning():
                thread.requestInterruption()
                thread.quit()
                if not thread.wait(timeout_ms):
                    sys.stderr.write(
                        f"[RunLater] 后台任务退出超时（任务 {entry.label}），"
                        f"继续进程退出而不阻塞\n")
            else:
                thread.wait(0)
        except RuntimeError:
            pass
        _disconnect(entry)
    with _ACTIVE_LOCK:
        _ALL_TASKS.clear()


# 解释器退出兜底：即便没有 QApplication.aboutToQuit（自动化测试直接结束），
# 也在 C++ 对象析构前 join 全部线程，杜绝“QThread 仍在运行即被销毁”。
_atexit.register(shutdown_all)


class RunLater:
    """把耗时调用推到独立线程执行，完成后回主线程回调。

    用法：
        RunLater(self).run(fn, on_done=..., on_error=...)
    fn 在子线程执行（禁止触碰 UI）；on_done(result)/on_error(msg)
    在主线程执行（可安全更新 UI）。

    生命周期：线程不挂到页面对象下（避免页面销毁时 Qt 强制删除仍在运行的
    线程导致崩溃）；任务进入模块级注册表统一登记，回调完成后断开信号并 join；
    QApplication 退出与解释器退出时统一 join 全部残留线程。
    """

    def __init__(self, parent):
        self._parent = parent

    def run(self, fn, on_done=None, on_error=None, on_progress=None):
        parent = self._parent
        label = _describe_caller(fn)
        thread = _TaskThread(fn, label=label)  # 无父对象：显式管理生命周期
        parent_ref = weakref.ref(parent) if parent is not None else None
        entry = _TaskEntry(thread, parent_ref, on_done, on_error, label)

        def _on_succeeded(result):
            _release(entry)
            if _SHUTTING_DOWN or entry.cancelled or not _parent_alive(entry):
                return
            cb = entry.on_done
            if cb is None:
                return
            try:
                cb(result)
            except Exception:  # noqa: BLE001
                _report_callback_error(label)

        def _on_failed(msg):
            _release(entry)
            if _SHUTTING_DOWN or entry.cancelled:
                return
            cb = entry.on_error
            live_parent = parent_ref() if parent_ref is not None else None
            try:
                if cb is not None:
                    if _parent_alive(entry):
                        cb(msg)
                elif live_parent is not None:
                    error(live_parent, "操作失败", msg)
            except Exception:  # noqa: BLE001
                _report_callback_error(label)

        thread.succeeded.connect(_on_succeeded)
        thread.failed.connect(_on_failed)
        thread.finished.connect(lambda: _release(entry))

        # 父控件被销毁时主动停掉该任务，避免回调触达已析构对象。
        if parent_ref is not None:
            entry.cancel_ref = weakref.ref(
                parent, lambda _ref, e=entry: _cancel_entry(e))

        with _ACTIVE_LOCK:
            _ACTIVE_TASKS.add(entry)
            _ALL_TASKS.append(entry)

        # 延迟绑定退出回收（无 QApplication 的纯脚本环境也安全）。
        try:
            from PySide6.QtWidgets import QApplication
            _ensure_quit_hook(QApplication.instance())
        except Exception:  # noqa: BLE001
            pass

        thread.start()
        return thread

    def shutdown(self, timeout_ms=2000):
        """等待本页发起的全部后台线程结束（页面关闭/测试拆卸时调用）。"""
        parent = self._parent
        with _ACTIVE_LOCK:
            snapshot = [
                e for e in _ACTIVE_TASKS
                if e.parent_ref is not None and e.parent_ref() is parent
            ]
        for entry in snapshot:
            entry.cancelled = True
            thread = entry.thread
            try:
                if thread.isRunning():
                    thread.requestInterruption()
                    thread.quit()
                    thread.wait(timeout_ms)
            except RuntimeError:
                pass
            _release(entry)
