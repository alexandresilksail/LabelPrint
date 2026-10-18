"""订阅与授权状态服务。

状态机：trialing -> active -> past_due/expired/canceled/suspended
统一由服务端根据试用、订阅记录与管理操作计算，客户端不得伪造。
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..app.config import settings
from ..app.models import Subscription, Trial, User
from ..app.security import utc_now

# 允许使用受保护功能的状态
ENTITLED_STATUSES = ("trialing", "active")


class SubscriptionError(Exception):
    pass


def get_subscription(db: Session, user: User) -> Subscription | None:
    return db.scalar(
        select(Subscription)
        .where(Subscription.user_id == user.id)
        .order_by(Subscription.id.desc())
    )


def has_active_subscription(db: Session, user: User) -> bool:
    sub = get_subscription(db, user)
    return bool(sub and sub.status == "active" and sub.expires_at > utc_now())


def refresh_subscription_status(db: Session, user: User) -> Subscription | None:
    """惰性刷新：到期置 expired。"""
    sub = get_subscription(db, user)
    if not sub:
        return None
    now = utc_now()
    if sub.status in ("trialing", "active", "past_due") and sub.expires_at < now:
        sub.status = "expired"
        db.commit()
        db.refresh(sub)
    return sub


def effective_status(db: Session, user: User) -> str:
    """用户当前统一授权状态。

    优先级：有效订阅 > 试用 > 账号状态。
    """
    if user.account_status != "active":
        return "suspended"
    sub = refresh_subscription_status(db, user)
    if sub and sub.status == "active" and sub.expires_at > utc_now():
        return "active"
    if sub and sub.status in ("past_due",):
        return "past_due"
    if sub and sub.status == "canceled":
        return "canceled"
    if sub and sub.status == "suspended":
        return "suspended"
    trial = db.scalar(select(Trial).where(Trial.user_id == user.id))
    if trial:
        if trial.status == "active" and trial.expires_at > utc_now():
            return "trialing"
        return "expired"
    return "expired"


def activate_subscription(db: Session, user: User, *, plan,
                          billing_period_days: int) -> Subscription:
    """付款确认后开通订阅。"""
    sub = get_subscription(db, user)
    now = utc_now()
    if sub is None:
        sub = Subscription(user_id=user.id, plan_id=plan.id, status="active",
                           started_at=now, expires_at=now)
        db.add(sub)
        db.flush()
    sub.status = "active"
    sub.plan_id = plan.id
    sub.started_at = now
    sub.expires_at = now + timedelta(days=billing_period_days)
    db.commit()
    db.refresh(sub)
    return sub


def suspend_subscription(db: Session, user: User, *, reason: str) -> Subscription:
    if not reason or not reason.strip():
        raise SubscriptionError("暂停订阅必须填写原因")
    sub = get_subscription(db, user)
    if not sub:
        raise SubscriptionError("该用户没有订阅记录")
    sub.status = "suspended"
    db.commit()
    db.refresh(sub)
    return sub


def resume_subscription(db: Session, user: User) -> Subscription:
    sub = get_subscription(db, user)
    if not sub:
        raise SubscriptionError("该用户没有订阅记录")
    if sub.expires_at < utc_now():
        sub.status = "expired"
    else:
        sub.status = "active"
    db.commit()
    db.refresh(sub)
    return sub


def adjust_subscription(db: Session, subscription_id: int, *,
                        action: str, days: int | None = None,
                        reason: str) -> Subscription:
    """管理员调整订阅：extend（延长）/ terminate（终止）/ suspend / resume。"""
    if not reason or not reason.strip():
        raise SubscriptionError("调整订阅必须填写原因")
    sub = db.get(Subscription, subscription_id)
    if not sub:
        raise SubscriptionError("订阅记录不存在")
    if action == "extend":
        if not days or days <= 0 or days > 730:
            raise SubscriptionError("延长天数必须在 1-730 之间")
        sub.expires_at = sub.expires_at + timedelta(days=days)
        if sub.status == "expired":
            sub.status = "active"
    elif action == "terminate":
        sub.status = "canceled"
        sub.expires_at = utc_now()
    elif action == "suspend":
        sub.status = "suspended"
    elif action == "resume":
        if sub.expires_at < utc_now():
            sub.status = "expired"
        else:
            sub.status = "active"
    else:
        raise SubscriptionError(f"未知调整动作: {action}")
    db.commit()
    db.refresh(sub)
    return sub


def subscription_summary(db: Session, user: User) -> dict:
    status = effective_status(db, user)
    sub = get_subscription(db, user)
    trial = db.scalar(select(Trial).where(Trial.user_id == user.id))
    now = utc_now()
    return {
        "status": status,
        "entitled": status in ENTITLED_STATUSES,
        "subscription": None if not sub else {
            "status": sub.status,
            "plan_code": sub.plan.plan_code if sub.plan else None,
            "plan_name": sub.plan.plan_name if sub.plan else None,
            "expires_at": sub.expires_at.isoformat() + "Z",
        },
        "trial": None if not trial else {
            "status": trial.status,
            "started_at": trial.started_at.isoformat() + "Z",
            "expires_at": trial.expires_at.isoformat() + "Z",
            "days_remaining": max(
                0, (trial.expires_at - now).days),
        },
        "offline_grace_hours": settings.offline_grace_hours,
    }
