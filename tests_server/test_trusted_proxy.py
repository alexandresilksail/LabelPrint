"""审计问题 1：限流 IP 信任、伪造转发头、多 worker 配置。

覆盖：
- 无可信代理时，X-Forwarded-For / X-Real-IP 一律忽略；
- 可信代理链从右往左剥离，客户端伪造最左项无效；
- 非法头部值失败关闭，回退连接对端；
- 登录/注册限流不能被伪造头绕过；
- 多 worker + 内存限流被配置校验拒绝；Redis 后端共享计数可验证。
"""

import unittest
import unittest.mock
from pathlib import Path

from starlette.requests import Request

from server.app import config as config_module
from server.app.config import Settings
from server.app.netutil import (
    is_trusted, normalize_ip, parse_networks, resolve_client_ip,
)
from server.app.security import InMemoryRateLimiter, RedisRateLimiter
from server.api.deps import request_client_ip

from .base import ServerTestCase


def make_request(peer="127.0.0.1", headers=None) -> Request:
    raw = []
    for name, value in (headers or {}).items():
        raw.append((name.lower().encode(), str(value).encode()))
    return Request({
        "type": "http", "method": "GET", "path": "/", "headers": raw,
        "client": (peer, 12345) if peer else None,
        "query_string": b"", "server": ("testserver", 80), "scheme": "http",
    })


class NetutilUnitTests(unittest.TestCase):
    def test_parse_networks_rejects_garbage(self):
        self.assertEqual(len(parse_networks(["10.0.0.0/8", "192.168.1.5"])), 2)
        with self.assertRaises(ValueError):
            parse_networks(["not-an-ip"])

    def test_normalize_ip_variants(self):
        self.assertEqual(normalize_ip("192.0.2.7"), "192.0.2.7")
        self.assertEqual(normalize_ip("192.0.2.7:5555"), "192.0.2.7")
        self.assertEqual(normalize_ip("[2001:db8::1]:443"), "2001:db8::1")
        self.assertEqual(normalize_ip("::ffff:192.0.2.7"), "::ffff:192.0.2.7")
        self.assertIsNone(normalize_ip("evil.example.com"))
        self.assertIsNone(normalize_ip(""))

    def test_untrusted_peer_ignores_headers(self):
        """核心修复点：不是可信代理时，转发头完全不可信。"""
        ip = resolve_client_ip("203.0.113.9",
                               forwarded_for="1.2.3.4",
                               real_ip="5.6.7.8",
                               trusted=parse_networks(["10.0.0.0/8"]))
        self.assertEqual(ip, "203.0.113.9")

    def test_no_trusted_config_ignores_headers(self):
        ip = resolve_client_ip("203.0.113.9", "1.2.3.4", "5.6.7.8", ())
        self.assertEqual(ip, "203.0.113.9")

    def test_trusted_proxy_uses_forwarded_for(self):
        ip = resolve_client_ip("10.0.0.5", "198.51.100.7", None,
                               parse_networks(["10.0.0.0/8"]))
        self.assertEqual(ip, "198.51.100.7")

    def test_spoofed_leftmost_entry_is_ignored(self):
        """攻击者在最左侧伪造 IP，真实客户端地址应取最右侧不可信项。"""
        chain = "1.1.1.1, 198.51.100.7, 10.0.0.5"
        ip = resolve_client_ip("10.0.0.5", chain, None,
                               parse_networks(["10.0.0.0/8"]))
        self.assertEqual(ip, "198.51.100.7")

    def test_multi_hop_trusted_chain(self):
        chain = "198.51.100.7, 10.0.0.6, 10.0.0.5"
        ip = resolve_client_ip("10.0.0.5", chain, None,
                               parse_networks(["10.0.0.0/8"]))
        self.assertEqual(ip, "198.51.100.7")

    def test_invalid_header_fails_closed(self):
        ip = resolve_client_ip("10.0.0.5", "198.51.100.7, garbage", None,
                               parse_networks(["10.0.0.0/8"]))
        self.assertEqual(ip, "10.0.0.5")

    def test_real_ip_used_when_no_forwarded_for(self):
        ip = resolve_client_ip("10.0.0.5", None, "198.51.100.9",
                               parse_networks(["10.0.0.0/8"]))
        self.assertEqual(ip, "198.51.100.9")

    def test_unknown_peer(self):
        self.assertEqual(resolve_client_ip(None, "1.2.3.4", None, ()), "unknown")

    def test_is_trusted(self):
        nets = parse_networks(["10.0.0.0/8"])
        self.assertTrue(is_trusted("10.1.2.3", nets))
        self.assertFalse(is_trusted("11.1.2.3", nets))
        self.assertFalse(is_trusted("nonsense", nets))


class ClientIpEndpointTests(unittest.TestCase):
    """直接测试依赖函数，验证配置切换后的行为。"""

    def tearDown(self):
        config_module.settings.trusted_proxies_raw = []
        config_module.settings._trusted_networks = None

    def test_default_config_ignores_spoofed_headers(self):
        config_module.settings.trusted_proxies_raw = []
        config_module.settings._trusted_networks = None
        req = make_request("203.0.113.9", {
            "x-forwarded-for": "1.2.3.4",
            "x-real-ip": "5.6.7.8",
        })
        self.assertEqual(request_client_ip(req), "203.0.113.9")

    def test_trusted_proxy_header_honored(self):
        config_module.settings.trusted_proxies_raw = ["10.0.0.0/8"]
        config_module.settings._trusted_networks = None
        req = make_request("10.0.0.5", {"x-forwarded-for": "198.51.100.7"})
        self.assertEqual(request_client_ip(req), "198.51.100.7")

    def test_spoof_outside_trusted_range(self):
        config_module.settings.trusted_proxies_raw = ["10.0.0.0/8"]
        config_module.settings._trusted_networks = None
        req = make_request("203.0.113.9", {"x-forwarded-for": "198.51.100.7"})
        self.assertEqual(request_client_ip(req), "203.0.113.9")


class RateLimitBypassTests(ServerTestCase):
    """端到端：伪造转发头不能让登录/注册限流失效。"""

    def test_login_limit_not_bypassed_by_rotating_xff(self):
        self.register_verified()
        limit = config_module.settings.rate_limit_login
        statuses = []
        for i in range(limit + 2):
            r = self.client.post(
                "/api/auth/login",
                json={"identifier": "user1", "password": "WrongPass123!"},
                headers={"X-Forwarded-For": f"198.51.100.{i}",
                         "X-Real-IP": f"203.0.113.{i}"},
            )
            statuses.append(r.status_code)
        self.assertIn(429, statuses,
                      f"伪造 XFF 绕过了登录限流：{statuses}")
        self.assertEqual(statuses[-1], 429)

    def test_register_limit_not_bypassed_by_rotating_xff(self):
        limit = config_module.settings.rate_limit_register
        last = None
        for i in range(limit + 2):
            last = self.client.post(
                "/api/auth/register",
                json={"email": f"x{i}@example.com", "username": f"user{i}",
                      "password": "Passw0rd!", "accept_terms": True},
                headers={"X-Forwarded-For": f"198.51.100.{i}"},
            )
        self.assertEqual(last.status_code, 429)

    def test_headers_ignored_when_peer_not_trusted(self):
        """测试客户端对端为 testclient（非 IP），必须归一到 unknown 同一桶。"""
        req = make_request("testclient", {"x-forwarded-for": "1.2.3.4"})
        self.assertEqual(request_client_ip(req), "unknown")


class SharedLimiterTests(unittest.TestCase):
    class FakeRedis:
        def __init__(self):
            self.store = {}
            self.ttl = {}

        def incr(self, key):
            self.store[key] = self.store.get(key, 0) + 1
            return self.store[key]

        def expire(self, key, seconds):
            self.ttl[key] = seconds
            return True

        def scan_iter(self, pattern):
            prefix = pattern.rstrip("*")
            return [k for k in list(self.store) if k.startswith(prefix)]

        def delete(self, *keys):
            for k in keys:
                self.store.pop(k, None)
            return len(keys)

    def test_redis_limiter_shares_counter(self):
        fake = self.FakeRedis()
        a = RedisRateLimiter(fake)
        b = RedisRateLimiter(fake)          # 模拟第二个 worker
        self.assertTrue(a.check("login:1.2.3.4", 2))
        self.assertTrue(b.check("login:1.2.3.4", 2))
        self.assertFalse(a.check("login:1.2.3.4", 2))
        self.assertFalse(b.check("login:1.2.3.4", 2))
        self.assertTrue(fake.ttl)

    def test_redis_limiter_fail_closed_by_default(self):
        class Broken:
            def incr(self, key):
                raise ConnectionError("redis down")

            def expire(self, key, seconds):
                raise ConnectionError("redis down")

        self.assertFalse(RedisRateLimiter(Broken()).check("login:x", 5))

    def test_redis_limiter_can_fail_open_when_configured(self):
        class Broken:
            def incr(self, key):
                raise ConnectionError("redis down")

        self.assertTrue(
            RedisRateLimiter(Broken(), fail_open=True).check("login:x", 5))

    def test_memory_limiter_is_per_process(self):
        """内存限流器两个实例互不可见——这正是多 worker 会放大配额的原因。"""
        a, b = InMemoryRateLimiter(), InMemoryRateLimiter()
        for _ in range(2):
            self.assertTrue(a.check("k", 2))
        self.assertTrue(b.check("k", 2))
        self.assertFalse(a.check("k", 2))

    def test_redis_limiter_reset(self):
        fake = self.FakeRedis()
        limiter = RedisRateLimiter(fake)
        limiter.check("login:1.1.1.1", 5)
        limiter.reset()
        self.assertEqual(fake.store, {})


class MultiWorkerConfigTests(unittest.TestCase):
    """配置校验：多 worker + 内存限流必须拒绝启动。"""

    BASE_ENV = {
        "CLS_ENV": "production",
        "CLS_SECRET_KEY": "p" * 48,
        "CLS_DATABASE_URL": "postgresql+psycopg://u:p@db:5432/cls",
        "CLS_MAIL_BACKEND": "smtp",
        "CLS_SMTP_HOST": "smtp.example.com",
        "CLS_SMTP_USER": "mailer@example.com",
        "CLS_SMTP_PASSWORD": "smtp-secret",
        "CLS_MAIL_FROM": "noreply@example.com",
        "CLS_CORS_ORIGINS": "https://app.example.com",
        "CLS_APP_BASE_URL": "https://api.example.com",
        "CLS_LICENSE_PRIVATE_KEY": "ab" * 32,
        "CLS_RATE_LIMIT_BACKEND": "redis",
        "CLS_REDIS_URL": "redis://redis:6379/0",
        "CLS_WORKERS": "1",
    }

    def build(self, **overrides) -> Settings:
        env = {**self.BASE_ENV, **overrides}
        with unittest.mock.patch.dict("os.environ", env, clear=True), \
                unittest.mock.patch.object(config_module, "LICENSE_KEY_FILE",
                                           Path("C:/definitely/not/here.pem")):
            # validate=False：本组测试就是要断言"错误配置会报哪些错"
            return Settings(validate=False)

    def test_memory_limiter_with_multi_worker_rejected(self):
        settings = self.build(CLS_RATE_LIMIT_BACKEND="memory", CLS_WORKERS="4")
        errors = settings.production_errors()
        self.assertTrue(any("worker" in e for e in errors), errors)

    def test_memory_limiter_single_worker_allowed(self):
        settings = self.build(CLS_RATE_LIMIT_BACKEND="memory", CLS_WORKERS="1")
        self.assertEqual(settings.production_errors(), [])

    def test_redis_backend_requires_url(self):
        settings = self.build(CLS_REDIS_URL="")
        errors = settings.production_errors()
        self.assertTrue(any("CLS_REDIS_URL" in e for e in errors), errors)

    def test_behind_proxy_requires_trusted_list(self):
        settings = self.build(CLS_BEHIND_PROXY="1", CLS_TRUSTED_PROXIES="")
        errors = settings.production_errors()
        self.assertTrue(any("CLS_TRUSTED_PROXIES" in e for e in errors), errors)

    def test_behind_proxy_with_trusted_list_ok(self):
        settings = self.build(CLS_BEHIND_PROXY="1",
                              CLS_TRUSTED_PROXIES="10.0.0.0/8,172.16.0.1")
        self.assertEqual(settings.production_errors(), [])
        self.assertEqual(len(settings.trusted_networks), 2)

    def test_invalid_trusted_proxy_rejected(self):
        settings = self.build(CLS_BEHIND_PROXY="1",
                              CLS_TRUSTED_PROXIES="10.0.0.0/8,oops")
        errors = settings.production_errors()
        self.assertTrue(any("可信代理" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
