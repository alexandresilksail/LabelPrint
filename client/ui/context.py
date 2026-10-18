"""应用上下文：数据库、业务服务、会话与授权缓存的装配。

UI 各页面只依赖 AppContext，不自行创建连接，保证：
- 单数据库实例（线程本地连接 + 代次机制）；
- 备份/恢复后服务自动跟随新连接（context.reload()）。
"""

import sys
from pathlib import Path

from ..auth.session import SessionStore
from ..barcode.service import BarcodeService
from ..database.db import Database
from ..licensing.client_license import LicenseStore
from ..printing.template_service import TemplateService


def default_data_dir() -> Path:
    """数据目录优先级：
    1. exe 同级 data_dir.txt（安装版由 Inno Setup 写入，允许用户自定义位置）；
    2. 可执行文件同级 data/（打包默认）；
    3. 仓库内 data/（开发环境）。
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        marker = exe_dir / "data_dir.txt"
        try:
            if marker.exists():
                configured = marker.read_text(encoding="utf-8").strip()
                if configured:
                    return Path(configured)
        except OSError:
            pass
        return exe_dir / "data"
    return Path(__file__).resolve().parents[2] / "data"


class AppContext:
    def __init__(self, data_dir=None):
        root = Path(data_dir) if data_dir else default_data_dir()
        root.mkdir(parents=True, exist_ok=True)
        self.data_dir = root
        self.db = Database(root / "codelabel.db")
        self.service = BarcodeService(self.db)
        self.templates = TemplateService(self.db)
        self.session = SessionStore(root / "session.json")
        self.license_store = LicenseStore(root / "license.json")

    def reload_after_restore(self):
        """备份恢复后重建服务绑定（Database 内部连接已按代次失效）。"""
        self.service = BarcodeService(self.db)
        self.templates = TemplateService(self.db)

    # ---------- 备份恢复 ----------
    def backup(self, dest):
        return self.db.backup(dest)

    def restore(self, src):
        self.db.restore(src)
        self.reload_after_restore()
