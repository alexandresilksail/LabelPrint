"""授权服务：设备绑定、授权签发、验证、撤销、离线宽限。

- 设备指纹仅存 SHA-256 哈希，最小化收集；
- 授权凭证由服务端 Ed25519 私钥签发，客户端仅持公钥验签；
- validate 必须联网：服务端核对订阅状态与撤销记录；
- 离线宽限：授权过期/撤销后，客户端在宽限期内仍可验证缓存凭证，
  宽限由服务端配置（默认 72h），不延长已撤销授权。
"""

import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..app.config import settings
from ..app.licensing import issue_credential
from ..app.models import Device, License, User
from ..app.security import hash_token, utc_now
from .subscription import effective_status, get_subscription


class LicenseError(Exception):
    pass


def _hash_fingerprint(fingerprint: str) -> str:
    return hash_token(fingerprint)


def activate_device(db: Session, user: User, *, fingerprint: str,
                    device_name: str = "") -> Device:
    """激活/复用设备。超出数量限制时拒绝最旧的非活跃策略由管理员处理。"""
    if not fingerprint or len(fingerprint) < 8:
        raise LicenseError("设备指纹不合法")
    fp_hash = _hash_fingerprint(fingerprint)
    device = db.scalar(select(Device).where(
        Device.user_id == user.id,
        Device.device_fingerprint_hash == fp_hash,
    ))
    if device:
        if device.status == "revoked":
            raise LicenseError("该设备已被解绑，请联系管理员恢复")
        device.last_seen_at = utc_now()
        if device_name:
            device.device_name = device_name[:128]
        db.commit()
        db.refresh(device)
        return device
    active_count = db.scalar(
        select(Device).where(Device.user_id == user.id,
                             Device.status == "active")
    )
    # 简化计数
    from sqlalchemy import func
    count = db.scalar(
        select(func.count()).select_from(Device).where(
            Device.user_id == user.id, Device.status == "active")
    )
    if count >= settings.max_devices:
        raise LicenseError(
            f"已达到设备数量上限（{settings.max_devices}），"
            "请先解绑不使用的设备")
    device = Device(
        user_id=user.id,
        device_fingerprint_hash=fp_hash,
        device_name=(device_name or "未命名设备")[:128],
        status="active",
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


def deactivate_device(db: Session, user: User, device_id: int) -> bool:
    device = db.get(Device, device_id)
    if not device or device.user_id != user.id:
        raise LicenseError("设备不存在")
    if device.status == "revoked":
        return True
    device.status = "revoked"
    device.revoked_at = utc_now()
    # 撤销该设备上的授权
    db.query(License).where(
        License.device_id == device_id,
        License.status == "active",
    ).update({"status": "revoked", "revoked_at": utc_now()})
    db.commit()
    return True


def list_devices(db: Session, user: User) -> list[Device]:
    from sqlalchemy.orm import selectinload
    return list(db.scalars(
        select(Device).where(Device.user_id == user.id)
        .order_by(Device.id)
    ))


def issue_license(db: Session, user: User, device: Device) -> dict:
    """为有效授权状态用户签发凭证，并落库 License 记录。"""
    status = effective_status(db, user)
    if status not in ("trialing", "active"):
        raise LicenseError(f"当前授权状态为 {status}，无法签发授权")
    sub = get_subscription(db, user)
    plan_code = sub.plan.plan_code if sub and sub.plan else None
    if sub:
        expires_at = sub.expires_at
    else:
        from .trial import get_trial
        trial = get_trial(db, user)
        expires_at = trial.expires_at if trial else utc_now() + timedelta(days=1)
    credential = issue_credential(
        user_id=user.id,
        subscription_status=status,
        plan_code=plan_code,
        expires_at=expires_at,
        device_id=device.id,
    )
    license_rec = License(
        license_id=credential["license_id"],
        user_id=user.id,
        device_id=device.id,
        subscription_status=status,
        plan_code=plan_code,
        expires_at=expires_at,
        status="active",
    )
    db.add(license_rec)
    db.commit()
    return {
        "credential": credential,
        "offline_grace_hours": settings.offline_grace_hours,
        "server_time": utc_now().isoformat() + "Z",
    }


def validate_license(db: Session, user: User, *, credential: dict) -> dict:
    """联网验证授权凭证。

    核对：签名由客户端本地完成；服务端核对 license 记录状态、
    订阅/试用状态与设备绑定。撤销或过期即拒绝。
    """
    payload = credential.get("payload") if isinstance(credential, dict) else None
    if not payload or "license_id" not in payload:
        raise LicenseError("授权凭证结构非法")
    lic = db.scalar(select(License).where(
        License.license_id == payload["license_id"]))
    if not lic or lic.user_id != user.id:
        raise LicenseError("授权记录不存在")
    if lic.status == "revoked":
        raise LicenseError("授权已被撤销")
    # 设备绑定核对
    device_id = payload.get("device_id")
    if lic.device_id and device_id != lic.device_id:
        raise LicenseError("授权与设备不匹配")
    status = effective_status(db, user)
    if status not in ("trialing", "active"):
        raise LicenseError(f"当前授权状态为 {status}，授权已失效")
    # 服务端时间到达即拒绝（客户端改本地时间无效）
    if lic.expires_at < utc_now():
        lic.status = "expired"
        db.commit()
        raise LicenseError("授权已到期")
    lic.last_validated_at = utc_now()
    db.commit()
    return {
        "valid": True,
        "license_id": lic.license_id,
        "subscription_status": status,
        "expires_at": lic.expires_at.isoformat() + "Z",
        "server_time": utc_now().isoformat() + "Z",
        "offline_grace_hours": settings.offline_grace_hours,
    }


def revoke_license(db: Session, license_id: str, *, reason: str) -> bool:
    if not reason or not reason.strip():
        raise LicenseError("撤销授权必须填写原因")
    lic = db.scalar(select(License).where(License.license_id == license_id))
    if not lic:
        raise LicenseError("授权记录不存在")
    lic.status = "revoked"
    lic.revoked_at = utc_now()
    db.commit()
    return True


def offline_deadline(last_validated_at, grace_hours=None) -> "datetime":
    """离线宽限截止时间 = 最近一次成功验证 + 宽限。"""
    grace = grace_hours if grace_hours is not None else settings.offline_grace_hours
    return last_validated_at + timedelta(hours=grace)


def generate_nonce() -> str:
    return secrets.token_hex(16)
