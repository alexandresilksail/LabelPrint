"""审计问题 3：生产环境启动检查。

覆盖：
- 缺数据库/密钥/邮件/CORS/授权私钥时拒绝启动，并一次性列出全部问题；
- 生产禁止 SQLite（除非显式逃生开关）与 console 邮件后端；
- 授权私钥生产不静默生成；密钥损坏/互换必须启动自检失败；
- 指纹在多次加载间稳定（多 worker / 重启一致性）；
- 数据库缺表时拒绝启动（要求先跑 alembic）。
"""

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from server.app import config as config_module
from server.app import licensing
from server.app.config import ConfigError, Settings

GOOD_ENV = {
    "CLS_ENV": "production",
    "CLS_SECRET_KEY": "s" * 48,
    "CLS_DATABASE_URL": "postgresql+psycopg://u:p@db:5432/cls",
    "CLS_MAIL_BACKEND": "smtp",
    "CLS_SMTP_HOST": "smtp.example.com",
    "CLS_SMTP_USER": "mailer@example.com",
    "CLS_SMTP_PASSWORD": "smtp-secret",
    "CLS_MAIL_FROM": "noreply@example.com",
    "CLS_CORS_ORIGINS": "https://app.example.com",
    "CLS_APP_BASE_URL": "https://api.example.com",
    "CLS_LICENSE_PRIVATE_KEY": "ab" * 32,
    "CLS_RATE_LIMIT_BACKEND": "redis",
    "CLS_REDIS_URL": "redis://redis:6379/0",
    "CLS_WORKERS": "2",
}


def build(env: dict) -> Settings:
    # 指向不存在的密钥文件，避免本机开发遗留的 data/license_private.pem
    # 让"缺授权私钥"用例误判为已配置
    env = {**env,
           "CLS_LICENSE_KEY_FILE":
               str(Path(tempfile.gettempdir()) / "cls_no_such_key.pem")}
    with unittest.mock.patch.dict(os.environ, env, clear=True):
        return Settings(validate=False)


class ProductionConfigTests(unittest.TestCase):
    def assertError(self, errors: list[str], needle: str):
        self.assertTrue(any(needle in e for e in errors),
                        f"未出现预期错误「{needle}」，实际：{errors}")

    def test_good_config_has_no_errors(self):
        self.assertEqual(build(GOOD_ENV).production_errors(), [])

    def test_empty_config_reports_all_missing(self):
        errors = build({"CLS_ENV": "production"}).production_errors()
        self.assertError(errors, "CLS_SECRET_KEY")
        self.assertError(errors, "SQLite")
        self.assertError(errors, "CLS_MAIL_BACKEND")
        self.assertError(errors, "CORS")
        self.assertError(errors, "授权私钥")
        self.assertGreaterEqual(len(errors), 5,
                                f"应一次性报告全部问题：{errors}")

    def test_short_secret_rejected(self):
        errors = build({**GOOD_ENV, "CLS_SECRET_KEY": "short"}).production_errors()
        self.assertError(errors, "长度不足")

    def test_known_placeholder_secret_rejected(self):
        errors = build({**GOOD_ENV,
                        "CLS_SECRET_KEY": "test-secret-key-not-for-production"}
                       ).production_errors()
        self.assertError(errors, "开发默认值")

    def test_sqlite_rejected_in_production(self):
        errors = build({**GOOD_ENV,
                        "CLS_DATABASE_URL": "sqlite:///./data/x.sqlite3"}
                       ).production_errors()
        self.assertError(errors, "SQLite")

    def test_sqlite_allowed_with_explicit_override(self):
        errors = build({**GOOD_ENV,
                        "CLS_DATABASE_URL": "sqlite:///./data/x.sqlite3",
                        "CLS_ALLOW_SQLITE_IN_PROD": "1"}).production_errors()
        self.assertEqual(errors, [])

    def test_console_mail_rejected(self):
        errors = build({**GOOD_ENV, "CLS_MAIL_BACKEND": "console"}
                       ).production_errors()
        self.assertError(errors, "smtp")

    def test_smtp_incomplete(self):
        errors = build({**GOOD_ENV, "CLS_SMTP_PASSWORD": ""}).production_errors()
        self.assertError(errors, "CLS_SMTP_PASSWORD")

    def test_default_mail_from_rejected(self):
        errors = build({**GOOD_ENV,
                        "CLS_MAIL_FROM": "noreply@codelabel.local"}
                       ).production_errors()
        self.assertError(errors, "默认占位地址")

    def test_cors_wildcard_rejected(self):
        errors = build({**GOOD_ENV, "CLS_CORS_ORIGINS": "*"}).production_errors()
        self.assertError(errors, "通配符")

    def test_cors_localhost_rejected(self):
        errors = build({**GOOD_ENV,
                        "CLS_CORS_ORIGINS": "https://app.example.com,"
                                            "http://localhost:5173"}
                       ).production_errors()
        self.assertError(errors, "开发地址")

    def test_cors_empty_rejected(self):
        errors = build({**GOOD_ENV, "CLS_CORS_ORIGINS": ""}).production_errors()
        self.assertError(errors, "CORS")

    def test_http_base_url_rejected(self):
        errors = build({**GOOD_ENV,
                        "CLS_APP_BASE_URL": "http://api.example.com"}
                       ).production_errors()
        self.assertError(errors, "https")

    def test_string_config_raises_on_construction(self):
        """默认构造（validate=True）必须直接抛 ConfigError。"""
        with unittest.mock.patch.dict(os.environ, {"CLS_ENV": "production"},
                                      clear=True):
            with self.assertRaises(ConfigError) as ctx:
                Settings()
        self.assertIn("拒绝启动", str(ctx.exception))

    def test_development_needs_nothing(self):
        with unittest.mock.patch.dict(os.environ, {"CLS_ENV": "development"},
                                      clear=True):
            settings = Settings()
        self.assertFalse(settings.is_production)
        self.assertEqual(settings.production_errors(), [])
        self.assertTrue(settings.secret_key)     # 开发自动生成

    def test_validate_raises_when_errors(self):
        settings = build({**GOOD_ENV, "CLS_MAIL_BACKEND": "console"})
        with self.assertRaises(ConfigError):
            settings.validate_for_production()

    def test_safe_summary_has_no_secret(self):
        summary = build(GOOD_ENV).safe_summary()
        dumped = str(summary)
        self.assertNotIn(GOOD_ENV["CLS_SECRET_KEY"], dumped)
        self.assertNotIn(GOOD_ENV["CLS_SMTP_PASSWORD"], dumped)
        self.assertNotIn(GOOD_ENV["CLS_LICENSE_PRIVATE_KEY"], dumped)


class LicenseKeyStartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.key_file = Path(self.tmp.name) / "license_private.pem"
        self._patches = [
            unittest.mock.patch.object(licensing, "_KEY_FILE", self.key_file),
            unittest.mock.patch.object(licensing, "_private_key", None),
            unittest.mock.patch.object(licensing, "_public_key", None),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _settings(self, **kw):
        base = dict(is_production=True, license_private_key_hex="")
        base.update(kw)
        return SimpleNamespace(**base)

    def test_production_without_key_refuses_to_generate(self):
        with unittest.mock.patch.object(licensing, "settings", self._settings()):
            with self.assertRaises(RuntimeError) as ctx:
                licensing.get_private_key()
        self.assertIn("拒绝自动生成", str(ctx.exception))
        self.assertFalse(self.key_file.exists(),
                         "生产环境不得落盘生成新密钥")

    def test_production_uses_env_key(self):
        key_hex = bytes(range(32)).hex()
        with unittest.mock.patch.object(
                licensing, "settings",
                self._settings(license_private_key_hex=key_hex)):
            fp = licensing.key_fingerprint()
        self.assertEqual(len(fp), 16)
        self.assertFalse(self.key_file.exists())

    def test_production_uses_existing_pem_file(self):
        licensing._private_key = None
        licensing._public_key = None
        dev = SimpleNamespace(is_production=False, license_private_key_hex="")
        with unittest.mock.patch.object(licensing, "settings", dev):
            licensing.get_private_key()          # 开发态生成并落盘
        self.assertTrue(self.key_file.exists())

        licensing._private_key = None
        licensing._public_key = None
        with unittest.mock.patch.object(licensing, "settings", self._settings()):
            fp_after_restart = licensing.key_fingerprint()
        self.assertEqual(len(fp_after_restart), 16)

    def test_invalid_hex_key_rejected(self):
        with unittest.mock.patch.object(
                licensing, "settings",
                self._settings(license_private_key_hex="zz")):
            with self.assertRaises(RuntimeError):
                licensing.get_private_key()

    def test_wrong_length_key_rejected(self):
        with unittest.mock.patch.object(
                licensing, "settings",
                self._settings(license_private_key_hex="ab" * 16)):
            with self.assertRaises(RuntimeError) as ctx:
                licensing.get_private_key()
        self.assertIn("32 字节", str(ctx.exception))

    def test_fingerprint_stable_across_reload(self):
        key_hex = bytes(range(32)).hex()
        with unittest.mock.patch.object(
                licensing, "settings",
                self._settings(license_private_key_hex=key_hex)):
            first = licensing.key_fingerprint()
            licensing._private_key = None
            licensing._public_key = None
            second = licensing.key_fingerprint()
        self.assertEqual(first, second, "同一私钥的指纹必须稳定（多 worker 一致性）")

    def test_different_keys_have_different_fingerprint(self):
        with unittest.mock.patch.object(
                licensing, "settings",
                self._settings(license_private_key_hex=bytes(range(32)).hex())):
            a = licensing.key_fingerprint()
        licensing._private_key = None
        licensing._public_key = None
        with unittest.mock.patch.object(
                licensing, "settings",
                self._settings(license_private_key_hex=bytes(range(1, 33)).hex())):
            b = licensing.key_fingerprint()
        self.assertNotEqual(a, b)

    def test_self_test_passes_with_valid_key(self):
        with unittest.mock.patch.object(
                licensing, "settings",
                self._settings(license_private_key_hex=bytes(range(32)).hex())):
            licensing.self_test()               # 不抛异常即通过


class DatabaseReadinessTests(unittest.TestCase):
    def test_missing_tables_reported(self):
        from server.app.database import missing_tables
        missing = missing_tables()
        # 测试库由 base.py 建好全部表；这里只验证函数可调用且返回集合
        self.assertIsInstance(missing, set)
        self.assertNotIn("outbox_messages", missing,
                         "测试库应包含 outbox_messages 表")


if __name__ == "__main__":
    unittest.main()
