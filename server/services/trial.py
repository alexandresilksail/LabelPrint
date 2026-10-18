"""试用服务：服务端控制的 7 天试用。

- 首次邮箱验证成功后创建，每用户唯一（DB 唯一约束 + 幂等）；
- 时间完全由服务端决定，客户端本地时间无法延长；
- 重装/清本地库/改系统时间均不重置（状态在服务端持久化）；
- 管理员可延长并记录原因（admin 服务实现）。
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..app.config import settings
from ..app.models import Subscription, Trial, User
from ..app.security import utc_now


class TrialError(Exception):
    pass


def start_trial(db: Session, user: User) -> Trial:
    """幂等创建初始试用。已存在则直接返回既有记录。"""
    existing = db.scalar(select(Trial).where(Trial.user_id == user.id))
    if existing:
        return existing
    now = utc_now()
    trial = Trial(
        user_id=user.id,
        started_at=now,
        expires_at=now + timedelta(days=settings.trial_days),
        status="active",
    )
    db.add(trial)
    db.flush()
    # 同步创建 trialing 订阅记录（授权状态以订阅为准）
    sub = db.scalar(select(Subscription).where(Subscription.user_id == user.id))
    if sub is None:
        plan = _default_trial_plan(db)
        db.add(Subscription(
            user_id=user.id,
            plan_id=plan.id,
            status="trialing",
            started_at=now,
            expires_at=trial.expires_at,
        ))
    db.commit()
    db.refresh(trial)
    return trial


def _default_trial_plan(db: Session):
    from ..app.models import Plan
    plan = db.scalar(select(Plan).where(Plan.plan_code == "trial"))
    if plan is None:
        # enabled=False：内部占位计划，不对外售卖（/api/plans 只列 enabled）
        plan = Plan(plan_code="trial", plan_name="免费试用",
                    billing_period="trial", price=0, currency="CNY",
                    enabled=False)
        db.add(plan)
        db.flush()
    return plan


def get_trial(db: Session, user: User) -> Trial | None:
    return db.scalar(select(Trial).where(Trial.user_id == user.id))


def refresh_trial_status(db: Session, trial: Trial) -> Trial:
    """惰性到期：时间到达即置 expired（不改动开始时间）。"""
    if trial.status == "active" and trial.expires_at < utc_now():
        trial.status = "expired"
        db.commit()
        db.refresh(trial)
    return trial


def extend_trial(db: Session, user: User, *, days: int, reason: str,
                 operator_id: int) -> Trial:
    """管理员延长试用（记录原因）。"""
    if days <= 0 or days > 365:
        raise TrialError("延长期限必须在 1-365 天之间")
    if not reason or not reason.strip():
        raise TrialError("延长试用必须填写原因")
    trial = get_trial(db, user)
    if not trial:
        raise TrialError("该用户尚无试用记录")
    from .subscription import has_active_subscription
    if has_active_subscription(db, user):
        raise TrialError("该用户已有有效订阅，无需延长试用")
    trial.expires_at = trial.expires_at + timedelta(days=days)
    if trial.status == "expired":
        trial.status = "extended"
    trial.extension_reason = (
        f"{trial.extension_reason or ''}\n"
        f"[{utc_now().isoformat()}] 操作人#{operator_id}: {reason.strip()}"
    ).strip()
    _sync_subscription_from_trial(db, user, trial)
    db.commit()
    db.refresh(trial)
    return trial


def _sync_subscription_from_trial(db: Session, user: User, trial: Trial):
    from .subscription import get_subscription
    sub = get_subscription(db, user)
    if sub and sub.status in ("trialing", "expired"):
        sub.status = "trialing" if trial.expires_at >= utc_now() else "expired"
        sub.expires_at = trial.expires_at
