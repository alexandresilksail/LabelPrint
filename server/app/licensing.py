"""授权签名与验证（规格第十一章）。

- 授权私钥仅保存在服务端（环境变量 CLS_LICENSE_PRIVATE_KEY 或
  data/license_private.pem，权限 600）；客户端仅持有公钥；
- 凭证 = canonical JSON 载荷 + Ed25519 签名（base64）；
- 支持服务端撤销：revoke 后 /api/license/validate 拒绝（离线宽限
  不延长已撤销授权）；
- 时间一律 UTC ISO 8601。
"""

import base64
import json
import os
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

from .config import settings

_KEY_FILE = Path(__file__).resolve().parents[2] / "data" / "license_private.pem"


def canonical_payload(payload: dict) -> bytes:
    """规范化 JSON（键排序、无空白），保证签名可重现。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _load_or_create_private_key() -> Ed25519PrivateKey:
    hex_key = settings.license_private_key_hex
    if hex_key:
        raw = bytes.fromhex(hex_key.strip())
        return Ed25519PrivateKey.from_private_bytes(raw)
    if _KEY_FILE.exists():
        data = _KEY_FILE.read_bytes()
        return serialization.load_pem_private_key(data, password=None)
    # 开发环境：生成并落盘（生产必须通过环境变量注入）
    key = Ed25519PrivateKey.generate()
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _KEY_FILE.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        os.chmod(_KEY_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return key


_private_key = None
_public_key = None


def get_private_key() -> Ed25519PrivateKey:
    global _private_key
    if _private_key is None:
        _private_key = _load_or_create_private_key()
    return _private_key


def get_public_key() -> Ed25519PublicKey:
    global _public_key
    if _public_key is None:
        _public_key = get_private_key().public_key()
    return _public_key


def public_key_b64() -> str:
    """客户端内置的公钥（base64，RawEncoding 32 字节）。"""
    raw = get_public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def sign_payload(payload: dict) -> str:
    """对规范化载荷签名，返回 base64 签名。"""
    sig = get_private_key().sign(canonical_payload(payload))
    return base64.b64encode(sig).decode("ascii")


def verify_payload(public_key_b64: str, payload: dict, signature_b64: str) -> bool:
    """客户端验签。任何结构/签名问题都返回 False（不抛异常）。"""
    try:
        raw = base64.b64decode(public_key_b64, validate=True)
        sig = base64.b64decode(signature_b64, validate=True)
        key = Ed25519PublicKey.from_public_bytes(raw)
        key.verify(sig, canonical_payload(payload))
        return True
    except (ValueError, InvalidSignature):
        return False


def _utc_iso(dt: datetime) -> str:
    """naive UTC -> ISO 8601（显式 Z 后缀）。

    expires_at 等库内时间为 naive UTC，直接 astimezone 会按本地时区
    解释导致偏移错误，故手动拼接 Z。
    """
    return dt.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")


def issue_credential(*, user_id: int, subscription_status: str,
                     plan_code: str | None, expires_at: datetime,
                     device_id: int | None = None) -> dict:
    """签发授权凭证。返回 {license_id, payload, signature}。"""
    license_id = str(uuid.uuid4())
    payload = {
        "license_id": license_id,
        "user_id": user_id,
        "subscription_status": subscription_status,
        "plan_code": plan_code,
        "device_id": device_id,
        "issued_at": _utc_iso(datetime.now(timezone.utc).replace(tzinfo=None)),
        "expires_at": _utc_iso(expires_at),
    }
    return {
        "license_id": license_id,
        "payload": payload,
        "signature": sign_payload(payload),
    }


def parse_credential(credential: dict) -> tuple[dict, str]:
    """客户端解析凭证。"""
    payload = credential.get("payload")
    signature = credential.get("signature")
    if not isinstance(payload, dict) or not signature:
        raise ValueError("授权凭证结构非法")
    return payload, signature
