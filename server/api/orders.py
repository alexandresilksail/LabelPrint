"""订单 API：计划列表 / 创建订单 / 订单列表 / 订单详情 / 提交付款凭证。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..app.database import get_db
from ..app.models import User
from ..services.orders import (
    OrderError, create_order, get_order, list_enabled_plans, list_orders,
    submit_payment_proof,
)
from .deps import get_current_user

router = APIRouter(prefix="/api", tags=["orders"])


@router.get("/plans")
def api_plans(db: Session = Depends(get_db)):
    plans = list_enabled_plans(db)
    return {
        "plans": [
            {
                "plan_code": p.plan_code,
                "plan_name": p.plan_name,
                "billing_period": p.billing_period,
                "price": str(p.price),
                "currency": p.currency,
            }
            for p in plans
        ]
    }


class CreateOrderIn(BaseModel):
    plan_code: str


@router.post("/orders", status_code=201)
def api_create_order(payload: CreateOrderIn,
                     user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    try:
        order = create_order(db, user, plan_code=payload.plan_code)
    except OrderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return _order_dict(order)


@router.get("/orders")
def api_list_orders(user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    return {"orders": [_order_dict(o) for o in list_orders(db, user)]}


@router.get("/orders/{order_id}")
def api_get_order(order_id: int, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    try:
        order = get_order(db, user, order_id)
    except OrderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return _order_dict(order)


class PaymentProofIn(BaseModel):
    reference: str


@router.post("/orders/{order_id}/payment-proof")
def api_payment_proof(order_id: int, payload: PaymentProofIn,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    try:
        order = submit_payment_proof(db, user, order_id,
                                     reference=payload.reference)
    except OrderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return _order_dict(order)


def _order_dict(order) -> dict:
    return {
        "id": order.id,
        "order_no": order.order_no,
        "plan_code": order.plan.plan_code if order.plan else None,
        "plan_name": order.plan.plan_name if order.plan else None,
        "amount": str(order.amount),
        "currency": order.currency,
        "order_status": order.order_status,
        "payment_provider": order.payment_provider,
        "payment_reference": order.payment_reference,
        "created_at": order.created_at.isoformat() + "Z",
        "paid_at": order.paid_at.isoformat() + "Z" if order.paid_at else None,
    }
