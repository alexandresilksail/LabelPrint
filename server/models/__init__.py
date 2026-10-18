"""SQLAlchemy 模型（兼容垫片）。

真实实现位于 server/app/models.py（单一事实来源，避免双份模型定义）。
本模块仅做转发：from server.models import User 仍然可用。
"""

from .app.models import *  # noqa: F401,F403
from .app.models import (  # noqa: F401
    AuditLog,
    Device,
    EmailVerification,
    License,
    Order,
    PasswordResetToken,
    PaymentEvent,
    Plan,
    Session,
    Subscription,
    Trial,
    User,
    utcnow,
)
