"""服务端配置：全部通过环境变量注入，禁止硬编码密钥。

生产与开发严格隔离：

- 开发环境保留零配置可运行（自动生成会话密钥、SQLite、console 邮件）；
- 生产环境缺任何必需项一律 **拒绝启动**，不允许"静默降级"到开发默认值；
- 限流器若不使用共享存储（Redis），则只允许单 worker，防止多进程
  各自计数导致限流形同虚设。
"""

import os
from pathlib import Path

from .netutil import parse_networks

# 授权私钥落盘位置（与 app/licensing.py 共用，避免两处不一致）
LICENSE_KEY_FILE = Path(__file__).resolve().parents[2] / "data" / "license_private.pem"

_DEV_SECRET_MARKERS = {
    "",
    "test-secret-key-not-for-production",
    "changeme",
    "secret",
    "dev",
}


class ConfigError(RuntimeError):
    """配置不满足运行要求（生产环境必须拒绝启动）。"""


class Settings:
    def __init__(self, *, validate: bool = True):
        self.env = os.environ.get("CLS_ENV", "development").strip().lower()

        # ---------- 基础 ----------
        self.database_url = os.environ.get(
            "CLS_DATABASE_URL",
            f"sqlite:///{Path(__file__).resolve().parents[2] / 'data' / 'server_dev.sqlite3'}",
        )
        # 生产显式允许 SQLite 的逃生开关（默认关闭；默认配置下生产禁用 SQLite）
        self.allow_sqlite_in_prod = _env_flag("CLS_ALLOW_SQLITE_IN_PROD", False)

        self.secret_key = os.environ.get("CLS_SECRET_KEY", "")
        if not self.secret_key and self.env != "production":
            # 开发环境自动生成（重启后会话失效，仅限开发）
            import secrets
            self.secret_key = secrets.token_hex(32)

        # ---------- 反向代理与限流 ----------
        # 逗号分隔的 IP / CIDR；为空表示不信任任何转发头
        self.trusted_proxies_raw = [
            item.strip() for item in
            os.environ.get("CLS_TRUSTED_PROXIES", "").split(",") if item.strip()
        ]
        # 是否部署在反向代理之后（生产要求显式声明，便于校验配置完整性）
        self.behind_proxy = _env_flag("CLS_BEHIND_PROXY", False)
        # 限流后端：memory（进程内，仅单 worker 安全）| redis（共享存储）
        self.rate_limit_backend = os.environ.get(
            "CLS_RATE_LIMIT_BACKEND", "memory").strip().lower()
        self.redis_url = os.environ.get("CLS_REDIS_URL", "")
        # Redis 不可用时是否放行（默认失败关闭，避免限流失效被当作正常）
        self.rate_limit_fail_open = _env_flag("CLS_RATE_LIMIT_FAIL_OPEN", False)
        # 进程数：多 worker + 内存限流 = 限流配额被成倍放大
        self.workers = int(os.environ.get("CLS_WORKERS", "1"))

        # ---------- 会话与令牌 ----------
        self.session_ttl_hours = int(os.environ.get("CLS_SESSION_TTL_HOURS", "72"))
        self.email_token_ttl_hours = int(os.environ.get("CLS_EMAIL_TOKEN_TTL_HOURS", "24"))
        self.reset_token_ttl_hours = int(os.environ.get("CLS_RESET_TOKEN_TTL_HOURS", "2"))

        # ---------- 速率限制（每分钟请求数） ----------
        self.rate_limit_register = int(os.environ.get("CLS_RL_REGISTER", "5"))
        self.rate_limit_login = int(os.environ.get("CLS_RL_LOGIN", "10"))
        self.rate_limit_email = int(os.environ.get("CLS_RL_EMAIL", "3"))
        self.rate_limit_default = int(os.environ.get("CLS_RL_DEFAULT", "120"))

        # ---------- 试用与授权 ----------
        self.trial_days = int(os.environ.get("CLS_TRIAL_DAYS", "7"))
        self.offline_grace_hours = int(os.environ.get("CLS_OFFLINE_GRACE_HOURS", "72"))
        self.max_devices = int(os.environ.get("CLS_MAX_DEVICES", "3"))

        # 授权签名私钥（hex）。生产必须显式提供（环境变量或已存在的 PEM 文件）
        self.license_private_key_hex = os.environ.get("CLS_LICENSE_PRIVATE_KEY", "")
        # PEM 文件位置可覆盖（容器挂载 secret 时常用非默认路径）
        self.license_key_file_override = os.environ.get("CLS_LICENSE_KEY_FILE", "").strip()

        # ---------- 邮件 ----------
        self.mail_backend = os.environ.get("CLS_MAIL_BACKEND", "console").strip().lower()
        self.smtp_host = os.environ.get("CLS_SMTP_HOST", "")
        self.smtp_port = int(os.environ.get("CLS_SMTP_PORT", "465"))
        self.smtp_user = os.environ.get("CLS_SMTP_USER", "")
        self.smtp_password = os.environ.get("CLS_SMTP_PASSWORD", "")
        self.mail_from = os.environ.get("CLS_MAIL_FROM", "noreply@codelabel.local")
        self.app_base_url = os.environ.get(
            "CLS_APP_BASE_URL", "https://codelabel.example.com"
        )

        # ---------- CORS 白名单（逗号分隔） ----------
        self.cors_origins = [
            o.strip() for o in os.environ.get(
                "CLS_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",") if o.strip()
        ]

        # ---------- 订阅计划（由服务端配置，客户端不得硬编码价格） ----------
        self.plans = [
            {
                "plan_code": "monthly",
                "plan_name": "月度订阅",
                "billing_period": "monthly",
                "price": "29.00",
                "currency": "CNY",
                "enabled": True,
            },
            {
                "plan_code": "yearly",
                "plan_name": "年度订阅",
                "billing_period": "yearly",
                "price": "199.00",
                "currency": "CNY",
                "enabled": True,
            },
        ]

        if self.is_production and validate:
            # 生产环境在导入即失败，绝不带着开发默认值启动服务。
            # validate=False 仅供测试构造"反面配置"以断言错误清单。
            errors = self.production_errors()
            if errors:
                raise ConfigError(_format_errors(errors))

    # ---------- 派生属性 ----------
    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def trusted_networks(self):
        """可信代理网段（解析一次后缓存，避免每请求重复解析）。"""
        cached = getattr(self, "_trusted_networks", None)
        if cached is None:
            cached = parse_networks(self.trusted_proxies_raw)
            self._trusted_networks = cached
        return cached

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def license_key_file(self) -> Path:
        if self.license_key_file_override:
            return Path(self.license_key_file_override)
        return LICENSE_KEY_FILE

    def has_license_key(self) -> bool:
        if self.license_private_key_hex.strip():
            return True
        return self.license_key_file.is_file()

    # ---------- 生产校验 ----------
    def production_errors(self) -> list[str]:
        """返回全部不满足生产要求的问题（一次性报告，便于运维修完）。"""
        if not self.is_production:
            return []
        errors: list[str] = []

        # 1) 会话签名密钥
        if self.secret_key.strip().lower() in _DEV_SECRET_MARKERS:
            errors.append("CLS_SECRET_KEY 未设置或使用了开发默认值")
        elif len(self.secret_key) < 32:
            errors.append("CLS_SECRET_KEY 长度不足 32 字符（熵不足）")

        # 2) 数据库
        if self.is_sqlite and not self.allow_sqlite_in_prod:
            errors.append(
                "CLS_DATABASE_URL 指向 SQLite（生产禁止开发数据库；"
                "确需使用时显式设置 CLS_ALLOW_SQLITE_IN_PROD=1 并接受单机限制）")

        # 3) 邮件
        if self.mail_backend != "smtp":
            errors.append(
                "CLS_MAIL_BACKEND 必须为 smtp（console 后端只写日志，"
                "生产会静默丢失验证/重置邮件）")
        else:
            missing = [
                name for name, value in (
                    ("CLS_SMTP_HOST", self.smtp_host),
                    ("CLS_SMTP_USER", self.smtp_user),
                    ("CLS_SMTP_PASSWORD", self.smtp_password),
                ) if not value.strip()
            ]
            if missing:
                errors.append("SMTP 配置不完整：" + "、".join(missing))
            if self.mail_from.strip().lower().endswith("@codelabel.local"):
                errors.append("CLS_MAIL_FROM 仍是默认占位地址")

        # 4) CORS
        if not self.cors_origins:
            errors.append("CLS_CORS_ORIGINS 为空：管理后台与客户端将无法跨域访问")
        elif "*" in self.cors_origins:
            errors.append("CLS_CORS_ORIGINS 含通配符 *，禁止与凭据同时使用")
        else:
            local = [o for o in self.cors_origins
                     if "localhost" in o or "127.0.0.1" in o]
            if local:
                errors.append(
                    "CLS_CORS_ORIGINS 仍含开发地址：" + "、".join(local))

        # 5) 对外基础地址
        if not self.app_base_url.startswith("https://"):
            errors.append("CLS_APP_BASE_URL 必须为 https（邮件链接与回调依赖）")

        # 6) 授权私钥：生产不允许自动生成
        if not self.has_license_key():
            errors.append(
                "未配置授权私钥（CLS_LICENSE_PRIVATE_KEY 或 "
                f"{self.license_key_file}）：生产禁止自动生成，"
                "否则重启即使用户授权全部失效")

        # 7) 限流后端
        if self.rate_limit_backend not in ("memory", "redis"):
            errors.append(
                f"CLS_RATE_LIMIT_BACKEND 非法：{self.rate_limit_backend}")
        if self.rate_limit_backend == "memory":
            if self.workers > 1:
                errors.append(
                    f"内存限流 + {self.workers} 个 worker：限流配额会被放大 "
                    f"{self.workers} 倍。请改用 CLS_RATE_LIMIT_BACKEND=redis "
                    "或设置 CLS_WORKERS=1")
        elif not self.redis_url.startswith(("redis://", "rediss://")):
            errors.append("CLS_RATE_LIMIT_BACKEND=redis 时必须提供 CLS_REDIS_URL")

        # 8) 反向代理
        if self.behind_proxy:
            if not self.trusted_proxies_raw:
                errors.append(
                    "CLS_BEHIND_PROXY=1 但 CLS_TRUSTED_PROXIES 为空："
                    "所有请求会被识别为代理地址，限流将把全部用户计入同一桶")
            try:
                parse_networks(self.trusted_proxies_raw)
            except ValueError as exc:
                errors.append(str(exc))
        return errors

    def validate_for_production(self) -> None:
        errors = self.production_errors()
        if errors:
            raise ConfigError(_format_errors(errors))

    def safe_summary(self) -> dict:
        """可安全写入日志/健康检查的配置摘要（不含任何密钥）。"""
        return {
            "env": self.env,
            "database": "sqlite" if self.is_sqlite else "server",
            "mail_backend": self.mail_backend,
            "rate_limit_backend": self.rate_limit_backend,
            "workers": self.workers,
            "behind_proxy": self.behind_proxy,
            "trusted_proxies": len(self.trusted_proxies_raw),
        }


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _format_errors(errors: list[str]) -> str:
    lines = "\n".join(f"  - {item}" for item in errors)
    return f"生产环境配置校验未通过（拒绝启动）：\n{lines}"


settings = Settings()
