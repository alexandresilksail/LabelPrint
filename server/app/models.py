"""服务端数据模型（规格第十四节）。

表：users / email_verifications / password_reset_tokens / trials /
    plans / subscriptions / orders / payment_events / devices /
    sessions / licenses / audit_logs

约定：
- 所有时间列统一存储 UTC（naive UTC datetime，由服务端写入）；
- 金额一律使用 Numeric(12, 2)（Decimal），禁止浮点；
- 关键业务表带唯一约束与状态 CHECK（幂等与防重复）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("account_status IN "
                        "('pending','active','disabled')", name="ck_user_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    account_status: Mapped[str] = mapped_column(String(16), default="pending")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow,
                                                 onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)

    trial = relationship("Trial", back_populates="user", uselist=False)
    subscriptions = relationship("Subscription", back_populates="user")
    orders = relationship("Order", back_populates="user")
    devices = relationship("Device", back_populates="user")
    sessions = relationship("Session", back_populates="user")


class EmailVerification(Base):
    __tablename__ = "email_verifications"
    __table_args__ = (
        UniqueConstraint("user_id", "consumed_at", name="uq_email_verif_user_pending"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Trial(Base):
    __tablename__ = "trials"
    __table_args__ = (
        # 每个用户只能有一条初始试用记录（幂等防重复试用）
        UniqueConstraint("user_id", name="uq_trial_user"),
        CheckConstraint("expires_at > started_at", name="ck_trial_window"),
        CheckConstraint("status IN "
                        "('active','expired','extended')", name="ck_trial_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="active")
    extension_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user = relationship("User", back_populates="trial")


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_plan_price"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_code: Mapped[str] = mapped_column(String(32), unique=True)
    plan_name: Mapped[str] = mapped_column(String(64))
    billing_period: Mapped[str] = mapped_column(String(16))  # monthly|yearly
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow,
                                                 onupdate=utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('trialing','active','past_due','expired',"
            "'canceled','suspended')", name="ck_sub_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    status: Mapped[str] = mapped_column(String(16), default="trialing")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow,
                                                 onupdate=utcnow)

    user = relationship("User", back_populates="subscriptions")
    plan = relationship("Plan")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_order_amount"),
        CheckConstraint(
            "order_status IN ('pending','paid','confirmed','failed',"
            "'refunded','canceled')", name="ck_order_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    order_status: Mapped[str] = mapped_column(String(16), default="pending")
    payment_provider: Mapped[str] = mapped_column(String(32), default="manual")
    provider_order_id: Mapped[str | None] = mapped_column(String(128))
    payment_reference: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user = relationship("User", back_populates="orders", foreign_keys=[user_id])
    plan = relationship("Plan")
    events = relationship("PaymentEvent", back_populates="order")


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    __table_args__ = (
        # 幂等：同一服务商同一事件号只处理一次
        UniqueConstraint("provider", "provider_event_id",
                         name="uq_payment_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(32))
    provider_event_id: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(32))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(8))
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    order = relationship("Order", back_populates="events")


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("user_id", "device_fingerprint_hash",
                         name="uq_device_user_fp"),
        CheckConstraint("status IN ('active','revoked')", name="ck_device_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True)
    device_fingerprint_hash: Mapped[str] = mapped_column(String(64))
    device_name: Mapped[str] = mapped_column(String(128), default="未命名设备")
    status: Mapped[str] = mapped_column(String(16), default="active")
    activated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)

    user = relationship("User", back_populates="devices")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user = relationship("User", back_populates="sessions")


class License(Base):
    __tablename__ = "licenses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','revoked','expired')", name="ck_license_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    license_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True)
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"))
    subscription_status: Mapped[str] = mapped_column(String(16))
    plan_code: Mapped[str | None] = mapped_column(String(32))
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="active")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user = relationship("User")
    device = relationship("Device")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)  # JSON 摘要，禁存敏感数据
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
