"""UI 页面集合。"""

from .account import AccountPage
from .dashboard import DashboardPage
from .generate import GeneratePage
from .print_export import PrintPage
from .records import RecordsPage
from .settings import SettingsPage
from .templates import TemplatesPage

__all__ = [
    "AccountPage", "DashboardPage", "GeneratePage", "PrintPage",
    "RecordsPage", "SettingsPage", "TemplatesPage",
]
