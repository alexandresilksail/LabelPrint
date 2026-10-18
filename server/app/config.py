"""服务端配置：全部通过环境变量注入，禁止硬编码密钥。"""

import os
from pathlib import Path


class Settings:
    def __init__(self):
        self.env = os.environ.get("CLS_ENV", "development")
        self.database_url = os.environ.get(
            "CLS_DATABASE_URL",
            f"sqlite:///{Path(__file__).resolve().parents[2] / 'data' / 'server_dev.sqlite3'}",
        )
        self.secret_key = os.environ.get("CLS_SECRET_KEY", "")
        if not self.secret_key:
            if self.env == "production":
                raise RuntimeError("生产环境必须设置 CLS_SECRET_KEY")
            # 开发环境自动生成（重启后会话失效，仅限开发）
            import secrets
            self.secret_key = secrets.token_hex(32)

        # 会话与令牌
        self.session_ttl_hours = int(os.environ.get("CLS_SESSION_TTL_HOURS", "72"))
        self.email_token_ttl_hours = int(os.environ.get("CLS_EMAIL_TOKEN_TTL_HOURS", "24"))
        self.reset_token_ttl_hours = int(os.environ.get("CLS_RESET_TOKEN_TTL_HOURS", "2"))

        # 速率限制（每分钟请求数）
        self.rate_limit_register = int(os.environ.get("CLS_RL_REGISTER", "5"))
        self.rate_limit_login = int(os.environ.get("CLS_RL_LOGIN", "10"))
        self.rate_limit_email = int(os.environ.get("CLS_RL_EMAIL", "3"))
        self.rate_limit_default = int(os.environ.get("CLS_RL_DEFAULT", "120"))

        # 试用与授权
        self.trial_days = int(os.environ.get("CLS_TRIAL_DAYS", "7"))
        self.offline_grace_hours = int(os.environ.get("CLS_OFFLINE_GRACE_HOURS", "72"))
        self.max_devices = int(os.environ.get("CLS_MAX_DEVICES", "3"))

        # 授权签名私钥（hex）。缺省时开发环境自动生成并落盘
        self.license_private_key_hex = os.environ.get("CLS_LICENSE_PRIVATE_KEY", "")

        # 邮件
        self.mail_backend = os.environ.get("CLS_MAIL_BACKEND", "console")  # console|smtp
        self.smtp_host = os.environ.get("CLS_SMTP_HOST", "")
        self.smtp_port = int(os.environ.get("CLS_SMTP_PORT", "465"))
        self.smtp_user = os.environ.get("CLS_SMTP_USER", "")
        self.smtp_password = os.environ.get("CLS_SMTP_PASSWORD", "")
        self.mail_from = os.environ.get("CLS_MAIL_FROM", "noreply@codelabel.local")
        self.app_base_url = os.environ.get(
            "CLS_APP_BASE_URL", "https://codelabel.example.com"
        )

        # CORS 白名单（逗号分隔）
        self.cors_origins = [
            o.strip() for o in os.environ.get(
                "CLS_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",") if o.strip()
        ]

        # 订阅计划（由服务端配置，客户端不得硬编码价格）
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

    @property
    def is_production(self):
        return self.env == "production"


settings = Settings()
