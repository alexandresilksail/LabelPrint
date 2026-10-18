"""订单服务：创建、查询、付款凭证提交、管理员人工确认。

- 金额 Decimal，币种服务端配置；
- 订单号唯一（重试安全）；
- 只有管理员确认付款后才开通订阅（前端提交"已付款"无效）；
- 全部状态变更写 PaymentEvent 与审计日志；
- 预留支付 webhook 适配器（签名验证 + 幂等）。
"""

import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..app.models import AuditLog, Order, PaymentEvent, Plan, Subscription, User
from ..app.security import utc_now
from .subscription import activate_subscription, get_subscription

ORDER_NO_PREFIX = "CLS"


class OrderError(Exception):
    pass


def list_enabled_plans(db: Session) -> list[Plan]:
    return list(db.scalars(select(Plan).where(Plan.enabled.is_(True))))


def _generate_order_no() -> str:
    return f"{ORDER_NO_PREFIX}{utc_now().strftime('%Y%m%d%H%M%S')}{secrets.randbelow(1000000):06d}"


def create_order(db: Session, user: User, *, plan_code: str) -> Order:
    plan = db.scalar(select(Plan).where(Plan.plan_code == plan_code,
                                        Plan.enabled.is_(True)))
    if not plan:
        raise OrderError("订阅计划不存在或未启用")
    # 已有待付款订单则复用（防重复下单）
    pending = db.scalar(select(Order).where(
        Order.user_id == user.id,
        Order.order_status == "pending",
    ).order_by(Order.id.desc()))
    if pending:
        return pending
    order = Order(
        order_no=_generate_order_no(),
        user_id=user.id,
        plan_id=plan.id,
        amount=Decimal(str(plan.price)),
        currency=plan.currency,
        order_status="pending",
        payment_provider="manual",
    )
    db.add(order)
    db.flush()
    db.add(PaymentEvent(
        order_id=order.id, provider="manual",
        provider_event_id=f"create-{order.order_no}",
        event_type="order_created", amount=order.amount,
        currency=order.currency, verified=True,
    ))
    db.commit()
    db.refresh(order)
    return order


def get_order(db: Session, user: User, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise OrderError("订单不存在")
    return order


def list_orders(db: Session, user: User) -> list[Order]:
    return list(db.scalars(
        select(Order).where(Order.user_id == user.id).order_by(Order.id.desc())
    ))


def submit_payment_proof(db: Session, user: User, order_id: int, *,
                         reference: str) -> Order:
    """用户提交付款凭证（交易参考号）。不等于已付款。"""
    order = get_order(db, user, order_id)
    if order.order_status != "pending":
        raise OrderError("订单状态不允许提交付款凭证")
    if not reference or not reference.strip():
        raise OrderError("请填写付款交易参考号")
    order.payment_reference = reference.strip()[:500]
    order.order_status = "pending"  # 仍待管理员核实
    db.add(PaymentEvent(
        order_id=order.id, provider="manual",
        provider_event_id=f"proof-{order.order_no}-{secrets.token_hex(4)}",
        event_type="payment_proof_submitted", verified=False,
    ))
    db.commit()
    db.refresh(order)
    return order


def confirm_payment(db: Session, *, order_id: int, admin: User,
                    approved: bool, reason: str) -> Order:
    """管理员人工确认付款。approved=True 时开通订阅。"""
    if not admin.is_admin:
        raise OrderError("只有管理员可以确认付款", status_code=403)
    if not reason or not reason.strip():
        raise OrderError("确认付款必须填写原因")
    order = db.get(Order, order_id)
    if not order:
        raise OrderError("订单不存在")
    if order.order_status in ("confirmed", "refunded", "canceled"):
        raise OrderError(f"订单已终态（{order.order_status}），不能重复确认")

    now = utc_now()
    if approved:
        order.order_status = "confirmed"
        order.paid_at = order.paid_at or now
        order.confirmed_at = now
        order.confirmed_by = admin.id
        db.add(PaymentEvent(
            order_id=order.id, provider="manual",
            provider_event_id=f"confirm-{order.order_no}",
            event_type="payment_confirmed", amount=order.amount,
            currency=order.currency, verified=True,
        ))
        # 开通订阅
        plan = db.get(Plan, order.plan_id)
        user = db.get(User, order.user_id)
        days = 30 if plan.billing_period == "monthly" else 365
        activate_subscription(db, user, plan=plan, billing_period_days=days)
        # 通知用户
        from ..app.mailer import payment_notification, send_mail
        subject, body = payment_notification(order.order_no, True)
        send_mail(user.email, subject, body)
    else:
        order.order_status = "failed"
        db.add(PaymentEvent(
            order_id=order.id, provider="manual",
            provider_event_id=f"reject-{order.order_no}",
            event_type="payment_rejected", verified=False,
        ))
        user = db.get(User, order.user_id)
        from ..app.mailer import payment_notification, send_mail
        subject, body = payment_notification(order.order_no, False)
        send_mail(user.email, subject, body)

    db.add(AuditLog(
        operator_id=admin.id, target_user_id=order.user_id,
        action="confirm_payment" if approved else "reject_payment",
        reason=reason.strip(),
        detail=f"order={order.order_no} amount={order.amount} "
               f"{order.currency}",
    ))
    db.commit()
    db.refresh(order)
    return order


# ---------- 支付 webhook 适配器（预留） ----------
def process_webhook(db: Session, *, provider: str, event_id: str,
                    signature: str, raw_body: bytes) -> bool:
    """自动支付 webhook 入口。

    未接入真实支付商时保持拒绝；接入后必须：
    1. 验证服务商签名（verify_webhook_signature）；
    2. 按 (provider, event_id) 幂等去重；
    3. 核对订单号、金额、币种、支付状态；
    4. 仅全部通过才调用 confirm_payment 等价逻辑。
    """
    known = {"alipay", "wechatpay", "stripe"}
    if provider not in known:
        raise OrderError("不支持的支付服务商")
    # 签名密钥未配置 -> 一律拒绝（防止伪造回调）
    raise OrderError("支付 webhook 尚未配置签名密钥，已拒绝处理")
