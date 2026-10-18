"""服务端 API 客户端：注册/登录/验证/账户/授权（stdlib urllib，零依赖）。"""

import json
import platform
import urllib.error
import urllib.parse
import urllib.request
import uuid

# 仅允许 http/https：拒绝 file:/ftp: 等本地/明文协议，防止 SSRF 与本地文件读取
ALLOWED_SCHEMES = ("http", "https")


class ApiError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def _validate_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ApiError(f"不允许的 URL 协议: {parsed.scheme or '(缺失)'}")
    if not parsed.hostname:
        raise ApiError("URL 缺少主机名")


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 15.0):
        _validate_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method, path, token=None, body=None):
        url = f"{self.base_url}{path}"
        _validate_url(url)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(  # nosec B310 - 已限定 http/https
                    req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8")
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get(
                    "detail", str(exc))
            except (ValueError, OSError):
                detail = str(exc)
            raise ApiError(f"服务端返回 {exc.code}: {detail}",
                           status_code=exc.code) from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"无法连接服务端: {exc.reason}") from exc

    # ---------- 认证 ----------
    def register(self, email, username, password, accept_terms=True):
        return self._request("POST", "/api/auth/register", body={
            "email": email, "username": username, "password": password,
            "accept_terms": accept_terms})

    def verify_email(self, token):
        return self._request("POST", "/api/auth/verify-email",
                             body={"token": token})

    def resend_verification(self, email):
        return self._request("POST", "/api/auth/resend-verification",
                             body={"email": email})

    def login(self, identifier, password):
        return self._request("POST", "/api/auth/login", body={
            "identifier": identifier, "password": password})

    def logout(self, token):
        return self._request("POST", "/api/auth/logout", token=token)

    def change_password(self, token, old_password, new_password):
        return self._request("POST", "/api/auth/change-password", token=token,
                             body={"old_password": old_password,
                                   "new_password": new_password})

    # ---------- 账户 ----------
    def me(self, token):
        return self._request("GET", "/api/me", token=token)

    def my_subscription(self, token):
        return self._request("GET", "/api/me/subscription", token=token)

    def my_devices(self, token):
        return self._request("GET", "/api/me/devices", token=token)

    def deactivate_device(self, token, device_id):
        return self._request(
            "POST", "/api/me/devices/revoke", token=token,
            body={"device_id": device_id})

    def plans(self):
        return self._request("GET", "/api/plans")

    def license_public_key(self):
        return self._request("GET", "/api/license/public-key")

    def activate_device(self, token, fingerprint, device_name=""):
        return self._request(
            "POST", "/api/license/activate-device", token=token,
            body={"fingerprint": fingerprint, "device_name": device_name})

    def issue_license(self, token, fingerprint):
        return self._request("POST", "/api/license/issue", token=token,
                             body={"fingerprint": fingerprint,
                                   "device_name": ""})

    def validate_license(self, token, credential):
        return self._request("POST", "/api/license/validate", token=token,
                             body={"credential": credential})

    # ---------- 订单 ----------
    def create_order(self, token, plan_code):
        return self._request("POST", "/api/orders", token=token,
                             body={"plan_code": plan_code})

    def my_orders(self, token):
        return self._request("GET", "/api/orders", token=token)

    def submit_payment_proof(self, token, order_id, reference):
        return self._request(
            "POST", f"/api/orders/{order_id}/payment-proof", token=token,
            body={"reference": reference})


def device_fingerprint() -> str:
    """稳定的设备指纹：主机名 + MAC + 系统信息哈希（不采集个人数据）。"""
    import hashlib
    raw = "|".join([
        platform.node(), str(uuid.getnode()), platform.system(),
        platform.machine(), platform.version(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
