"""PySide6 界面层：主题、通用组件、页面与主窗口。

注意：子模块按需导入（`from client.ui.main_window import MainWindow`），
避免无 GUI 环境（如服务端测试）被迫安装 PySide6。
"""

__all__ = ["main_window", "context", "theme", "widgets"]
