"""账户 API：me / 订阅状态 / 设备列表 / 设备解绑 / 授权状态。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..app.config import settings
from ..app.database import get_db
from ..app.models import User
from ..app.security import utc_now
from ..services.license import (
    LicenseError, deactivate_device, list_devices,
)
from ..services.subscription import effective_status, subscription_summary
from .deps import get_current_user

router = APIRouter(prefix="/api", tags=["account"])


@router.get("/me")
def api_me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "email_verified": user.email_verified_at is not None,
        "account_status": user.account_status,
        "is_admin": user.is_admin,
        "created_at": user.created_at.isoformat() + "Z",
        "server_time_utc": utc_now().isoformat() + "Z",
    }


@router.get("/me/subscription")
def api_my_subscription(user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    return subscription_summary(db, user)


@router.get("/me/devices")
def api_my_devices(user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    devices = list_devices(db, user)
    return {
        "devices": [
            {
                "id": d.id,
                "device_name": d.device_name,
                "status": d.status,
                "activated_at": d.activated_at.isoformat() + "Z",
                "last_seen_at": d.last_seen_at.isoformat() + "Z",
            }
            for d in devices
        ],
        "max_devices": settings.max_devices,
    }


class RevokeDeviceIn(BaseModel):
    device_id: int


@router.post("/me/devices/revoke")
def api_revoke_device(payload: RevokeDeviceIn,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    try:
        deactivate_device(db, user, payload.device_id)
    except LicenseError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(exc))
    return {"revoked": True}


@router.get("/license/status")
def api_license_status(user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    return {
        "status": effective_status(db, user),
        "subscription": subscription_summary(db, user),
    }
