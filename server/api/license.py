"""授权 API：设备激活 / 授权签发 / 联网验证 / 授权撤销。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..app.database import get_db
from ..app.models import User
from ..services.license import (
    LicenseError, activate_device, issue_license, validate_license,
)
from .deps import get_current_user

router = APIRouter(prefix="/api/license", tags=["license"])


class ActivateDeviceIn(BaseModel):
    fingerprint: str
    device_name: str = ""


@router.post("/activate-device")
def api_activate_device(payload: ActivateDeviceIn,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    try:
        device = activate_device(db, user, fingerprint=payload.fingerprint,
                                 device_name=payload.device_name)
    except LicenseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"device_id": device.id, "device_name": device.device_name,
            "status": device.status}


@router.post("/issue")
def api_issue_license(payload: ActivateDeviceIn,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    try:
        device = activate_device(db, user, fingerprint=payload.fingerprint,
                                 device_name=payload.device_name)
        result = issue_license(db, user, device)
    except LicenseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


class ValidateIn(BaseModel):
    credential: dict


@router.post("/validate")
def api_validate_license(payload: ValidateIn,
                         user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    try:
        return validate_license(db, user, credential=payload.credential)
    except LicenseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/public-key")
def api_public_key():
    from ..app.licensing import public_key_b64
    return {"algorithm": "Ed25519", "public_key_b64": public_key_b64()}
