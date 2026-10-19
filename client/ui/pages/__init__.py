"""UI 页面集合。

页面模块依赖 PySide6；未安装 Qt 的环境（如 Linux CI 跑纯逻辑单测）下
逐个容错导入，保证 ``client.ui.pages`` 包本身可被导入，纯逻辑模块
（print_export_logic）不被牵连。
"""

try:
    from .account import AccountPage
except ImportError:  # pragma: no cover - 无 PySide6 环境
    AccountPage = None
try:
    from .dashboard import DashboardPage
except ImportError:  # pragma: no cover
    DashboardPage = None
try:
    from .generate import GeneratePage
except ImportError:  # pragma: no cover
    GeneratePage = None
try:
    from .print_export import PrintPage
except ImportError:  # pragma: no cover
    PrintPage = None
try:
    from .records import RecordsPage
except ImportError:  # pragma: no cover
    RecordsPage = None
try:
    from .settings import SettingsPage
except ImportError:  # pragma: no cover
    SettingsPage = None
try:
    from .templates import TemplatesPage
except ImportError:  # pragma: no cover
    TemplatesPage = None

__all__ = [
    "AccountPage", "DashboardPage", "GeneratePage", "PrintPage",
    "RecordsPage", "SettingsPage", "TemplatesPage",
]
