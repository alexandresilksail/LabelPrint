"""订单服务：创建、查询、付款凭证提交、管理员人工确认。

关键约束（审计修复）：

- 复用待付款订单必须**同方案、同金额、同币种**；用户切换月付/年付时
  取消旧单（保留历史与事件）并新建，绝不复用另一方案的旧订单；
- 并发下单由数据库部分唯一索引 `uq_order_user_pending` 兜底，撞索引后
  重新查询复用，不产生两条待付款订单；
- 付款确认在同一事务内完成：订单状态、支付事件、订阅激活、审计日志、
  通知 outbox 一起提交；邮件由 outbox 在提交后投递，发信失败不影响
  订单状态；
- 只有管理员确认付款后才开通订阅（前端提交"已付款"无效）。
"""

import secrets
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..app.models import AuditLog, Order, PaymentEvent, Plan, User
from ..app.security import utc_now
from ..app import mailer
from . import outbox
from .subscription import activate_subscription

ORDER_NO_PREFIX = "CLS"

# 已进入终态、不允许再次确认的状态
TERMINAL_STATUSES = ("confirmed", "refunded", "canceled", "failed")


class OrderError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def list_enabled_plans(db: Session) -> list[Plan]:
    return list(db.scalars(select(Plan).where(Plan.enabled.is_(True))))


def _generate_order_no() -> str:
    return f"{ORDER_NO_PREFIX}{utc_now().strftime('%Y%m%d%H%M%S')}{secrets.randbelow(1000000):06d}"


def _same_plan_snapshot(order: Order, plan: Plan) -> bool:
    """待付款订单是否就是当前所选方案的等价订单。"""
    if order.plan_id != plan.id:
        return False
    if Decimal(str(order.amount)) != Decimal(str(plan.price)):
        return False
    return (order.currency or "").upper() == (plan.currency or "").upper()


def _find_pending(db: Session, user_id: int) -> Order | None:
    return db.scalar(select(Order).where(
        Order.user_id == user_id,
        Order.order_status == "pending",
    ).order_by(Order.id.desc()))


def _cancel_pending(db: Session, order: Order, *, reason: str) -> None:
    """取消旧待付款订单：保留记录与事件，不删除历史。"""
    order.order_status = "canceled"
    db.add(PaymentEvent(
        order_id=order.id, provider="manual",
        provider_event_id=f"cancel-{order.order_no}",
        event_type="order_canceled", amount=order.amount,
        currency=order.currency, verified=True,
    ))
    db.add(AuditLog(
        operator_id=None, target_user_id=order.user_id,
        action="order_canceled_on_plan_change", reason=reason,
        detail=f"order={order.order_no}",
    ))


def create_order(db: Session, user: User, *, plan_code: str) -> Order:
    plan = db.scalar(select(Plan).where(Plan.plan_code == plan_code,
                                        Plan.enabled.is_(True)))
    if not plan:
        raise OrderError("订阅计划不存在或未启用")

    for _attempt in range(2):
        pending = _find_pending(db, user.id)
        if pending is not None:
            if _same_plan_snapshot(pending, plan):
                # 幂等复用：同一方案重复点击"下单"不会产生新订单
                return pending
            # 方案已切换：取消旧单并新建，避免把月付订单当成年付订单返回
            _cancel_pending(db, pending,
                            reason=f"切换订阅方案：{pending.plan_id} -> "
                                   f"{plan.id}（{plan.plan_code}）")
            db.flush()

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
        try:
            db.flush()          # 触发 uq_order_user_pending 检查
        except IntegrityError:
            # 并发请求先插入了待付款订单：回滚本轮后重查复用
            db.rollback()
            continue

        db.add(PaymentEvent(
            order_id=order.id, provider="manual",
            provider_event_id=f"create-{order.order_no}",
            event_type="order_created", amount=order.amount,
            currency=order.currency, verified=True,
        ))
        try:
            db.commit()
        except Exception as exc:          # noqa: BLE001 - 写库失败需回滚并上报
            db.rollback()
            raise OrderError("订单创建失败，请稍后重试",
                             status_code=500) from exc
        db.refresh(order)
        return order

    # 两轮都撞并发：对方已建好同方案订单，直接复用；方案不同则明确报冲突
    pending = _find_pending(db, user.id)
    if pending is not None and _same_plan_snapshot(pending, plan):
        return pending
    raise OrderError("订单创建冲突，请稍后重试", status_code=409)


def get_order(db: Session, user: User, order_id: int) -> Order:
    order = db.get(Order, order_id)
    # 统一 404：不透露订单是否存在（仅归属本人可见）
    if not order or order.user_id != user.id:
        raise OrderError("订单不存在", status_code=404)
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
    """管理员人工确认付款。

    approved=True 时：订单置 confirmed、写支付事件、激活订阅、写审计日志、
    入队通知邮件——全部在**同一个事务**内提交。邮件在提交后由 outbox
    投递，发信失败只影响 outbox 记录，不会让订单状态与实际通知不一致。
    """
    if not admin.is_admin:
        raise OrderError("只有管理员可以确认付款", status_code=403)
    if not reason or not reason.strip():
        raise OrderError("确认付款必须填写原因")

    # 行级锁（PostgreSQL/MySQL 生效；SQLite 串行写已足够）
    order = db.scalar(
        select(Order).where(Order.id == order_id).with_for_update())
    if not order:
        raise OrderError("订单不存在", status_code=404)
    if order.order_status in TERMINAL_STATUSES:
        raise OrderError(f"订单已终态（{order.order_status}），不能重复确认",
                         status_code=409)

    now = utc_now()
    plan = db.get(Plan, order.plan_id)
    user = db.get(User, order.user_id)
    if not plan or not user:
        raise OrderError("订单关联的计划或用户不存在", status_code=409)

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
        days = 30 if plan.billing_period == "monthly" else 365
        # commit=False：订阅激活必须与订单状态同事务，不能各自提交
        activate_subscription(db, user, plan=plan, billing_period_days=days,
                              commit=False)
    else:
        order.order_status = "failed"
        db.add(PaymentEvent(
            order_id=order.id, provider="manual",
            provider_event_id=f"reject-{order.order_no}",
            event_type="payment_rejected", verified=False,
        ))

    db.add(AuditLog(
        operator_id=admin.id, target_user_id=order.user_id,
        action="confirm_payment" if approved else "reject_payment",
        reason=reason.strip(),
        detail=f"order={order.order_no} amount={order.amount} "
               f"{order.currency}",
    ))

    subject, body = mailer.payment_notification(order.order_no, approved)
    outbox.enqueue(
        db,
        dedupe_key=f"payment-result:{order.order_no}",
        topic="payment_result",
        recipient=user.email,
        subject=subject,
        body_text=body,
    )

    try:
        db.commit()
    except Exception as exc:          # noqa: BLE001 - 提交失败必须整体回滚
        db.rollback()
        raise OrderError("订单确认写入失败，请重试",
                         status_code=500) from exc

    db.refresh(order)
    # 提交之后再投递；投递异常在 outbox 内部消化，不影响已确认的订单
    try:
        outbox.dispatch_pending(db, limit=20)
    except Exception:                 # noqa: BLE001
        outbox.logger.exception("付款通知投递异常（订单已确认）")
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
