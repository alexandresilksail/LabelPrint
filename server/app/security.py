"""服务端安全原语：argon2id 密码哈希、随机令牌、限流器。

- 密码：argon2id（argon2-cffi），不明文存储、不入日志；
- 会话/验证/重置令牌：URL 安全随机数，库内仅存 SHA-256 哈希；
- 限流：可切换后端——
  * `memory`：进程内固定窗口，**只在单 worker 下有效**；
  * `redis`：共享存储固定窗口（INCR + EXPIRE），多 worker / 多实例一致。
  生产多 worker 若仍用 memory，配置校验会拒绝启动（见 app/config.py）；
- 时间统一使用 naive UTC（与 models.utcnow 一致，避免 aware/naive 混用）。
"""

import hashlib
import hmac
import logging
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher, exceptions as argon2_exceptions

logger = logging.getLogger("codelabel.security")

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
    """naive UTC（与数据库 DateTime 列一致，禁止 aware/naive 混用）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- 限流 ----------
class InMemoryRateLimiter:
    """固定窗口限流器（线程安全，仅进程内）。

    多 worker 部署时每个进程独立计数，实际配额 = 配置值 × worker 数，
    因此生产多 worker 必须改用 RedisRateLimiter。
    """

    backend = "memory"

    def __init__(self):
        self._hits: dict[str, list[float]] = {}
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


class RedisRateLimiter:
    """基于 Redis 的固定窗口限流器，多 worker / 多实例共享计数。

    客户端只要求实现 `incr` / `expire` / `delete` / `scan_iter`，
    因此可以注入假客户端做单元测试，无需真实 Redis 实例。

    Redis 不可用时的行为由 `fail_open` 决定：默认 False（失败关闭），
    宁可拒绝请求也不让暴力破解在限流器故障期间畅通无阻。
    """

    backend = "redis"

    def __init__(self, client, *, prefix: str = "cls:rl:",
                 fail_open: bool = False):
        self._client = client
        self._prefix = prefix
        self._fail_open = fail_open

    def check(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        bucket = int(time.time() // window_seconds)
        redis_key = f"{self._prefix}{key}:{bucket}"
        try:
            count = int(self._client.incr(redis_key))
            if count == 1:
                # 窗口过期后自动清理，多留一点余量避免边界抖动丢计数
                self._client.expire(redis_key, window_seconds + 5)
        except Exception as exc:                     # noqa: BLE001 - 后端故障需兜底
            logger.error("限流后端不可用（key=%s）：%s", key, exc)
            return self._fail_open
        return count <= limit

    def reset(self, key: str | None = None):
        pattern = f"{self._prefix}{key}:*" if key else f"{self._prefix}*"
        try:
            stale = list(self._client.scan_iter(pattern))
            if stale:
                self._client.delete(*stale)
        except Exception as exc:                     # noqa: BLE001
            logger.warning("限流键清理失败：%s", exc)


def create_redis_client(url: str):
    """构造 Redis 客户端；未安装 redis 包时给出可执行的修复提示。"""
    try:
        import redis  # 延迟导入：开发环境不必安装
    except ImportError as exc:                       # pragma: no cover - 依赖缺失
        raise RuntimeError(
            "CLS_RATE_LIMIT_BACKEND=redis 需要安装 redis 包："
            "pip install 'redis>=5.0'") from exc
    return redis.Redis.from_url(url, decode_responses=True)


def build_rate_limiter(settings):
    """按配置选择限流后端。生产多 worker + memory 已在配置校验阶段拦截。"""
    if getattr(settings, "rate_limit_backend", "memory") == "redis":
        client = create_redis_client(settings.redis_url)
        return RedisRateLimiter(client,
                                fail_open=getattr(settings, "rate_limit_fail_open", False))
    return InMemoryRateLimiter()


# 兼容旧引用：RateLimiter 即内存实现
RateLimiter = InMemoryRateLimiter


from .config import settings  # noqa: E402  （避免循环导入放到末尾）
rate_limiter = build_rate_limiter(settings)


def expires_at(seconds_from_now: int) -> datetime:
    return utc_now() + timedelta(seconds=seconds_from_now)


def is_expired(dt: datetime | None) -> bool:
    return dt is None or dt <= utc_now()
