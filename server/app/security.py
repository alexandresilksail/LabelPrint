"""服务端安全原语：Argon2id 密码哈希、随机令牌、限流器。

- 密码：Argon2id（argon2-cffi），不明文存储、不入日志；
- 会话/验证/重置令牌：URL 安全随机数，库内仅存 SHA-256 哈希；
- 限流：进程内固定窗口（按 IP + 路由），生产建议改用 Redis。
"""

import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher, exceptions as argon2_exceptions

_hasher = PasswordHasher()


# ---------- 密码 ----------
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (argon2_exceptions.VerifyMismatchError,
            argon2_exceptions.VerificationError,
            argon2_exceptions.InvalidHash):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except argon2_exceptions.InvalidHash:
        return True


# ---------- 令牌 ----------
def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """令牌仅存哈希；验证时比较哈希。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- 限流 ----------
class RateLimiter:
    """固定窗口限流器（线程安全）。"""

    def __init__(self):
        self._hits = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        """返回 True 表示允许；False 表示超限。"""
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < window_seconds]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def reset(self, key: str | None = None):
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


rate_limiter = RateLimiter()


def expires_at(seconds_from_now: int) -> datetime:
    return utc_now() + timedelta(seconds=seconds_from_now)


def is_expired(dt: datetime | None) -> bool:
    return dt is None or dt <= utc_now()
