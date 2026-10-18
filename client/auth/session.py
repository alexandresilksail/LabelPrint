"""客户端登录会话存储。

- 令牌存本地 JSON（权限 600），不存入日志/数据库业务表；
- 支持多服务端地址记忆（默认官方地址可配置）；
- 登出即删除本地令牌文件。
"""

import json
import os
import stat
from pathlib import Path

DEFAULT_SERVER_URL = "http://127.0.0.1:8000"


class SessionStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(self, session: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(session, ensure_ascii=False, indent=2),
            encoding="utf-8")
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()

    @property
    def token(self) -> str:
        return self.load().get("access_token", "")

    @property
    def server_url(self) -> str:
        return self.load().get("server_url", DEFAULT_SERVER_URL)

    @property
    def user(self) -> dict:
        return self.load().get("user", {})
