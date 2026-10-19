"""授权签名与验证（规格第十一章）。

- 授权私钥仅保存在服务端（环境变量 CLS_LICENSE_PRIVATE_KEY 或
  data/license_private.pem，权限 600）；客户端仅持有公钥；
- 凭证 = canonical JSON 载荷 + Ed25519 签名（base64）；
- 支持服务端撤销：revoke 后 /api/license/validate 拒绝（离线宽限
  不延长已撤销授权）；
- 时间一律 UTC ISO 8601。
"""

import base64
import hashlib
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

from .config import LICENSE_KEY_FILE, settings

_KEY_FILE = LICENSE_KEY_FILE


def key_path() -> Path:
    """私钥文件路径：环境变量可覆盖（容器挂载 secret 的常见做法）。"""
    override = getattr(settings, "license_key_file_override", "")
    return Path(override) if override else _KEY_FILE


def canonical_payload(payload: dict) -> bytes:
    """规范化 JSON（键排序、无空白），保证签名可重现。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _load_or_create_private_key() -> Ed25519PrivateKey:
    hex_key = (settings.license_private_key_hex or "").strip()
    if hex_key:
        try:
            raw = bytes.fromhex(hex_key)
        except ValueError as exc:
            raise RuntimeError(
                "CLS_LICENSE_PRIVATE_KEY 不是合法的十六进制字符串") from exc
        if len(raw) != 32:
            raise RuntimeError(
                f"CLS_LICENSE_PRIVATE_KEY 必须为 32 字节（64 个 hex 字符），"
                f"当前为 {len(raw)} 字节")
        return Ed25519PrivateKey.from_private_bytes(raw)
    path = key_path()
    if path.exists():
        data = path.read_bytes()
        try:
            return serialization.load_pem_private_key(data, password=None)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                f"授权私钥文件无法解析：{path}") from exc
    if settings.is_production:
        # 生产绝不静默生成：否则每次重启/新容器都会换一把密钥，
        # 已签发的用户授权全部验签失败，而服务仍然"正常"对外服务。
        raise RuntimeError(
            "生产环境未配置授权私钥：请设置 CLS_LICENSE_PRIVATE_KEY"
            f"（32 字节 hex）或提供 {path}。拒绝自动生成新密钥。")
    # 开发环境：生成并落盘（重启后保持稳定，便于本地联调）
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
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


def key_fingerprint() -> str:
    """公钥指纹（SHA-256 前 16 位 hex）。

    多 worker / 多容器部署时，各实例日志与 /health 都应输出同一指纹；
    不一致说明某个实例用了不同的私钥，签发的凭证会互不认账。
    """
    digest = hashlib.sha256(get_public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )).hexdigest()
    return digest[:16]


def key_source() -> str:
    """私钥来源：env | file（用于启动日志排查，不暴露密钥内容）。"""
    if (settings.license_private_key_hex or "").strip():
        return "env"
    if key_path().exists():
        return "file"
    return "generated"


def self_test() -> None:
    """启动自检：签名/验签闭环。失败说明密钥损坏或环境异常，拒绝启动。"""
    probe = {"probe": "codeLabel", "ts": _utc_iso(
        datetime.now(timezone.utc).replace(tzinfo=None))}
    signature = sign_payload(probe)
    if not verify_payload(public_key_b64(), probe, signature):
        raise RuntimeError("授权签名自检失败：私钥/公钥不匹配")
    if not verify_payload(public_key_b64(), {**probe, "probe": "tampered"},
                          signature):
        return
    raise RuntimeError("授权签名自检失败：篡改载荷仍然验签通过")


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
