"""应用入口：QApplication 装配、窗口启动、全局异常兜底。"""

import sys
import traceback


def _excepthook(exc_type, exc_value, exc_tb):
    """未捕获异常：弹框告知而非静默崩溃（保留控制台输出便于排查）。"""
    traceback.print_exception(exc_type, exc_value, exc_tb)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance():
            QMessageBox.critical(
                None, "发生错误",
                "程序遇到未预期的错误：\n\n"
                f"{exc_value}\n\n详细日志已输出到控制台。")
    except Exception:  # nosec B110 - 对话框本身失败时只能放弃（已打印到 stderr）
        pass


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    sys.excepthook = _excepthook

    from PySide6.QtWidgets import QApplication

    from ..ui.context import AppContext
    from ..ui.main_window import MainWindow

    app = QApplication(argv)
    app.setApplicationName("CodeLabelStudio")
    app.setOrganizationName("CodeLabelStudio")

    window = MainWindow(AppContext())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
