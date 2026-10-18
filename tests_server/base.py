"""服务端集成测试基础设施。

要点：
- 必须在 import server.* 之前设置 CLS_DATABASE_URL（引擎在导入时创建）；
- 每个测试独立临时 SQLite 库（drop_all + create_all 隔离）；
- 邮件后端替换为内存捕获（console 后端只写日志，测试需要读取令牌）；
- 限流器进程内全局，setUp 重置（测试不受默认配额干扰）；
- 授权私钥通过环境变量注入固定值（避免落盘 data/license_private.pem）。
"""

import atexit
import os
import re
import shutil
import tempfile
import unittest
import unittest.mock

_TMP = tempfile.mkdtemp(prefix="cls_server_test_")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ["CLS_ENV"] = "development"
os.environ["CLS_DATABASE_URL"] = f"sqlite:///{_TMP}/test.sqlite3"
os.environ["CLS_SECRET_KEY"] = "test-secret-key-not-for-production"
# 固定测试用 Ed25519 私钥（32 字节 hex），避免落盘真实密钥文件
os.environ["CLS_LICENSE_PRIVATE_KEY"] = bytes(range(32)).hex()

from fastapi.testclient import TestClient  # noqa: E402

from server.app import mailer as mailer_module  # noqa: E402
from server.app.database import Base, engine  # noqa: E402
from server.app.models import User  # noqa: E402
from server.app.security import hash_password, rate_limiter  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.main import app  # noqa: E402


class MailRecorder:
    """捕获 send_mail 调用，便于测试提取验证/重置令牌。"""

    def __init__(self):
        self.sent = []

    def __call__(self, to_address, subject, body_text, body_html=None):
        self.sent.append({
            "to": to_address, "subject": subject, "body": body_text,
        })
        return True

    def token_for(self, kind: str) -> str:
        """从最近一封指定类型邮件中提取令牌。"""
        pattern = rf"/{kind}\?token=([A-Za-z0-9_\-]+)"
        for mail in reversed(self.sent):
            m = re.search(pattern, mail["body"])
            if m:
                return m.group(1)
        raise AssertionError(f"未找到类型为 {kind} 的邮件令牌")

    def clear(self):
        self.sent.clear()


class ServerTestCase(unittest.TestCase):
    """每个测试：全新数据库 + 重置限流 + 邮件捕获。"""

    def setUp(self):
        rate_limiter.reset()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        self.mailer = MailRecorder()
        self._mailer_patch = unittest.mock.patch.object(
            mailer_module, "send_mail", self.mailer)
        self._mailer_patch.start()
        self.addCleanup(self._mailer_patch.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    # ---------- 常用流程助手 ----------
    def register(self, email="u1@example.com", username="user1",
                 password="Passw0rd!", accept_terms=True):
        return self.client.post("/api/auth/register", json={
            "email": email, "username": username,
            "password": password, "accept_terms": accept_terms,
        })

    def register_verified(self, email="u1@example.com", username="user1",
                          password="Passw0rd!"):
        """注册 + 邮箱验证 + 登录，返回 (user_dict, token)。"""
        r = self.register(email=email, username=username, password=password)
        self.assertEqual(r.status_code, 201, r.text)
        token = self.mailer.token_for("verify-email")
        r = self.client.post("/api/auth/verify-email",
                             json={"token": token})
        self.assertEqual(r.status_code, 200, r.text)
        return self.login(username, password)

    def login(self, identifier, password="Passw0rd!"):
        r = self.client.post("/api/auth/login", json={
            "identifier": identifier, "password": password,
        })
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["user"], r.json()["access_token"]

    @staticmethod
    def auth(token) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def create_admin(self, username="admin", email="admin@example.com",
                     password="AdminPassw0rd!"):
        """直接在库内创建已激活管理员并登录。"""
        with SessionLocal() as db:
            user = User(username=username, email=email,
                        password_hash=hash_password(password),
                        account_status="active", is_admin=True)
            db.add(user)
            db.commit()
        return self.login(username, password)

    def seed_plans(self):
        from server.seed import seed_plans
        with SessionLocal() as db:
            seed_plans(db)
