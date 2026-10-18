"""管理员 API：用户管理 / 订单审核 / 订阅调整 / 试用延长 / 统计 / 审计日志。

全部接口服务端校验管理员角色；高风险操作必须填写原因；
所有操作写 audit_logs（操作人、目标、时间、原因、摘要）。
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from ..app.database import get_db
from ..app.models import (
    AuditLog, Device, License, Order, Subscription, Trial, User,
)
from ..app.security import utc_now
from ..services.orders import OrderError, confirm_payment
from ..services.subscription import (
    SubscriptionError, adjust_subscription, effective_status,
)
from ..services.trial import TrialError, extend_trial, get_trial
from .deps import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _pagination(page: int, size: int) -> tuple[int, int]:
    """分页参数边界校验，防负 offset / 超大 page size。"""
    if page < 1:
        page = 1
    if size < 1:
        size = 1
    if size > 100:
        size = 100
    return page, size


# ---------- 用户 ----------
@router.get("/users")
def api_admin_users(q: str = Query(default=""), status: str = Query(default=""),
                    page: int = 1, size: int = 20,
                    db: Session = Depends(get_db)):
    page, size = _pagination(page, size)
    stmt = select(User)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((User.username.like(like)) | (User.email.like(like)))
    if status:
        stmt = stmt.where(User.account_status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    users = list(db.scalars(
        stmt.order_by(User.id.desc()).offset((page - 1) * size).limit(size)
    ))
    return {
        "total": total,
        "page": page,
        "size": size,
        "users": [_user_brief(db, u) for u in users],
    }


@router.get("/users/{user_id}")
def api_admin_user_detail(user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    orders = list(db.scalars(
        select(Order).where(Order.user_id == user_id).order_by(Order.id.desc())
    ))
    devices = list(db.scalars(select(Device).where(Device.user_id == user_id)))
    trial = get_trial(db, user)
    return {
        "user": _user_brief(db, user),
        "trial": None if not trial else {
            "status": trial.status,
            "started_at": trial.started_at.isoformat() + "Z",
            "expires_at": trial.expires_at.isoformat() + "Z",
            "extension_reason": trial.extension_reason,
        },
        "devices": [
            {"id": d.id, "device_name": d.device_name, "status": d.status,
             "activated_at": d.activated_at.isoformat() + "Z"}
            for d in devices
        ],
        "orders": [
            {"order_no": o.order_no, "amount": str(o.amount),
             "currency": o.currency, "order_status": o.order_status,
             "created_at": o.created_at.isoformat() + "Z"}
            for o in orders
        ],
        "effective_status": effective_status(db, user),
    }


class SuspendIn(BaseModel):
    reason: str


@router.post("/users/{user_id}/suspend")
def api_admin_suspend(user_id: int, payload: SuspendIn,
                      admin: User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    if not payload.reason.strip():
        raise HTTPException(status_code=400, detail="必须填写停用原因")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.account_status = "disabled"
    db.add(AuditLog(operator_id=admin.id, target_user_id=user_id,
                    action="suspend_user", reason=payload.reason.strip()))
    db.commit()
    return {"suspended": True}


@router.post("/users/{user_id}/resume")
def api_admin_resume(user_id: int, admin: User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.account_status = "active"
    db.add(AuditLog(operator_id=admin.id, target_user_id=user_id,
                    action="resume_user", reason="管理员恢复账号"))
    db.commit()
    return {"resumed": True}


class ExtendTrialIn(BaseModel):
    days: int
    reason: str


@router.post("/users/{user_id}/extend-trial")
def api_admin_extend_trial(user_id: int, payload: ExtendTrialIn,
                           admin: User = Depends(require_admin),
                           db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        trial = extend_trial(db, user, days=payload.days,
                             reason=payload.reason, operator_id=admin.id)
    except TrialError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.add(AuditLog(operator_id=admin.id, target_user_id=user_id,
                    action="extend_trial",
                    reason=payload.reason.strip(),
                    detail=f"days={payload.days}"))
    db.commit()
    return {"expires_at": trial.expires_at.isoformat() + "Z"}


# ---------- 订单 ----------
@router.get("/orders")
def api_admin_orders(status: str = Query(default=""),
                     page: int = 1, size: int = 20,
                     db: Session = Depends(get_db)):
    page, size = _pagination(page, size)
    stmt = select(Order)
    if status:
        stmt = stmt.where(Order.order_status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    orders = list(db.scalars(
        stmt.order_by(Order.id.desc()).offset((page - 1) * size).limit(size)
    ))
    user_ids = {o.user_id for o in orders}
    usernames = {
        u.id: u.username for u in db.scalars(
            select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}
    return {
        "total": total,
        "orders": [
            {"id": o.id,
             "order_no": o.order_no,
             "user_id": o.user_id,
             "username": usernames.get(o.user_id),
             "amount": str(o.amount), "currency": o.currency,
             "order_status": o.order_status,
             "payment_reference": o.payment_reference,
             "created_at": o.created_at.isoformat() + "Z"}
            for o in orders
        ],
    }


class ConfirmPaymentIn(BaseModel):
    approved: bool
    reason: str


@router.post("/orders/{order_id}/confirm-payment")
def api_admin_confirm_payment(order_id: int, payload: ConfirmPaymentIn,
                              admin: User = Depends(require_admin),
                              db: Session = Depends(get_db)):
    try:
        order = confirm_payment(db, order_id=order_id, admin=admin,
                                approved=payload.approved,
                                reason=payload.reason)
    except OrderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {"order_status": order.order_status}


# ---------- 订阅 ----------
class AdjustSubscriptionIn(BaseModel):
    action: str  # extend|terminate|suspend|resume
    days: int | None = None
    reason: str


@router.post("/subscriptions/{subscription_id}/adjust")
def api_admin_adjust_subscription(subscription_id: int,
                                  payload: AdjustSubscriptionIn,
                                  admin: User = Depends(require_admin),
                                  db: Session = Depends(get_db)):
    try:
        sub = adjust_subscription(db, subscription_id, action=payload.action,
                                  days=payload.days,
                                  reason=payload.reason)
    except SubscriptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.add(AuditLog(operator_id=admin.id, target_user_id=sub.user_id,
                    action=f"adjust_subscription_{payload.action}",
                    reason=payload.reason.strip(),
                    detail=f"subscription={subscription_id}"))
    db.commit()
    return {"status": sub.status, "expires_at": sub.expires_at.isoformat() + "Z"}


# ---------- 授权 ----------
class RevokeLicenseIn(BaseModel):
    reason: str


@router.post("/licenses/{license_id}/revoke")
def api_admin_revoke_license(license_id: str, payload: RevokeLicenseIn,
                             admin: User = Depends(require_admin),
                             db: Session = Depends(get_db)):
    from ..services.license import LicenseError, revoke_license
    try:
        revoke_license(db, license_id, reason=payload.reason)
    except LicenseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.add(AuditLog(operator_id=admin.id, action="revoke_license",
                    reason=payload.reason.strip(),
                    detail=f"license={license_id}"))
    db.commit()
    return {"revoked": True}


# ---------- 统计与审计 ----------
@router.get("/stats")
def api_admin_stats(db: Session = Depends(get_db)):
    now = utc_now()
    users_total = db.scalar(select(func.count()).select_from(User)) or 0
    verified = db.scalar(
        select(func.count()).select_from(User).where(
            User.email_verified_at.is_not(None))) or 0
    trialing = db.scalar(
        select(func.count()).select_from(Subscription).where(
            Subscription.status == "trialing")) or 0
    active = db.scalar(
        select(func.count()).select_from(Subscription).where(
            Subscription.status == "active")) or 0
    expired = db.scalar(
        select(func.count()).select_from(Subscription).where(
            Subscription.status == "expired")) or 0
    orders_pending = db.scalar(
        select(func.count()).select_from(Order).where(
            Order.order_status == "pending")) or 0
    orders_confirmed = db.scalar(
        select(func.count()).select_from(Order).where(
            Order.order_status == "confirmed")) or 0
    revenue = db.scalar(
        select(func.coalesce(func.sum(Order.amount), 0)).where(
            Order.order_status == "confirmed")) or 0
    return {
        "users_total": users_total,
        "email_verified": verified,
        "trialing": trialing,
        "active_subscriptions": active,
        "expired_subscriptions": expired,
        "orders_pending": orders_pending,
        "orders_confirmed": orders_confirmed,
        # 统一两位小数（空值时也是 0.00）
        "confirmed_revenue": f"{Decimal(str(revenue)):.2f}",
        "generated_at": now.isoformat() + "Z",
    }


@router.get("/audit-logs")
def api_admin_audit_logs(page: int = 1, size: int = 50,
                         db: Session = Depends(get_db)):
    page, size = _pagination(page, size)
    total = db.scalar(select(func.count()).select_from(AuditLog)) or 0
    logs = list(db.scalars(
        select(AuditLog).order_by(AuditLog.id.desc())
        .offset((page - 1) * size).limit(size)
    ))
    return {
        "total": total,
        "logs": [
            {"id": l.id, "operator_id": l.operator_id,
             "target_user_id": l.target_user_id, "action": l.action,
             "reason": l.reason, "detail": l.detail,
             "created_at": l.created_at.isoformat() + "Z"}
            for l in logs
        ],
    }


def _user_brief(db: Session, u: User) -> dict:
    trial = get_trial(db, u)
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "account_status": u.account_status,
        "is_admin": u.is_admin,
        "email_verified": u.email_verified_at is not None,
        "created_at": u.created_at.isoformat() + "Z",
        "last_login_at": u.last_login_at.isoformat() + "Z" if u.last_login_at else None,
        "effective_status": effective_status(db, u),
        "trial_expires_at": trial.expires_at.isoformat() + "Z" if trial else None,
    }
