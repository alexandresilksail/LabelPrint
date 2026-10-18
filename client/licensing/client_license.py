"""客户端授权：凭证验签、离线宽限、服务端通信。

设计：
- 授权凭证由服务端 Ed25519 私钥签发；客户端首次联网时获取并固定
  （pin）服务端公钥（TOFU），此后可完全离线验签；
- 离线可用性 = 凭证未过期 且 未撤销宽限 且 最近一次成功验证在宽限期内；
- 撤销不延长宽限： revoked 立即失效；
- 本地时间可被篡改，因此宽限判断以"最近一次服务端验证时间"为锚，
  客户端只做缓存展示，权威状态始终由服务端 validate 决定。
"""

import json
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_GRACE_HOURS = 72


class LicenseClientError(Exception):
    """客户端授权错误。"""


@dataclass
class CachedCredential:
    """本地缓存的授权状态。"""

    credential: dict = field(default_factory=dict)
    public_key_b64: str = ""
    last_validated_at: str = ""     # ISO 8601 UTC（来自服务端响应）
    revoked: bool = False

    def to_dict(self) -> dict:
        return {
            "credential": self.credential,
            "public_key_b64": self.public_key_b64,
            "last_validated_at": self.last_validated_at,
            "revoked": self.revoked,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CachedCredential":
        return cls(
            credential=data.get("credential") or {},
            public_key_b64=data.get("public_key_b64") or "",
            last_validated_at=data.get("last_validated_at") or "",
            revoked=bool(data.get("revoked")),
        )


class LicenseStore:
    """授权缓存持久化（JSON 文件，权限 600）。"""

    def __init__(self, path):
        self.path = Path(path)

    def load(self) -> CachedCredential:
        if not self.path.exists():
            return CachedCredential()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return CachedCredential.from_dict(data)
        except (OSError, ValueError):
            return CachedCredential()

    def save(self, cached: CachedCredential) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(cached.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def verify_credential(credential: dict, public_key_b64: str) -> bool:
    """离线验签（复用服务端 licensing 的规范化逻辑）。

    客户端与服务端共享 canonical JSON 规则，
    保证签名字节完全一致。
    """
    payload = credential.get("payload")
    signature = credential.get("signature")
    if not isinstance(payload, dict) or not signature or not public_key_b64:
        return False
    import base64
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )

    def canonical(p: dict) -> bytes:
        return json.dumps(p, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")

    try:
        raw = base64.b64decode(public_key_b64, validate=True)
        sig = base64.b64decode(signature, validate=True)
        key = Ed25519PublicKey.from_public_bytes(raw)
        key.verify(sig, canonical(payload))
        return True
    except (ValueError, InvalidSignature):
        return False


def offline_status(cached: CachedCredential, *, now: datetime | None = None,
                   grace_hours: int = DEFAULT_GRACE_HOURS) -> dict:
    """离线状态评估（仅基于本地缓存）。

    返回 dict：
        entitled   是否可继续使用受保护功能
        reason     判定依据（ok / no_credential / invalid_signature /
                   revoked / expired / grace_expired / not_yet_valid）
        expires_at 凭证到期时间（ISO）
        grace_deadline 离线宽限截止（ISO）
    注意：这是离线兜底；联网后应以服务端 validate 结果为准。
    """
    now = now or datetime.now(timezone.utc)
    credential = cached.credential
    if not credential:
        return {"entitled": False, "reason": "no_credential",
                "expires_at": None, "grace_deadline": None}
    if cached.revoked:
        return {"entitled": False, "reason": "revoked",
                "expires_at": None, "grace_deadline": None}
    if not verify_credential(credential, cached.public_key_b64):
        return {"entitled": False, "reason": "invalid_signature",
                "expires_at": None, "grace_deadline": None}

    payload = credential.get("payload") or {}
    expires = _parse_iso(payload.get("expires_at", ""))
    issued = _parse_iso(payload.get("issued_at", ""))
    if expires is None:
        return {"entitled": False, "reason": "expired",
                "expires_at": None, "grace_deadline": None}
    if issued is not None and issued > now + timedelta(minutes=5):
        return {"entitled": False, "reason": "not_yet_valid",
                "expires_at": expires.isoformat(), "grace_deadline": None}

    last_validated = _parse_iso(cached.last_validated_at)
    grace_deadline = None
    if last_validated is not None:
        grace_deadline = last_validated + timedelta(hours=grace_hours)

    if now < expires:
        # 未到期：直接可用（无论是否在宽限内）
        return {"entitled": True, "reason": "ok",
                "expires_at": expires.isoformat(),
                "grace_deadline": grace_deadline.isoformat()
                if grace_deadline else None}
    # 已到期：仅在宽限期内可用
    if grace_deadline is not None and now <= grace_deadline:
        return {"entitled": True, "reason": "grace",
                "expires_at": expires.isoformat(),
                "grace_deadline": grace_deadline.isoformat()}
    return {"entitled": False, "reason": "grace_expired",
            "expires_at": expires.isoformat(),
            "grace_deadline": grace_deadline.isoformat()
            if grace_deadline else None}


class LicenseApiClient:
    """服务端授权 API 客户端（stdlib urllib，无额外依赖）。"""

    # 仅允许 http/https：拒绝 file:/ftp: 等本地/明文协议
    _ALLOWED_SCHEMES = ("http", "https")

    def __init__(self, base_url: str, timeout: float = 15.0):
        self._validate_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _validate_url(self, url: str) -> None:
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in self._ALLOWED_SCHEMES:
            raise LicenseClientError(
                f"不允许的 URL 协议: {parsed.scheme or '(缺失)'}")
        if not parsed.hostname:
            raise LicenseClientError("URL 缺少主机名")

    def _request(self, method: str, path: str, token: str | None = None,
                 body: dict | None = None) -> dict:
        import urllib.error
        import urllib.request
        url = f"{self.base_url}{path}"
        self._validate_url(url)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(  # nosec B310 - 已限定 http/https
                    req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get(
                    "detail", str(exc))
            except (ValueError, OSError):
                detail = str(exc)
            raise LicenseClientError(f"服务端返回 {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LicenseClientError(f"无法连接服务端: {exc.reason}") from exc

    def public_key(self) -> str:
        data = self._request("GET", "/api/license/public-key")
        return data["public_key_b64"]

    def activate_device(self, token: str, fingerprint: str,
                        device_name: str = "") -> dict:
        return self._request("POST", "/api/license/activate-device",
                             token=token,
                             body={"fingerprint": fingerprint,
                                   "device_name": device_name})

    def issue(self, token: str, fingerprint: str) -> dict:
        return self._request("POST", "/api/license/issue", token=token,
                             body={"fingerprint": fingerprint,
                                   "device_name": ""})

    def validate(self, token: str, credential: dict) -> dict:
        return self._request("POST", "/api/license/validate", token=token,
                             body={"credential": credential})
